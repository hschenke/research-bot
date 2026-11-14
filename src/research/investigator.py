"""Phase 2: Konfigurierbare, faktenzentrierte Teilthemen-Untersuchung mit tiefenabhängigem Aufwand."""

from __future__ import annotations

from collections.abc import Callable

from src.config.domain_loader import load_default_domain_config, render_prompt_template
from src.domain.domain_config import DomainConfig
from src.domain.models import (
    DepthProfile,
    ResearchJob,
    SourceCitation,
    SubTopicFinding,
    SubTopicPlan,
)
from src.research.evidence_consolidator import (
    build_focus_context,
    collect_external_sources,
    consolidate_evidence_resilient,
    format_external_evidence_note,
)
from src.research.evidence_parser import (
    extract_bullet_facts,
    extract_inline_citations,
    merge_citations,
)
from src.research.followup_runner import (
    collect_new_urls,
    fetch_urls_safely,
    mark_urls_seen,
    run_followup_rounds,
)
from src.research.gemini_client import ResearchLLMGateway
from src.research.web_fetcher import SafeWebFetcher


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
    prompt = render_prompt_template(
        cfg.prompts.investigator_quick_user_prompt,
        {
            "topic": job.topic,
            "focus_block": build_focus_context(job, cfg),
            "subtopic_index": subtopic.index,
            "subtopic_title": subtopic.title,
            "guiding_question": subtopic.guiding_question,
            "search_queries": queries_str,
            "max_subtopic_bullets": profile.max_subtopic_bullets,
        },
    )
    sys_prompt = render_prompt_template(
        cfg.prompts.investigator_quick_system_prompt,
        {"language": lang_name, "style_rules": cfg.style.global_style_rules},
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
        key_facts=extract_bullet_facts(grounded.text, subtopic.title, max_facts=profile.max_subtopic_bullets),
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
    """Untersucht ein Teilthema über Single-Pass (Quick) oder echtes Multi-Pass (Standard/Deep)."""
    cfg = domain_config or load_default_domain_config()
    lang_name = "Deutsch" if job.language == "de" else "US English"
    routing = getattr(cfg, "model_routing", None)
    is_single_pass = (
        profile.single_pass
        or (profile.max_urls_per_subtopic <= 0 and profile.followup_rounds <= 0)
        or (routing is not None and routing.single_pass_research)
    )
    if is_single_pass:
        return _investigate_quick_single_pass(gateway, job, subtopic, profile, cfg, lang_name, on_heartbeat)

    focus_block = build_focus_context(job, cfg)
    sys_prompt = render_prompt_template(
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
            "search_queries": ", ".join(subtopic.search_queries) or subtopic.title,
            "max_subtopic_bullets": profile.max_subtopic_bullets,
        },
    )
    initial_grounded = gateway.generate_grounded(
        prompt=initial_prompt,
        system_instruction=sys_prompt,
        use_url_context=False,
        prefer_deep=profile.prefer_deep_model,
    )
    if on_heartbeat:
        on_heartbeat()

    ext_citations = collect_external_sources(gateway, subtopic, profile, routing, on_heartbeat)
    seen_urls: set[str] = set()
    candidate_urls = collect_new_urls(initial_grounded, ext_citations, seen_urls)
    scraped_pages = fetch_urls_safely(
        web_fetcher, candidate_urls, profile.max_urls_per_subtopic, on_heartbeat, on_action
    )
    mark_urls_seen(seen_urls, candidate_urls, scraped_pages, max(1, profile.max_urls_per_subtopic))

    page_cites = [SourceCitation(url=p.url, title=p.title, snippet=p.text_content[:200]) for p in scraped_pages]
    all_citations = merge_citations(extract_inline_citations(initial_grounded), ext_citations, page_cites)
    evidence_notes = [initial_grounded.text]
    ext_note = format_external_evidence_note(ext_citations)
    if ext_note:
        evidence_notes.append(ext_note)

    if on_action is not None:
        on_action("🧩 Verdichte gesammelte Fakten & Primärquellen...")
    structured = consolidate_evidence_resilient(
        gateway, lang_name, job, subtopic, profile, cfg, evidence_notes, scraped_pages, baseline_excerpt,
        remaining_followups=profile.followup_rounds,
    )
    structured, all_citations = run_followup_rounds(
        gateway=gateway,
        web_fetcher=web_fetcher,
        lang_name=lang_name,
        job=job,
        subtopic=subtopic,
        profile=profile,
        cfg=cfg,
        focus_block=focus_block,
        system_instruction=sys_prompt,
        baseline_excerpt=baseline_excerpt,
        evidence_notes=evidence_notes,
        scraped_pages=scraped_pages,
        seen_urls=seen_urls,
        all_citations=all_citations,
        structured=structured,
        on_heartbeat=on_heartbeat,
        on_action=on_action,
    )
    return SubTopicFinding(
        subtopic_index=subtopic.index,
        title=subtopic.title,
        analysis_markdown=structured.analysis_markdown.strip(),
        key_facts=[f.strip() for f in structured.key_facts[: profile.max_subtopic_bullets] if f.strip()],
        open_questions=[q.strip() for q in structured.open_questions if q.strip()],
        citations=all_citations,
    )
