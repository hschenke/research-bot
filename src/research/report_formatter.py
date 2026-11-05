"""Reine Markdown-Aufbereitung für Berichte, Quellenverzeichnisse und Teilergebnis-Hinweise."""

from __future__ import annotations

import re

from src.config.domain_loader import render_prompt_template
from src.domain.domain_config import DomainConfig
from src.domain.models import (
    ResearchPlan,
    SourceCitation,
    SubTopicFinding,
)
from src.security.sanitization import sanitize_display_url, sanitize_markdown_links

_LEADING_H1_PATTERN = re.compile(r"^\s*#\s+[^\n]+\n*")


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


def build_sources_section(
    citations: list[SourceCitation],
    language: str,
    cfg: DomainConfig,
    omit_when_empty: bool,
) -> str:
    """Erstellt ein kompaktes Quellenverzeichnis gemäß Konfiguration."""
    if not cfg.style.include_sources_section:
        return ""
    heading = cfg.style.sources_heading_de if language == "de" else cfg.style.sources_heading_en
    if not citations:
        if omit_when_empty:
            return ""
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


def build_partial_notice(
    plan: ResearchPlan,
    findings: list[SubTopicFinding],
    language: str,
    cfg: DomainConfig,
) -> str:
    """Erstellt einen transparenten Hinweisblock bei Teilergebnissen."""
    total = len(plan.subtopics)
    if total <= 0 or len(findings) >= total:
        return ""
    done_indices = {f.subtopic_index for f in findings}
    skipped = [st.title for st in plan.subtopics if st.index not in done_indices]
    skipped_str = ", ".join(skipped)
    suffix = (
        (f" (übersprungen: _{skipped_str}_)" if language == "de" else f" (skipped: _{skipped_str}_)")
        if skipped_str
        else ""
    )
    tpl = cfg.style.partial_notice_de if language == "de" else cfg.style.partial_notice_en
    return render_prompt_template(
        tpl,
        {"done": len(findings), "total": total, "skipped_suffix": suffix},
    )


def build_fallback_body(
    findings: list[SubTopicFinding],
    takeaways: list[str],
    omit_wrapper_sections: bool,
) -> str:
    """Baut den Markdown-Hauptteil für Fallbacks (als reine Liste oder kompakte Kapitel)."""
    ordered = sorted(findings, key=lambda item: item.subtopic_index)
    if omit_wrapper_sections:
        bullet_lines: list[str] = []
        for finding in ordered:
            for fact in finding.key_facts:
                if fact.strip():
                    bullet_lines.append(f"- {fact.strip()}")
            if not finding.key_facts and finding.analysis_markdown.strip():
                bullet_lines.append(finding.analysis_markdown.strip())
        if bullet_lines:
            return "\n".join(bullet_lines)
        return "\n".join(f"- {t}" for t in takeaways)

    parts: list[str] = []
    if takeaways:
        parts.extend(f"- {point.strip()}" for point in takeaways if point.strip())
    for finding in ordered:
        parts.extend(["", f"## {finding.subtopic_index}. {finding.title}", "", finding.analysis_markdown.strip()])
    return "\n".join(parts).strip()


def assemble_report_markdown(
    report_title: str,
    body_markdown: str,
    key_takeaways: list[str],
    plan: ResearchPlan,
    findings: list[SubTopicFinding],
    citations: list[SourceCitation],
    language: str,
    cfg: DomainConfig,
    omit_wrapper_sections: bool,
) -> str:
    """Setzt Titel, optionalen Teilhinweis, synthetisierten Hauptteil und Quellen zusammen."""
    partial_notice = build_partial_notice(plan, findings, language, cfg)
    cleaned_body = _LEADING_H1_PATTERN.sub("", body_markdown.strip()).strip()
    if not cleaned_body:
        cleaned_body = build_fallback_body(findings, key_takeaways, omit_wrapper_sections)

    sections: list[str] = [f"# {report_title.strip()}"]
    if partial_notice:
        sections.extend(["", partial_notice])
    if cleaned_body:
        sections.extend(["", cleaned_body])

    sources_md = build_sources_section(
        citations=citations,
        language=language,
        cfg=cfg,
        omit_when_empty=omit_wrapper_sections,
    )
    if sources_md:
        sections.extend(["", sources_md])

    return sanitize_markdown_links("\n".join(sections).strip())
