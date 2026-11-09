"""Reine Markdown-Aufbereitung für Berichte, Quellenverzeichnisse und Teilergebnis-Hinweise."""

from __future__ import annotations

import re

from src.config.domain_loader import render_prompt_template
from src.domain.domain_config import DomainConfig, ModelRoutingConfig
from src.domain.models import (
    ResearchJob,
    ResearchPlan,
    SourceCitation,
    SubTopicFinding,
)
from src.research.result_verifier import build_verification_markdown_section
from src.research.table_extractor import auto_generate_top_x_table
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
    plan: ResearchPlan | None,
    findings: list[SubTopicFinding],
    language: str,
    cfg: DomainConfig,
) -> str:
    """Erstellt einen transparenten Hinweisblock bei Teilergebnissen."""
    if plan is None:
        return ""
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


def build_execution_warnings_notice(warnings: list[str] | None, language: str) -> str:
    """Erstellt einen formatierten Markdown-Warnblock für im Lauf aufgetretene API- oder Tool-Fehler."""
    if not warnings:
        return ""
    header = (
        "> [!WARNING] **Hinweis zu API- & Tool-Limits während der Recherche:**"
        if language == "de"
        else "> [!WARNING] **Notice regarding API & Tool limits during research:**"
    )
    lines: list[str] = [header]
    for w in warnings:
        clean = w.strip()
        if clean:
            lines.append(f"> - {clean}")
    return "\n".join(lines)


def build_research_briefing_block(
    topic: str,
    focus_instructions: str,
    plan: ResearchPlan | None,
    language: str,
    routing: ModelRoutingConfig | None = None,
) -> str:
    """Erstellt einen transparenten Nachvollziehbarkeits-Block mit Thema, Vorgaben, Tools & Teilthemen."""
    if not topic and (plan is None or not plan.subtopics):
        return ""
    de = language == "de"
    header = "> [!NOTE] **Recherche-Auftrag & Suchkriterien:**" if de else "> [!NOTE] **Research Request & Search Criteria:**"
    lines = [header]
    if topic:
        lines.append(f"> - **{'Thema' if de else 'Topic'}:** {topic.strip()}")
    focus_clean = focus_instructions.strip()
    req_lbl = "Anforderungen / Vorgaben" if de else "Requirements / Focus"
    if focus_clean:
        lines.append(f"> - **{req_lbl}:** {focus_clean}")
    elif topic:
        none_lbl = "_Keine zusätzlichen Einschränkungen angegeben._" if de else "_No additional constraints specified._"
        lines.append(f"> - **{req_lbl}:** {none_lbl}")

    if routing is not None:
        tools: list[str] = ["Google Web-Suche"]
        if getattr(routing, "enable_reddit_search", False):
            tools.append("Reddit")
        if getattr(routing, "enable_hackernews_search", False):
            tools.append("Hacker News")
        if getattr(routing, "enable_wikipedia_search", False):
            tools.append("Wikipedia")
        if getattr(routing, "enable_arxiv_search", False):
            tools.append("arXiv")
        if getattr(routing, "enable_browserbase", False):
            tools.append("🌐 Browserbase (Cloud-Headless aktiv)")
        elif getattr(routing, "enable_jina_reader", False):
            tools.append("Jina Reader")
        tools_lbl = "Eingesetzte Recherche-Tools" if de else "Active Research Tools"
        lines.append(f"> - **{tools_lbl}:** {', '.join(tools)}")

    if plan and plan.subtopics:
        lines.append(f"> - **{'Untersuchte Teilthemen & Leitfragen' if de else 'Investigated Subtopics & Questions'}:**")
        for st_item in plan.subtopics:
            lines.append(f">   {st_item.index}. **{st_item.title}** – _{st_item.guiding_question}_")
    return "\n".join(lines)


def _has_markdown_table(text: str) -> bool:
    lines = text.splitlines()
    for i in range(len(lines) - 1):
        if "|" in lines[i] and re.match(r"^\s*\|?\s*:?-+:?", lines[i + 1]):
            return True
    return False


def assemble_report_markdown(
    report_title: str,
    body_markdown: str,
    key_takeaways: list[str],
    plan: ResearchPlan | None,
    findings: list[SubTopicFinding],
    citations: list[SourceCitation],
    language: str,
    cfg: DomainConfig,
    omit_wrapper_sections: bool,
    warnings: list[str] | None = None,
    job: ResearchJob | None = None,
) -> str:
    """Setzt Titel, Briefing, Warnungen, synthetisierten Hauptteil, Prüfung und Quellen zusammen."""
    routing = getattr(cfg, "model_routing", None)
    briefing = build_research_briefing_block(
        topic=job.topic if job else "",
        focus_instructions=job.focus_instructions if job else "",
        plan=plan,
        language=language,
        routing=routing,
    )
    partial_notice = build_partial_notice(plan, findings, language, cfg)
    warnings_notice = build_execution_warnings_notice(warnings, language)
    cleaned_body = _LEADING_H1_PATTERN.sub("", body_markdown.strip()).strip()
    if not cleaned_body:
        cleaned_body = build_fallback_body(findings, key_takeaways, omit_wrapper_sections)

    topic_str = job.topic if job else ""
    focus_str = job.focus_instructions if job else ""
    if cfg.is_top_x_query(topic_str, focus_str) and not _has_markdown_table(cleaned_body):
        auto_table = auto_generate_top_x_table(cleaned_body)
        if auto_table:
            lbl = "## 🏆 Top-Übersicht & Vergleich" if language == "de" else "## 🏆 Top Overview & Comparison"
            first_h2 = cleaned_body.find("## ")
            if first_h2 != -1:
                cleaned_body = f"{cleaned_body[:first_h2]}{lbl}\n\n{auto_table}\n\n{cleaned_body[first_h2:]}"
            else:
                cleaned_body = f"{lbl}\n\n{auto_table}\n\n{cleaned_body}"

    sections: list[str] = [f"# {report_title.strip()}"]
    if briefing:
        sections.extend(["", briefing])
    if partial_notice:
        sections.extend(["", partial_notice])
    if warnings_notice:
        sections.extend(["", warnings_notice])
    if cleaned_body:
        sections.extend(["", cleaned_body])

    routing = getattr(cfg, "model_routing", None)
    if routing and routing.enable_cross_verification and not omit_wrapper_sections:
        verif_section = build_verification_markdown_section(citations, findings, language)
        if verif_section:
            sections.extend(["", verif_section])

    sources_md = build_sources_section(
        citations=citations,
        language=language,
        cfg=cfg,
        omit_when_empty=omit_wrapper_sections,
    )
    if sources_md:
        sections.extend(["", sources_md])

    return sanitize_markdown_links("\n".join(sections).strip())
