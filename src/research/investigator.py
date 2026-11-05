"""Phase 2: Konfigurierbare, faktenzentrierte Teilthemen-Untersuchung ohne Prosa-Ballast."""

from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic import BaseModel, Field

from src.config.domain_loader import load_default_domain_config, render_prompt_template
from src.domain.domain_config import DomainConfig
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
        description="Knappe, faktenreiche Stichpunkte zum Teilthema in Markdown mit Quellenlinks."
    )
    key_facts: list[str] = Field(
        description="Die wichtigsten verifizierten Fakten, Zahlen oder Kernaussagen."
    )
    open_questions: list[str] = Field(
        description="Noch offene Detailfragen für eine gezielte Nachrecherche."
    )


def _build_focus_context(job: ResearchJob, cfg: DomainConfig) -> str:
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


def _investigate_quick_single_pass(
    gateway: ResearchLLMGateway,
    job: ResearchJob,
    subtopic: SubTopicPlan,
    profile: DepthProfile,
    cfg: DomainConfig,
    lang_name: str,
    on_heartbeat: Callable[[], None] | None,
) -> SubTopicFinding:
    """Schneller Single-Pass mit Web-Grounding gemäß konfigurierter Prompt-Vorlage."""
    queries_str = ", ".join(subtopic.search_queries) or subtopic.title
    focus_block = _build_focus_context(job, cfg)
    prompt = render_prompt_template(
        cfg.prompts.investigator_quick_user_prompt,
        {
            "topic": job.topic,
            "focus_block": focus_block,
            "subtopic_index": subtopic.index,
            "subtopic_title": subtopic.title,
            "guiding_question": subtopic.guiding_question,
            "search_queries": queries_str,
            "max_subtopic_bullets": profile.max_subtopic_bullets,
        },
    )
    sys_prompt = render_prompt_template(
        cfg.prompts.investigator_quick_system_prompt,
        {
            "language": lang_name,
            "style_rules": cfg.style.global_style_rules,
        },
    )
    grounded = gateway.generate_grounded(
        prompt=prompt,
        system_instruction=sys_prompt,
        use_url_context=False,
        prefer_deep=False,
    )
    if on_heartbeat:
        on_heartbeat()
    return SubTopicFinding(
        subtopic_index=subtopic.index,
        title=subtopic.title,
        analysis_markdown=grounded.text.strip(),
        key_facts=extract_bullet_facts(
            grounded.text,
            subtopic.title,
            max_facts=profile.max_subtopic_bullets,
        ),
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
    on_action: Callable[[str], None] | None = None,
    domain_config: DomainConfig | None = None,
) -> SubTopicFinding:
    """Untersucht ein Teilthema über Web-Grounding, optionalen Volltext-Abruf und Follow-ups."""
    cfg = domain_config or load_default_domain_config()
    lang_name = "Deutsch" if job.language == "de" else "US English"
    if profile.max_urls_per_subtopic <= 0 and profile.followup_rounds <= 0:
        return _investigate_quick_single_pass(
            gateway, job, subtopic, profile, cfg, lang_name, on_heartbeat
        )

    queries_str = ", ".join(subtopic.search_queries) or subtopic.title
    focus_block = _build_focus_context(job, cfg)
    system_instruction = render_prompt_template(
        cfg.prompts.investigator_deep_system_prompt,
        {"language": lang_name, "style_rules": cfg.style.global_style_rules},
    )
    initial_prompt = render_prompt_template(
        cfg.prompts.investigator_deep_user_prompt,
        {
            "topic": job.topic,
            "focus_block": focus_block,
            "subtopic_index": subtopic.index,
            "subtopic_title": subtopic.title,
            "guiding_question": subtopic.guiding_question,
            "search_queries": queries_str,
            "max_subtopic_bullets": profile.max_subtopic_bullets,
        },
    )
    initial_grounded = gateway.generate_grounded(
        prompt=initial_prompt,
        system_instruction=system_instruction,
        use_url_context=False,
        prefer_deep=profile.prefer_deep_model,
    )
    if on_heartbeat:
        on_heartbeat()

    scraped_pages = _fetch_candidate_pages_safely(
        web_fetcher, initial_grounded, profile.max_urls_per_subtopic, on_heartbeat, on_action
    )
    page_citations = [
        SourceCitation(url=p.url, title=p.title, snippet=p.text_content[:200]) for p in scraped_pages
    ]
    all_citations = merge_citations(extract_inline_citations(initial_grounded), page_citations)
    evidence_notes = [initial_grounded.text]

    if on_action is not None:
        on_action("🧩 Verdichte gesammelte Fakten & Primärquellen...")
    structured = _consolidate_evidence_resilient(
        gateway, lang_name, job, subtopic, profile, cfg, evidence_notes, scraped_pages, baseline_excerpt
    )

    max_q = cfg.limits.max_open_questions_per_followup
    for round_idx in range(profile.followup_rounds):
        if not structured.open_questions:
            break
        if on_heartbeat:
            on_heartbeat()
        if on_action is not None:
            on_action(f"🔄 Nachrecherche ({round_idx + 1}/{profile.followup_rounds}) zu offenen Detailfragen...")
        try:
            followup_prompt = render_prompt_template(
                cfg.prompts.investigator_followup_user_prompt,
                {
                    "topic": job.topic,
                    "subtopic_title": subtopic.title,
                    "open_questions": "; ".join(structured.open_questions[:max_q]),
                },
            )
            followup_res = gateway.generate_grounded(
                prompt=followup_prompt,
                system_instruction=system_instruction,
                use_url_context=False,
            )
            evidence_notes.append(followup_res.text)
            all_citations = merge_citations(all_citations, extract_inline_citations(followup_res))
            structured = _consolidate_evidence_resilient(
                gateway, lang_name, job, subtopic, profile, cfg, evidence_notes, scraped_pages, baseline_excerpt
            )
        except JobCancelledError:
            raise
        except ResearchBotError as exc:
            logger.warning("Nachrecherche für '%s' übersprungen (%s).", subtopic.title, exc)
            break

    return SubTopicFinding(
        subtopic_index=subtopic.index,
        title=subtopic.title,
        analysis_markdown=structured.analysis_markdown.strip(),
        key_facts=[f.strip() for f in structured.key_facts[: profile.max_subtopic_bullets] if f.strip()],
        open_questions=[q.strip() for q in structured.open_questions if q.strip()],
        citations=all_citations,
    )


def _fetch_candidate_pages_safely(
    web_fetcher: SafeWebFetcher,
    initial_grounded: object,
    max_pages: int,
    on_heartbeat: Callable[[], None] | None,
    on_action: Callable[[str], None] | None,
) -> list[FetchedWebPage]:
    urls = discover_candidate_urls(initial_grounded)  # type: ignore[arg-type]
    try:
        return web_fetcher.fetch_many_tolerant(
            urls=urls,
            max_pages=max_pages,
            cancel_check=on_heartbeat,
            on_action=on_action,
        )
    except TypeError:
        return web_fetcher.fetch_many_tolerant(
            urls=urls,
            max_pages=max_pages,
            cancel_check=on_heartbeat,
        )


def _consolidate_evidence_resilient(
    gateway: ResearchLLMGateway,
    lang_name: str,
    job: ResearchJob,
    subtopic: SubTopicPlan,
    profile: DepthProfile,
    cfg: DomainConfig,
    evidence_notes: list[str],
    scraped_pages: list[FetchedWebPage],
    baseline_excerpt: str,
) -> _StructuredSubTopicAnalysis:
    """Verdichtet Evidenz kompakt und fällt bei Schema-/API-Fehlern verlustfrei auf Rohtext zurück."""
    max_base = cfg.limits.max_baseline_chars_in_context
    baseline_section = (
        f"\nKontext aus Voranalyse:\n{baseline_excerpt[:max_base]}\n" if baseline_excerpt else ""
    )
    prompt = render_prompt_template(
        cfg.prompts.investigator_consolidation_user_prompt,
        {
            "topic": job.topic,
            "focus_block": _build_focus_context(job, cfg),
            "subtopic_title": subtopic.title,
            "guiding_question": subtopic.guiding_question,
            "baseline_section": baseline_section,
            "evidence_notes": "\n---\n".join(evidence_notes),
            "scraped_pages": format_scraped_pages(
                scraped_pages, max_chars_per_page=cfg.limits.max_chars_per_scraped_page
            ),
            "max_subtopic_bullets": profile.max_subtopic_bullets,
        },
    )
    sys_prompt = render_prompt_template(
        cfg.prompts.investigator_consolidation_system_prompt,
        {"language": lang_name, "style_rules": cfg.style.global_style_rules},
    )
    try:
        return gateway.generate_structured(
            prompt=prompt,
            system_instruction=sys_prompt,
            schema_cls=_StructuredSubTopicAnalysis,
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
        return _StructuredSubTopicAnalysis(
            analysis_markdown=merged_md,
            key_facts=extract_bullet_facts(
                merged_md, subtopic.title, max_facts=profile.max_subtopic_bullets
            ),
            open_questions=[],
        )
