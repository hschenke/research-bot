"""Phase 2: Mehrstufige Untersuchung einzelner Teilthemen inkl. Turbo-Pfad für Schnellüberblick."""

from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic import BaseModel, Field

from src.domain.exceptions import JobCancelledError, ResearchBotError
from src.domain.models import (
    DepthProfile,
    ResearchJob,
    SourceCitation,
    SubTopicFinding,
    SubTopicPlan,
)
from src.research.evidence_parser import (
    discover_candidate_urls,
    extract_bullet_facts,
    extract_inline_citations,
    format_scraped_pages,
    merge_citations,
)
from src.research.gemini_client import ResearchLLMGateway
from src.research.web_fetcher import FetchedWebPage, SafeWebFetcher

logger = logging.getLogger(__name__)


class _StructuredSubTopicAnalysis(BaseModel):
    analysis_markdown: str = Field(
        description="Detaillierte, faktenreiche Ausarbeitung des Teilthemas in Markdown mit Quellenverweisen."
    )
    key_facts: list[str] = Field(
        description="Die 3 bis 6 wichtigsten verifizierten Fakten, Zahlen oder Kernaussagen."
    )
    open_questions: list[str] = Field(
        description="Bis zu 2 noch offene Detailfragen oder Datenlücken für eine Nachrecherche."
    )


def _investigate_quick_single_pass(
    gateway: ResearchLLMGateway,
    job: ResearchJob,
    subtopic: SubTopicPlan,
    lang_name: str,
    on_heartbeat: Callable[[], None] | None,
) -> SubTopicFinding:
    """Schneller Single-Pass mit Web-Grounding für das Profil `Schnellüberblick`."""
    queries_str = ", ".join(subtopic.search_queries) or subtopic.title
    prompt = (
        f"Gesamtthema: {job.topic}\nTeilgebiet {subtopic.index}: {subtopic.title}\n"
        f"Leitfrage: {subtopic.guiding_question}\nSuchfokus: {queries_str}\n\n"
        "Liefere eine kompakte, faktenreiche Ausarbeitung (2–3 prägnante Absätze plus 3–5 "
        "Stichpunkte mit konkreten Zahlen/Fakten) in sauberem Markdown inklusive verlinkter "
        "Quellen `[Quellentitel](URL)`."
    )
    grounded = gateway.generate_grounded(
        prompt=prompt,
        system_instruction=(
            f"Du bist ein schneller Fakten-Analyst. Antworte präzise auf {lang_name} "
            "mit aktuellen Web-Quellen ohne Fülltext."
        ),
        use_url_context=False,
        prefer_deep=False,
    )
    if on_heartbeat:
        on_heartbeat()
    return SubTopicFinding(
        subtopic_index=subtopic.index,
        title=subtopic.title,
        analysis_markdown=grounded.text.strip(),
        key_facts=extract_bullet_facts(grounded.text, subtopic.title),
        open_questions=[],
        citations=extract_inline_citations(grounded),
    )


def investigate_subtopic(
    gateway: ResearchLLMGateway,
    web_fetcher: SafeWebFetcher,
    job: ResearchJob,
    subtopic: SubTopicPlan,
    profile: DepthProfile,
    baseline_excerpt: str = "",
    on_heartbeat: Callable[[], None] | None = None,
) -> SubTopicFinding:
    """Untersucht ein Teilthema über Web-Grounding, optionalen Volltext-Abruf und Follow-ups."""
    lang_name = "Deutsch" if job.language == "de" else "English"
    if profile.max_urls_per_subtopic <= 0 and profile.followup_rounds <= 0:
        return _investigate_quick_single_pass(gateway, job, subtopic, lang_name, on_heartbeat)

    queries_str = ", ".join(subtopic.search_queries) or subtopic.title
    system_instruction = (
        f"Du bist ein präziser Fakten-Analyst. Antworte auf {lang_name}. "
        "Nutze Web-Suche aktiv, vermeide Spekulationen und belege Aussagen mit konkreten Quellen."
    )
    initial_grounded = gateway.generate_grounded(
        prompt=(
            f"Gesamtthema: {job.topic}\nTeilthema {subtopic.index}: {subtopic.title}\n"
            f"Leitfrage: {subtopic.guiding_question}\nEmpfohlene Suchbegriffe: {queries_str}\n\n"
            "Recherchiere aktuelle Fakten, konkrete Zahlen, Studien und Primärquellen im Web. "
            "Nenne zu wichtigen Aussagen explizit die zugehörigen Quell-URLs."
        ),
        system_instruction=system_instruction,
        use_url_context=False,
        prefer_deep=profile.subtopic_count >= 10,
    )
    if on_heartbeat:
        on_heartbeat()

    scraped_pages = web_fetcher.fetch_many_tolerant(
        urls=discover_candidate_urls(initial_grounded),
        max_pages=profile.max_urls_per_subtopic,
        cancel_check=on_heartbeat,
    )
    page_citations = [
        SourceCitation(url=p.url, title=p.title, snippet=p.text_content[:200]) for p in scraped_pages
    ]
    all_citations = merge_citations(extract_inline_citations(initial_grounded), page_citations)
    evidence_notes = [initial_grounded.text]

    structured = _consolidate_evidence_resilient(
        gateway, lang_name, job, subtopic, evidence_notes, scraped_pages, baseline_excerpt
    )

    for _round_idx in range(profile.followup_rounds):
        if not structured.open_questions:
            break
        if on_heartbeat:
            on_heartbeat()
        followup_res = gateway.generate_grounded(
            prompt=(
                f"Schließe gezielt folgende offene Fragen zum Thema '{job.topic} – {subtopic.title}':\n"
                f"{'; '.join(structured.open_questions[:2])}\nLiefere belastbare Fakten und konkrete URLs."
            ),
            system_instruction=system_instruction,
            use_url_context=False,
        )
        evidence_notes.append(followup_res.text)
        all_citations = merge_citations(all_citations, extract_inline_citations(followup_res))
        structured = _consolidate_evidence_resilient(
            gateway, lang_name, job, subtopic, evidence_notes, scraped_pages, baseline_excerpt
        )

    return SubTopicFinding(
        subtopic_index=subtopic.index,
        title=subtopic.title,
        analysis_markdown=structured.analysis_markdown.strip(),
        key_facts=[f.strip() for f in structured.key_facts if f.strip()],
        open_questions=[q.strip() for q in structured.open_questions if q.strip()],
        citations=all_citations,
    )


def _consolidate_evidence_resilient(
    gateway: ResearchLLMGateway,
    lang_name: str,
    job: ResearchJob,
    subtopic: SubTopicPlan,
    evidence_notes: list[str],
    scraped_pages: list[FetchedWebPage],
    baseline_excerpt: str,
) -> _StructuredSubTopicAnalysis:
    """Verdichtet Evidenz strukturiert und fällt bei Schema-/API-Fehlern verlustfrei auf Rohtext zurück."""
    baseline_section = (
        f"\nKontext aus übergreifender Baseline-Recherche:\n{baseline_excerpt[:3000]}\n"
        if baseline_excerpt
        else ""
    )
    prompt = (
        f"Thema: {job.topic}\nTeilgebiet: {subtopic.title}\nLeitfrage: {subtopic.guiding_question}\n"
        f"{baseline_section}\nGesammelte Web-Recherche-Notizen:\n{'---'.join(evidence_notes)}\n\n"
        f"Extrahierte Volltexte von Primärseiten:\n{format_scraped_pages(scraped_pages)}\n\n"
        "Erstelle eine tiefgehende, klar gegliederte Ausarbeitung dieses Teilgebiets inklusive konkreter Daten "
        "und verlinkter Quellen im Format `[Quellentitel](URL)`."
    )
    try:
        return gateway.generate_structured(
            prompt=prompt,
            system_instruction=f"Antworte präzise, analytisch und faktenbasiert auf {lang_name}.",
            schema_cls=_StructuredSubTopicAnalysis,
        )
    except JobCancelledError:
        raise
    except ResearchBotError as exc:
        logger.warning(
            "Struktur-Konsolidierung für '%s' fehlgeschlagen (%s) – nutze direkte Grounding-Ausarbeitung.",
            subtopic.title,
            exc,
        )
        merged_md = "\n\n".join(note.strip() for note in evidence_notes if note.strip())
        return _StructuredSubTopicAnalysis(
            analysis_markdown=merged_md,
            key_facts=extract_bullet_facts(merged_md, subtopic.title),
            open_questions=[],
        )
