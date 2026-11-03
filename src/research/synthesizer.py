"""Phase 3: Synthese aller Teil-Ergebnisse in einen strukturierten, quellenbelegten Gesamtbericht."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from src.domain.models import (
    ResearchJob,
    ResearchPlan,
    ResearchReport,
    SourceCitation,
    SubTopicFinding,
)
from src.research.gemini_client import ResearchLLMGateway
from src.security.sanitization import sanitize_display_url, sanitize_markdown_links


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


def _assemble_report_markdown(
    overview: _ExecutiveSummarySchema,
    findings: list[SubTopicFinding],
    citations: list[SourceCitation],
    language: str,
) -> str:
    """Fügt Executive Summary, alle Detailkapitel, Fazit und Quellenverzeichnis sauber zusammen."""
    summary_heading = "## Executive Summary"
    takeaways_heading = "## Zentrale Erkenntnisse" if language == "de" else "## Key Takeaways"
    conclusion_heading = (
        "## Übergreifendes Fazit & Ausblick"
        if language == "de"
        else "## Synthesis & Outlook"
    )

    sections: list[str] = [
        f"# {overview.report_title.strip()}",
        "",
        summary_heading,
        "",
        overview.executive_summary.strip(),
        "",
        takeaways_heading,
        "",
    ]
    for point in overview.key_takeaways:
        if point.strip():
            sections.append(f"- {point.strip()}")

    for finding in sorted(findings, key=lambda item: item.subtopic_index):
        sections.extend(
            [
                "",
                f"## {finding.subtopic_index}. {finding.title}",
                "",
                finding.analysis_markdown.strip(),
            ]
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

    raw_markdown = "\n".join(sections).strip()
    return sanitize_markdown_links(raw_markdown)


def synthesize_final_report(
    gateway: ResearchLLMGateway,
    job: ResearchJob,
    plan: ResearchPlan,
    findings: list[SubTopicFinding],
    baseline_markdown: str,
    model_used: str,
    duration_seconds: int,
) -> ResearchReport:
    """Erstellt aus allen Checkpoints den vollständigen, bereinigten `ResearchReport`."""
    lang_name = "Deutsch" if job.language == "de" else "English"
    citations = collect_unique_citations(findings)

    digest_blocks = [
        f"### {f.subtopic_index}. {f.title}\nKernfakten: {'; '.join(f.key_facts)}\n{f.analysis_markdown[:2500]}"
        for f in findings
    ]
    baseline_hint = f"\nÜbergreifende Voranalyse:\n{baseline_markdown[:3000]}\n" if baseline_markdown else ""

    prompt = (
        f"Thema des Forschungsauftrags: {job.topic}\n"
        f"Zielsetzung: {plan.objective_summary}\n"
        f"{baseline_hint}\n"
        f"Ergebnisse der {len(findings)} untersuchten Teilgebiete:\n\n"
        + "\n\n".join(digest_blocks)
        + "\n\nErstelle Titel, Executive Summary, prägnante Key Takeaways sowie ein fundiertes Gesamtfazit."
    )

    overview = gateway.generate_structured(
        prompt=prompt,
        system_instruction=(
            f"Du bist Chefredakteur für wissenschaftliche und strategische Dossiers. "
            f"Schreibe klar, sachlich, ohne Werbefloskeln auf {lang_name}."
        ),
        schema_cls=_ExecutiveSummarySchema,
        prefer_deep=True,
    )

    full_markdown = _assemble_report_markdown(
        overview=overview,
        findings=findings,
        citations=citations,
        language=job.language,
    )

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
