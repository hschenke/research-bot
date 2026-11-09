"""Phase 3: Konfigurierbare, redundanzfreie Berichts-Synthese (inkl. reinem Listen-Modus)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from src.config.domain_loader import load_default_domain_config, render_prompt_template
from src.domain.domain_config import DomainConfig
from src.domain.exceptions import JobCancelledError, ResearchBotError
from src.domain.models import (
    DepthProfile,
    ResearchJob,
    ResearchPlan,
    ResearchReport,
    SubTopicFinding,
)
from src.research.gemini_client import ResearchLLMGateway
from src.research.report_formatter import (
    assemble_report_markdown,
    build_fallback_body,
    collect_unique_citations,
)
from src.security.sanitization import sanitize_markdown_links

logger = logging.getLogger(__name__)

__all__ = ["collect_unique_citations", "synthesize_final_report"]


class _SynthesizedReportSchema(BaseModel):
    report_title: str = Field(description="Treffender, kurzer Titel des Berichts.")
    executive_summary: str = Field(
        description="1 bis maximal 2 knackige Sätze Kernaussage (für Archiv-Vorschau)."
    )
    key_takeaways: list[str] = Field(
        default_factory=list,
        description="Prägnante Fakten-Stichpunkte.",
    )
    body_markdown: str = Field(
        default="",
        description=(
            "Fertiger, redundanzfreier Hauptinhalt in Markdown (ohne H1-Haupttitel und ohne "
            "abschließendes Quellenverzeichnis). Setzt Listen- und Kürzewünsche strikt um."
        ),
    )


def _build_fallback_synthesis(
    job: ResearchJob,
    plan: ResearchPlan,
    findings: list[SubTopicFinding],
    max_takeaways: int,
    omit_wrapper_sections: bool,
) -> _SynthesizedReportSchema:
    """Erstellt bei API-Spitzen eine kompakte Direkt-Synthese ohne unnötigen Prosa-Überbau."""
    takeaways: list[str] = []
    for finding in findings:
        for fact in finding.key_facts:
            cleaned = fact.strip()
            if cleaned and cleaned not in takeaways:
                takeaways.append(cleaned)
            if len(takeaways) >= max_takeaways:
                break
        if len(takeaways) >= max_takeaways:
            break

    if not takeaways:
        takeaways = [f.title for f in findings[:max_takeaways]]

    summary = plan.objective_summary.strip() or job.topic
    body = build_fallback_body(findings, takeaways, omit_wrapper_sections)
    return _SynthesizedReportSchema(
        report_title=plan.normalized_title.strip() or job.topic,
        executive_summary=summary,
        key_takeaways=takeaways,
        body_markdown=body,
    )


def _build_synthesis_prompts(
    job: ResearchJob,
    plan: ResearchPlan,
    findings: list[SubTopicFinding],
    baseline_markdown: str,
    active_profile: DepthProfile,
    cfg: DomainConfig,
) -> tuple[str, str, bool]:
    """Rendert System- und User-Prompt für die Berichts-Synthese aus `DomainConfig`."""
    _fmt_key, fmt_preset = cfg.resolve_effective_format(
        job.topic,
        job.focus_instructions,
        getattr(job, "report_format", "compact"),
    )
    lang_name = "Deutsch" if job.language == "de" else "US English"
    max_f_chars = cfg.limits.max_finding_chars_in_synthesis
    digest_blocks = [
        f"### {f.subtopic_index}. {f.title}\nKernfakten: {'; '.join(f.key_facts)}\n{f.analysis_markdown[:max_f_chars]}"
        for f in sorted(findings, key=lambda item: item.subtopic_index)
    ]
    max_b_chars = cfg.limits.max_baseline_chars_in_context
    baseline_section = (
        f"\nVoranalyse:\n{baseline_markdown[:max_b_chars]}\n" if baseline_markdown else ""
    )
    focus_block = (
        f"Spezifische Fokus- & Formatvorgaben des Nutzers:\n{job.focus_instructions.strip()}"
        if job.focus_instructions.strip()
        else "Keine zusätzlichen Freitext-Vorgaben."
    )
    if cfg.is_top_x_query(job.topic, job.focus_instructions):
        focus_block += (
            "\n\n🏆 TOP-X / RANKING-VORGABE (SEHR WICHTIG):\n"
            "Dies ist eine Top-X / Ranking-Anfrage! Erstelle im Hauptteil (`body_markdown`) als "
            "zentrales Kernelement zwingend eine vollständige, saubere Markdown-Tabelle aller Top-X Einträge:\n"
            "- Spalten: `| Rang | Name / Tool | Kategorie | Kernmerkmale & Kennzahlen | Preis / Modell | Quelle / Link |`\n"
            "- Jeder Eintrag MUSS eine eigene durchnummerierte Zeile (1, 2, 3...) erhalten.\n"
            "- Die Tabelle wird in der Web-App als interaktive, sortierbare Datentabelle bereitgestellt.\n"
            "- Ergänze darunter bei Bedarf kurze Stichpunkte mit Details zu den Platzierungen."
        )

    sys_prompt = render_prompt_template(
        cfg.prompts.synthesizer_system_prompt,
        {
            "language": lang_name,
            "style_rules": cfg.style.global_style_rules,
            "format_override_rules": cfg.style.format_override_rules,
        },
    )
    user_prompt = render_prompt_template(
        cfg.prompts.synthesizer_user_prompt,
        {
            "topic": job.topic,
            "objective_summary": plan.objective_summary,
            "focus_block": focus_block,
            "format_label": fmt_preset.label,
            "format_instructions": fmt_preset.instructions,
            "target_word_range": active_profile.target_word_range,
            "baseline_section": baseline_section,
            "findings_count": len(findings),
            "findings_digest": "\n\n".join(digest_blocks),
            "max_takeaways": cfg.limits.max_takeaways_count,
        },
    )
    return sys_prompt, user_prompt, fmt_preset.omit_wrapper_sections


def synthesize_final_report(
    gateway: ResearchLLMGateway,
    job: ResearchJob,
    plan: ResearchPlan,
    findings: list[SubTopicFinding],
    baseline_markdown: str,
    model_used: str,
    duration_seconds: int,
    on_action: Callable[[str], None] | None = None,
    profile: DepthProfile | None = None,
    domain_config: DomainConfig | None = None,
    warnings: list[str] | None = None,
) -> ResearchReport:
    """Synthetisiert alle Teilbefunde in einen knackigen, formatgetreuen `ResearchReport`."""
    cfg = domain_config or load_default_domain_config()
    active_profile = profile or cfg.get_depth_profile(job.depth)
    citations = collect_unique_citations(findings)
    sys_prompt, user_prompt, omit_wrapper = _build_synthesis_prompts(
        job, plan, findings, baseline_markdown, active_profile, cfg
    )

    if on_action is not None:
        on_action("✍️ Verdichte Ergebnisse in einen kompakten, präzisen Bericht...")

    try:
        synthesized = gateway.generate_structured(
            prompt=user_prompt,
            system_instruction=sys_prompt,
            schema_cls=_SynthesizedReportSchema,
            prefer_deep=active_profile.prefer_deep_model,
        )
    except JobCancelledError:
        raise
    except ResearchBotError as exc:
        logger.warning("Synthese-Aufruf fehlgeschlagen (%s) – nutze kompakten Fallback.", exc)
        if on_action is not None:
            on_action("📋 Erstelle kompakte Direkt-Zusammenfassung aus Teilbefunden...")
        synthesized = _build_fallback_synthesis(
            job=job,
            plan=plan,
            findings=findings,
            max_takeaways=cfg.limits.max_takeaways_count,
            omit_wrapper_sections=omit_wrapper,
        )

    full_markdown = assemble_report_markdown(
        report_title=synthesized.report_title.strip() or plan.normalized_title,
        body_markdown=synthesized.body_markdown,
        key_takeaways=synthesized.key_takeaways,
        plan=plan,
        findings=findings,
        citations=citations,
        language=job.language,
        cfg=cfg,
        omit_wrapper_sections=omit_wrapper,
        warnings=warnings,
        job=job,
    )
    max_tk = cfg.limits.max_takeaways_count
    return ResearchReport(
        job_id=job.id,
        title=synthesized.report_title.strip() or plan.normalized_title,
        topic=job.topic,
        depth=job.depth,
        language=job.language,
        executive_summary=sanitize_markdown_links(synthesized.executive_summary.strip()),
        key_takeaways=[t.strip() for t in synthesized.key_takeaways[:max_tk] if t.strip()],
        content_markdown=full_markdown,
        citations=citations,
        model_used=model_used,
        duration_seconds=max(1, duration_seconds),
        created_at=datetime.now(UTC).isoformat(),
    )
