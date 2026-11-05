"""Phase 1: Konfigurierbare Themen-Zerlegung in schlanke Teilfragen inkl. Direktplan-Fallback."""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from src.config.domain_loader import load_default_domain_config, render_prompt_template
from src.domain.domain_config import DomainConfig
from src.domain.exceptions import JobCancelledError, ResearchBotError
from src.domain.models import DepthProfile, ResearchJob, ResearchPlan, SubTopicPlan
from src.research.gemini_client import ResearchLLMGateway

logger = logging.getLogger(__name__)


class _DraftSubTopic(BaseModel):
    title: str = Field(description="Prägnanter Titel des Teilgebiets.")
    guiding_question: str = Field(description="Konkrete Leitfrage für dieses Teilgebiet.")
    search_queries: list[str] = Field(
        description="Präzise Web-Suchanfragen zur Beantwortung der Leitfrage."
    )


class _DraftResearchPlan(BaseModel):
    normalized_title: str = Field(description="Knackiger Gesamttitel des Berichts.")
    objective_summary: str = Field(description="Kurze Zielbeschreibung in 1 bis 2 Sätzen.")
    subtopics: list[_DraftSubTopic] = Field(description="Geordnete, überschneidungsfreie Teilthemen.")


def _language_label(lang_code: str) -> str:
    return "Deutsch" if lang_code.lower() == "de" else "US English"


def resolve_max_subtopics(
    job: ResearchJob,
    profile: DepthProfile,
    domain_config: DomainConfig,
) -> int:
    """Begrenzt die Teilthemen-Anzahl automatisch, wenn z. B. eine knappe Liste gewünscht ist."""
    _fmt_key, fmt_preset = domain_config.resolve_effective_format(
        job.topic,
        job.focus_instructions,
        getattr(job, "report_format", "compact"),
    )
    if fmt_preset.max_subtopics_cap > 0:
        return max(1, min(profile.subtopic_count, fmt_preset.max_subtopics_cap))
    return max(1, profile.subtopic_count)


def _build_deterministic_fallback_plan(
    job: ResearchJob,
    max_subtopics: int,
    domain_config: DomainConfig,
) -> ResearchPlan:
    """Erstellt bei erschöpftem KI-Kontingent einen schlanken Notfall-Plan aus der Konfiguration."""
    topic = job.topic.strip()
    is_de = job.language.lower() == "de"
    templates = (
        domain_config.fallback_plan_templates.de
        if is_de
        else domain_config.fallback_plan_templates.en
    )
    count = max(1, min(max_subtopics, len(templates)))
    subtopics = [
        SubTopicPlan(
            index=idx,
            title=render_prompt_template(tpl.title, {"topic": topic}),
            guiding_question=render_prompt_template(tpl.guiding_question, {"topic": topic}),
            search_queries=[
                render_prompt_template(q, {"topic": topic}) for q in tpl.search_queries
            ],
        )
        for idx, tpl in enumerate(templates[:count], start=1)
    ]
    summary = (
        f"Kompakte Untersuchung zu '{topic}' ({count} Fokusbereich(e))."
        if is_de
        else f"Concise investigation of '{topic}' ({count} focus area(s))."
    )
    return ResearchPlan(normalized_title=topic, objective_summary=summary, subtopics=subtopics)


def create_research_plan(
    gateway: ResearchLLMGateway,
    job: ResearchJob,
    profile: DepthProfile,
    domain_config: DomainConfig | None = None,
) -> ResearchPlan:
    """Erzeugt einen auf Recherche-Tiefe, Stilregeln und Zielformat abgestimmten `ResearchPlan`."""
    cfg = domain_config or load_default_domain_config()
    lang_name = _language_label(job.language)
    _fmt_key, fmt_preset = cfg.resolve_effective_format(
        job.topic,
        job.focus_instructions,
        getattr(job, "report_format", "compact"),
    )
    max_subtopics = resolve_max_subtopics(job, profile, cfg)
    max_queries = cfg.limits.max_search_queries_per_subtopic

    focus_block = (
        f"\nSpezifische Fokus- & Formatvorgaben des Nutzers:\n{job.focus_instructions}\n"
        if job.focus_instructions.strip()
        else ""
    )

    system_instruction = render_prompt_template(
        cfg.prompts.planner_system_prompt,
        {
            "language": lang_name,
            "style_rules": cfg.style.global_style_rules,
            "format_override_rules": cfg.style.format_override_rules,
        },
    )
    prompt = render_prompt_template(
        cfg.prompts.planner_user_prompt,
        {
            "topic": job.topic,
            "focus_block": focus_block,
            "format_label": fmt_preset.label,
            "format_instructions": fmt_preset.instructions,
            "max_subtopics": max_subtopics,
            "max_search_queries": max_queries,
            "target_word_range": profile.target_word_range,
        },
    )

    try:
        draft = gateway.generate_structured(
            prompt=prompt,
            system_instruction=system_instruction,
            schema_cls=_DraftResearchPlan,
            prefer_deep=profile.prefer_deep_model,
        )
    except JobCancelledError:
        raise
    except ResearchBotError as exc:
        logger.warning("KI-Planung fehlgeschlagen (%s) – nutze konfigurierten Fallback-Plan.", exc)
        return _build_deterministic_fallback_plan(job, max_subtopics, cfg)

    limited_drafts = draft.subtopics[:max_subtopics]
    subtopics = [
        SubTopicPlan(
            index=idx,
            title=item.title.strip(),
            guiding_question=item.guiding_question.strip(),
            search_queries=[q.strip() for q in item.search_queries[:max_queries] if q.strip()],
        )
        for idx, item in enumerate(limited_drafts, start=1)
    ]

    if not subtopics:
        return _build_deterministic_fallback_plan(job, max_subtopics, cfg)

    return ResearchPlan(
        normalized_title=draft.normalized_title.strip() or job.topic,
        objective_summary=draft.objective_summary.strip(),
        subtopics=subtopics,
    )
