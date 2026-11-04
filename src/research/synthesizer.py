"""Phase 3: Synthese aller Teil-Ergebnisse in einen strukturierten Gesamt- oder Teilbericht."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from src.domain.enums import ResearchDepth
from src.domain.exceptions import JobCancelledError, ResearchBotError
from src.domain.models import (
    ResearchJob,
    ResearchPlan,
    ResearchReport,
    SourceCitation,
    SubTopicFinding,
)
from src.research.gemini_client import ResearchLLMGateway
from src.security.sanitization import sanitize_display_url, sanitize_markdown_links

logger = logging.getLogger(__name__)


class _ExecutiveSummarySchema(BaseModel):
    report_title: str = Field(description="Aussagekräftiger Titel des finalen Forschungsberichts.")
    executive_summary: str = Field(
        description="Prägnante Management-Zusammenfassung (2 bis 3 Absätze) mit den Kernergebnissen."
    )
    key_takeaways: list[str] = Field(
        description="5 bis 8 präzise Kernaussagen mit konkreten Fakten/Zahlen."
    )
    synthesis_conclusion: str = Field(
        description="Übergreifendes Fazit, strategische Einordnung sowie offene Punkte/Ausblick."
    )


def collect_unique_citations(findings: list[SubTopicFinding]) -> list[SourceCitation]:
    """Sammelt alle verifizierten Quellen über sämtliche Teilthemen hinweg ohne Duplikate."""
    unique: list[SourceCitation] = []
    seen_urls: set[str] = set()
    for finding in findings:
        for citation in finding.citations:
            safe_url = sanitize_display_url(citation.url)
            if safe_url == "#" or safe_url in seen_urls:
                continue
            seen_urls.add(safe_url)
            unique.append(
                SourceCitation(
                    url=safe_url,
                    title=citation.title.strip() or safe_url,
                    snippet=citation.snippet.strip(),
                )
            )
    return unique


def _build_sources_section(citations: list[SourceCitation], language: str) -> str:
    """Erstellt ein nummeriertes Markdown-Quellenverzeichnis."""
    heading = "## Verifizierte Web-Quellen" if language == "de" else "## Verified Web Sources"
    if not citations:
        empty_note = (
            "_Keine externen Direkt-URLs extrahiert._"
            if language == "de"
            else "_No external direct URLs extracted._"
        )
        return f"{heading}\n\n{empty_note}"

    lines = [heading, ""]
    for idx, cite in enumerate(citations, start=1):
        label = cite.title.replace("[", "(").replace("]", ")")
        lines.append(f"{idx}. [{label}]({cite.url})")
    return "\n".join(lines)


def _build_partial_notice(plan: ResearchPlan, findings: list[SubTopicFinding], language: str) -> str:
    """Erstellt einen transparenten Hinweisblock, falls aufgrund von Limits ein Teilergebnis vorliegt."""
    total = len(plan.subtopics)
    if total <= 0 or len(findings) >= total:
        return ""
    done_indices = {f.subtopic_index for f in findings}
    skipped = [st.title for st in plan.subtopics if st.index not in done_indices]
    skipped_str = ", ".join(skipped)
    if language == "de":
        return (
            f"> **Hinweis (Teilergebnis):** Aufgrund von API-Ratenlimits oder Zeitbudget wurden "
            f"**{len(findings)} von {total}** geplanten Teilgebieten ausgewertet"
            + (f" (übersprungen: _{skipped_str}_)." if skipped_str else ".")
        )
    return (
        f"> **Note (Partial Result):** Due to API quota or time budget limits, "
        f"**{len(findings)} of {total}** planned subtopics were evaluated"
        + (f" (skipped: _{skipped_str}_)." if skipped_str else ".")
    )


def _build_fallback_overview(
    job: ResearchJob,
    plan: ResearchPlan,
    findings: list[SubTopicFinding],
) -> _ExecutiveSummarySchema:
    """Erstellt bei API-Spitzen sofort eine Zusammenfassung aus den bereits erhobenen Teilbefunden."""
    takeaways: list[str] = []
    for finding in findings:
        for fact in finding.key_facts:
            if fact.strip() and fact.strip() not in takeaways:
                takeaways.append(fact.strip())
            if len(takeaways) >= 8:
                break
        if len(takeaways) >= 8:
            break

    if not takeaways:
        takeaways = [f.title for f in findings[:6]]

    summary = plan.objective_summary.strip() or (
        f"Strukturierte Übersicht zu '{job.topic}' über {len(findings)} untersuchte Teilbereiche."
        if job.language == "de"
        else f"Structured overview of '{job.topic}' across {len(findings)} researched areas."
    )
    conclusion = (
        "Die detaillierten Analysen und Einzelquellen sind in den vorangehenden Fachkapiteln aufgeführt."
        if job.language == "de"
        else "Detailed analyses and verified sources are documented in the preceding chapters."
    )
    return _ExecutiveSummarySchema(
        report_title=plan.normalized_title.strip() or job.topic,
        executive_summary=summary,
        key_takeaways=takeaways,
        synthesis_conclusion=conclusion,
    )


def _assemble_report_markdown(
    overview: _ExecutiveSummarySchema,
    plan: ResearchPlan,
    findings: list[SubTopicFinding],
    citations: list[SourceCitation],
    language: str,
) -> str:
    """Fügt Executive Summary, optionalen Teilergebnis-Hinweis, Kapitel, Fazit & Quellen zusammen."""
    takeaways_heading = "## Zentrale Erkenntnisse" if language == "de" else "## Key Takeaways"
    conclusion_heading = (
        "## Übergreifendes Fazit & Ausblick" if language == "de" else "## Synthesis & Outlook"
    )
    partial_notice = _build_partial_notice(plan, findings, language)

    sections: list[str] = [f"# {overview.report_title.strip()}"]
    if partial_notice:
        sections.extend(["", partial_notice])
    sections.extend(
        [
            "",
            "## Executive Summary",
            "",
            overview.executive_summary.strip(),
            "",
            takeaways_heading,
            "",
        ]
    )
    for point in overview.key_takeaways:
        if point.strip():
            sections.append(f"- {point.strip()}")

    for finding in sorted(findings, key=lambda item: item.subtopic_index):
        sections.extend(
            ["", f"## {finding.subtopic_index}. {finding.title}", "", finding.analysis_markdown.strip()]
        )

    sections.extend(
        [
            "",
            conclusion_heading,
            "",
            overview.synthesis_conclusion.strip(),
            "",
            _build_sources_section(citations, language),
        ]
    )
    return sanitize_markdown_links("\n".join(sections).strip())


def synthesize_final_report(
    gateway: ResearchLLMGateway,
    job: ResearchJob,
    plan: ResearchPlan,
    findings: list[SubTopicFinding],
    baseline_markdown: str,
    model_used: str,
    duration_seconds: int,
    on_action: Callable[[str], None] | None = None,
) -> ResearchReport:
    """Erstellt aus allen Checkpoints den vollständigen oder teilweisen `ResearchReport`."""
    lang_name = "Deutsch" if job.language == "de" else "US English"
    citations = collect_unique_citations(findings)
    digest_blocks = [
        f"### {f.subtopic_index}. {f.title}\nKernfakten: {'; '.join(f.key_facts)}\n{f.analysis_markdown[:2500]}"
        for f in findings
    ]
    baseline_hint = f"\nÜbergreifende Voranalyse:\n{baseline_markdown[:3000]}\n" if baseline_markdown else ""

    prompt = (
        f"Thema des Forschungsauftrags: {job.topic}\nZielsetzung: {plan.objective_summary}\n"
        f"{baseline_hint}\nErgebnisse der {len(findings)} untersuchten Teilgebiete:\n\n"
        + "\n\n".join(digest_blocks)
        + "\n\nErstelle Titel, Executive Summary, prägnante Key Takeaways sowie ein fundiertes Gesamtfazit."
    )

    use_deep = job.depth in {ResearchDepth.DEEP, ResearchDepth.EXHAUSTIVE}
    if on_action is not None:
        on_action("✍️ Formuliere Executive Summary, Kernaussagen & Gesamtfazit...")

    try:
        overview = gateway.generate_structured(
            prompt=prompt,
            system_instruction=(
                f"Du bist Chefredakteur für wissenschaftliche und strategische Dossiers. "
                f"Schreibe klar, sachlich, ohne Werbefloskeln auf {lang_name}."
            ),
            schema_cls=_ExecutiveSummarySchema,
            prefer_deep=use_deep,
        )
    except JobCancelledError:
        raise
    except ResearchBotError as exc:
        logger.warning("Synthese-Aufruf fehlgeschlagen (%s) – erstelle Fallback-Zusammenfassung.", exc)
        if on_action is not None:
            on_action("📋 Erstelle Direkt-Zusammenfassung aus erhobenen Teilbefunden...")
        overview = _build_fallback_overview(job, plan, findings)

    full_markdown = _assemble_report_markdown(overview, plan, findings, citations, job.language)
    return ResearchReport(
        job_id=job.id,
        title=overview.report_title.strip() or plan.normalized_title,
        topic=job.topic,
        depth=job.depth,
        language=job.language,
        executive_summary=sanitize_markdown_links(overview.executive_summary.strip()),
        key_takeaways=[t.strip() for t in overview.key_takeaways if t.strip()],
        content_markdown=full_markdown,
        citations=citations,
        model_used=model_used,
        duration_seconds=max(1, duration_seconds),
        created_at=datetime.now(UTC).isoformat(),
    )
