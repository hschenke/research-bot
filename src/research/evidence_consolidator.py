"""Strukturierte Evidenz-Konsolidierung und Nachrecherche-Steuerung für Teilthemen."""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from src.config.domain_loader import render_prompt_template
from src.domain.domain_config import DomainConfig, ModelRoutingConfig
from src.domain.exceptions import JobCancelledError, ResearchBotError
from src.domain.models import DepthProfile, ResearchJob, SourceCitation, SubTopicPlan
from src.research.evidence_parser import extract_bullet_facts, format_scraped_pages, merge_citations
from src.research.gemini_client import ResearchLLMGateway
from src.research.web_fetcher import FetchedWebPage

logger = logging.getLogger(__name__)


class StructuredSubTopicAnalysis(BaseModel):
    """Strukturierte Teilthemen-Verdichtung inklusive offener Nachrecherche-Fragen."""

    analysis_markdown: str = Field(
        description="Knappe, faktenreiche Stichpunkte zum Teilthema in Markdown mit Quellenlinks."
    )
    key_facts: list[str] = Field(
        description="Die wichtigsten verifizierten Fakten, Zahlen oder Kernaussagen."
    )
    open_questions: list[str] = Field(
        description="Noch offene Detailfragen für eine gezielte Nachrecherche."
    )


def build_focus_context(job: ResearchJob, cfg: DomainConfig) -> str:
    """Kombiniert Nutzer-Fokus und Formatvorgaben kompakt für die Teilthemen-Recherche."""
    _fmt_key, fmt_preset = cfg.resolve_effective_format(
        job.topic,
        job.focus_instructions,
        getattr(job, "report_format", "compact"),
    )
    parts: list[str] = []
    if job.focus_instructions.strip():
        parts.append(f"Nutzer-Vorgaben: {job.focus_instructions.strip()}")
    if fmt_preset.omit_wrapper_sections:
        parts.append(f"Zielformat ({fmt_preset.label}): {fmt_preset.instructions}")
    return "\n".join(parts)


def collect_external_sources(
    gateway: ResearchLLMGateway,
    subtopic: SubTopicPlan,
    profile: DepthProfile,
    routing: ModelRoutingConfig | None,
    on_heartbeat: object = None,
) -> list[SourceCitation]:
    """Fragt ergänzende Community-, Wiki- und Web-Quellen aktiv gemäß Recherche-Tiefe ab."""
    if routing is not None and not routing.enable_cross_verification:
        return []
    search_fn = getattr(gateway, "search_external_sources", None)
    if not callable(search_fn):
        return []

    query_limit = 2 if (profile.followup_rounds >= 2 or profile.prefer_deep_model) else 1
    queries = [q.strip() for q in subtopic.search_queries if q.strip()][:query_limit] or [subtopic.title]
    collected: list[SourceCitation] = []
    for query in queries:
        raw = search_fn(query)
        if isinstance(raw, list) and raw:
            collected = merge_citations(collected, raw)
        if callable(on_heartbeat):
            on_heartbeat()
    return collected


def format_external_evidence_note(citations: list[SourceCitation]) -> str:
    """Formatiert gefundene Community-, Wiki- und Web-Treffer kompakt für die Konsolidierung."""
    if not citations:
        return ""
    lines = ["Ergänzende Web- & Community-Quellen:"]
    for citation in citations[:8]:
        snippet = f" – {citation.snippet[:240]}" if citation.snippet.strip() else ""
        lines.append(f"- [{citation.title or citation.url}]({citation.url}){snippet}")
    return "\n".join(lines)


def build_followup_guidance(remaining_followups: int, max_questions: int) -> str:
    """Erzeugt klare Anweisungen für das Befüllen von `open_questions` vor bzw. nach Nachrecherchen."""
    if remaining_followups > 0:
        return (
            f"WICHTIG FÜR NACHRECHERCHE: Es stehen noch {remaining_followups} Nachrecherche-Runde(n) "
            f"zur Verfügung. Prüfe kritisch auf fehlende Kennzahlen, widersprüchliche Aussagen oder "
            f"unbelegte Behauptungen und formuliere in `open_questions` 1 bis maximal {max_questions} "
            "präzise, eigenständig recherchierbare Nachfragen."
        )
    return "Dies ist die abschließende Konsolidierung: Liefere in `open_questions` eine leere Liste (`[]`)."


def consolidate_evidence_resilient(
    gateway: ResearchLLMGateway,
    lang_name: str,
    job: ResearchJob,
    subtopic: SubTopicPlan,
    profile: DepthProfile,
    cfg: DomainConfig,
    evidence_notes: list[str],
    scraped_pages: list[FetchedWebPage],
    baseline_excerpt: str,
    remaining_followups: int = 0,
) -> StructuredSubTopicAnalysis:
    """Verdichtet Evidenz kompakt und fällt bei Schema-/API-Fehlern verlustfrei auf Rohtext zurück."""
    max_base = cfg.limits.max_baseline_chars_in_context
    baseline_section = (
        f"\nKontext aus Voranalyse:\n{baseline_excerpt[:max_base]}\n" if baseline_excerpt else ""
    )
    guidance = build_followup_guidance(
        remaining_followups=remaining_followups,
        max_questions=cfg.limits.max_open_questions_per_followup,
    )
    tpl = cfg.prompts.investigator_consolidation_user_prompt
    prompt = render_prompt_template(
        tpl,
        {
            "topic": job.topic,
            "focus_block": build_focus_context(job, cfg),
            "subtopic_title": subtopic.title,
            "guiding_question": subtopic.guiding_question,
            "baseline_section": baseline_section,
            "evidence_notes": "\n---\n".join(evidence_notes),
            "scraped_pages": format_scraped_pages(
                scraped_pages, max_chars_per_page=cfg.limits.max_chars_per_scraped_page
            ),
            "max_subtopic_bullets": profile.max_subtopic_bullets,
            "followup_guidance": guidance,
        },
    )
    if "{followup_guidance}" not in tpl and guidance:
        prompt = f"{prompt}\n\n{guidance}"

    sys_prompt = render_prompt_template(
        cfg.prompts.investigator_consolidation_system_prompt,
        {"language": lang_name, "style_rules": cfg.style.global_style_rules},
    )
    try:
        return gateway.generate_structured(
            prompt=prompt,
            system_instruction=sys_prompt,
            schema_cls=StructuredSubTopicAnalysis,
            prefer_deep=profile.prefer_deep_model,
        )
    except JobCancelledError:
        raise
    except ResearchBotError as exc:
        logger.warning(
            "Struktur-Konsolidierung für '%s' fehlgeschlagen (%s) – nutze Grounding-Notizen.",
            subtopic.title,
            exc,
        )
        merged_md = "\n\n".join(note.strip() for note in evidence_notes if note.strip())
        return StructuredSubTopicAnalysis(
            analysis_markdown=merged_md,
            key_facts=extract_bullet_facts(
                merged_md, subtopic.title, max_facts=profile.max_subtopic_bullets
            ),
            open_questions=[],
        )
