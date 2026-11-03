"""Phase 2: Mehrstufige Untersuchung einzelner Teilthemen inkl. Web-Scraping und Follow-ups."""

from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import BaseModel, Field

from src.domain.models import (
    DepthProfile,
    ResearchJob,
    SourceCitation,
    SubTopicFinding,
    SubTopicPlan,
)
from src.research.gemini_client import GroundedGeneration, ResearchLLMGateway
from src.research.web_fetcher import FetchedWebPage, SafeWebFetcher
from src.security.sanitization import sanitize_display_url

_INLINE_URL_PATTERN = re.compile(r"https?://[^\s\)\]>\"']+")


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


def _merge_citations(*citation_lists: list[SourceCitation]) -> list[SourceCitation]:
    """Vereint und dedupliziert Quellenlisten anhand der bereinigten URL."""
    merged: list[SourceCitation] = []
    seen: set[str] = set()
    for items in citation_lists:
        for item in items:
            safe_url = sanitize_display_url(item.url)
            if safe_url == "#" or safe_url in seen:
                continue
            seen.add(safe_url)
            merged.append(
                SourceCitation(
                    url=safe_url,
                    title=item.title.strip(),
                    snippet=item.snippet.strip(),
                )
            )
    return merged


def _discover_candidate_urls(grounded: GroundedGeneration) -> list[str]:
    """Sammelt URLs aus Grounding-Annotationen sowie direkt genannten Markdown-Links."""
    urls = [c.url for c in grounded.citations]
    for match in _INLINE_URL_PATTERN.findall(grounded.text):
        safe = sanitize_display_url(match)
        if safe != "#" and safe not in urls:
            urls.append(safe)
    return urls


def _format_scraped_pages(pages: list[FetchedWebPage]) -> str:
    """Bereitet extrahierte Volltexte geladener Webseiten für die Tiefenanalyse auf."""
    if not pages:
        return "Keine zusätzlichen Volltext-Seiten geladen."
    blocks = [
        f"### Volltext-Quelle: {page.title or page.url}\nURL: {page.url}\nAuszug:\n{page.text_content[:4500]}"
        for page in pages
    ]
    return "\n\n".join(blocks)


def investigate_subtopic(
    gateway: ResearchLLMGateway,
    web_fetcher: SafeWebFetcher,
    job: ResearchJob,
    subtopic: SubTopicPlan,
    profile: DepthProfile,
    baseline_excerpt: str = "",
    on_heartbeat: Callable[[], None] | None = None,
) -> SubTopicFinding:
    """Untersucht ein Teilthema über Web-Grounding, Volltext-Abruf und optionale Nachschärfungsrunden."""
    lang_name = "Deutsch" if job.language == "de" else "English"
    queries_str = ", ".join(subtopic.search_queries) or subtopic.title

    search_prompt = (
        f"Gesamtthema: {job.topic}\n"
        f"Teilthema {subtopic.index}: {subtopic.title}\n"
        f"Leitfrage: {subtopic.guiding_question}\n"
        f"Empfohlene Suchbegriffe: {queries_str}\n\n"
        "Recherchiere aktuelle Fakten, konkrete Zahlen, Studien, Akteure und Primärquellen im Web. "
        "Nenne zu wichtigen Aussagen explizit die zugehörigen Quell-URLs."
    )
    system_instruction = (
        f"Du bist ein präziser Fakten-Analyst. Antworte auf {lang_name}. "
        "Nutze Web-Suche aktiv, vermeide Spekulationen und belege Aussagen mit konkreten Quellen."
    )

    initial_grounded = gateway.generate_grounded(
        prompt=search_prompt,
        system_instruction=system_instruction,
        use_url_context=True,
        prefer_deep=profile.subtopic_count >= 10,
    )
    if on_heartbeat:
        on_heartbeat()

    candidate_urls = _discover_candidate_urls(initial_grounded)
    scraped_pages = web_fetcher.fetch_many_tolerant(
        urls=candidate_urls,
        max_pages=profile.max_urls_per_subtopic,
    )
    page_citations = [
        SourceCitation(url=p.url, title=p.title, snippet=p.text_content[:200])
        for p in scraped_pages
    ]
    all_citations = _merge_citations(initial_grounded.citations, page_citations)
    evidence_notes = [initial_grounded.text]

    structured = _consolidate_evidence(
        gateway=gateway,
        lang_name=lang_name,
        job=job,
        subtopic=subtopic,
        evidence_notes=evidence_notes,
        scraped_pages=scraped_pages,
        baseline_excerpt=baseline_excerpt,
    )

    # Optionale Follow-up-Runden zur Schließung offener Wissenslücken (Standard, Deep, Exhaustive)
    for _round_idx in range(profile.followup_rounds):
        if not structured.open_questions:
            break
        if on_heartbeat:
            on_heartbeat()
        followup_question = "; ".join(structured.open_questions[:2])
        followup_res = gateway.generate_grounded(
            prompt=(
                f"Schließe gezielt folgende offene Fragen zum Thema '{job.topic} – {subtopic.title}':\n"
                f"{followup_question}\nLiefere belastbare Fakten und konkrete URLs."
            ),
            system_instruction=system_instruction,
            use_url_context=True,
        )
        evidence_notes.append(followup_res.text)
        all_citations = _merge_citations(all_citations, followup_res.citations)
        structured = _consolidate_evidence(
            gateway=gateway,
            lang_name=lang_name,
            job=job,
            subtopic=subtopic,
            evidence_notes=evidence_notes,
            scraped_pages=scraped_pages,
            baseline_excerpt=baseline_excerpt,
        )

    return SubTopicFinding(
        subtopic_index=subtopic.index,
        title=subtopic.title,
        analysis_markdown=structured.analysis_markdown.strip(),
        key_facts=[f.strip() for f in structured.key_facts if f.strip()],
        open_questions=[q.strip() for q in structured.open_questions if q.strip()],
        citations=all_citations,
    )


def _consolidate_evidence(
    gateway: ResearchLLMGateway,
    lang_name: str,
    job: ResearchJob,
    subtopic: SubTopicPlan,
    evidence_notes: list[str],
    scraped_pages: list[FetchedWebPage],
    baseline_excerpt: str,
) -> _StructuredSubTopicAnalysis:
    """Verdichtet Suchergebnisse und gescrapte Volltexte zu einer strukturierten Teil-Analyse."""
    baseline_section = (
        f"\nKontext aus übergreifender Baseline-Recherche:\n{baseline_excerpt[:3000]}\n"
        if baseline_excerpt
        else ""
    )
    consolidation_prompt = (
        f"Thema: {job.topic}\nTeilgebiet: {subtopic.title}\nLeitfrage: {subtopic.guiding_question}\n"
        f"{baseline_section}\n"
        f"Gesammelte Web-Recherche-Notizen:\n{'---'.join(evidence_notes)}\n\n"
        f"Extrahierte Volltexte von Primärseiten:\n{_format_scraped_pages(scraped_pages)}\n\n"
        "Erstelle eine tiefgehende, klar gegliederte Ausarbeitung dieses Teilgebiets inklusive konkreter Daten "
        "und verlinkter Quellen im Format `[Quellentitel](URL)`."
    )
    return gateway.generate_structured(
        prompt=consolidation_prompt,
        system_instruction=f"Antworte präzise, analytisch und faktenbasiert auf {lang_name}.",
        schema_cls=_StructuredSubTopicAnalysis,
    )
