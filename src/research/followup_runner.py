"""Nachrecherche-Runden, URL-Scraping und Quellenabgleich für tiefe Teilthemen."""

from __future__ import annotations

import logging
from collections.abc import Callable

from src.config.constants import MAX_CANDIDATE_URL_PROBE_FACTOR
from src.config.domain_loader import render_prompt_template
from src.domain.domain_config import DomainConfig
from src.domain.exceptions import JobCancelledError, ResearchBotError
from src.domain.models import (
    DepthProfile,
    ResearchJob,
    SourceCitation,
    SubTopicPlan,
)
from src.research.evidence_consolidator import (
    StructuredSubTopicAnalysis,
    consolidate_evidence_resilient,
)
from src.research.evidence_parser import (
    discover_candidate_urls,
    extract_inline_citations,
    merge_citations,
)
from src.research.gemini_client import GroundedGeneration, ResearchLLMGateway
from src.research.web_fetcher import FetchedWebPage, SafeWebFetcher

logger = logging.getLogger(__name__)


def fetch_urls_safely(
    web_fetcher: SafeWebFetcher,
    urls: list[str],
    max_pages: int,
    on_heartbeat: Callable[[], None] | None,
    on_action: Callable[[str], None] | None,
) -> list[FetchedWebPage]:
    """Lädt bis zu `max_pages` URLs fehlertolerant mit optionalem Live-Action-Hook."""
    if not urls or max_pages <= 0:
        return []
    try:
        return web_fetcher.fetch_many_tolerant(
            urls=urls,
            max_pages=max_pages,
            cancel_check=on_heartbeat,
            on_action=on_action,
        )
    except TypeError:
        return web_fetcher.fetch_many_tolerant(urls=urls, max_pages=max_pages, cancel_check=on_heartbeat)


def collect_new_urls(
    grounded: GroundedGeneration,
    extra_citations: list[SourceCitation],
    seen_urls: set[str],
) -> list[str]:
    """Sammelt noch nicht abgerufene Kandidaten-URLs aus Grounding und Zusatzquellen."""
    ordered: list[str] = []
    for url in [*discover_candidate_urls(grounded), *(c.url for c in extra_citations)]:
        if url and url not in seen_urls and url not in ordered:
            ordered.append(url)
    return ordered


def mark_urls_seen(
    seen_urls: set[str],
    candidate_urls: list[str],
    scraped_pages: list[FetchedWebPage],
    max_pages: int,
) -> None:
    """Merkt sondierte und erfolgreich geladene URLs, damit Runden nicht doppelt abrufen."""
    probe_limit = max(1, max_pages * MAX_CANDIDATE_URL_PROBE_FACTOR)
    seen_urls.update(candidate_urls[:probe_limit])
    seen_urls.update(page.url for page in scraped_pages)


def _resolve_round_questions(
    open_questions: list[str],
    subtopic: SubTopicPlan,
    profile: DepthProfile,
    job: ResearchJob,
    round_idx: int,
    max_q: int,
) -> list[str]:
    """Liefert offene Detailfragen oder im Deep-Profil eine strukturierte Gegenprüfung."""
    cleaned = [q.strip() for q in open_questions[:max_q] if q.strip()]
    if cleaned:
        return cleaned
    if round_idx == 0 and profile.followup_rounds >= 2 and len(subtopic.search_queries) > 1:
        extra_queries = [q.strip() for q in subtopic.search_queries[1 : 1 + max_q] if q.strip()]
        if extra_queries:
            prefix = (
                "Verify primary sources, metrics, and counter-arguments for"
                if job.language == "en"
                else "Prüfe Primärquellen, konkrete Kennzahlen und Gegenpositionen zu"
            )
            return [f"{prefix}: {', '.join(extra_queries)}"]
    return []


def run_followup_rounds(
    gateway: ResearchLLMGateway,
    web_fetcher: SafeWebFetcher,
    lang_name: str,
    job: ResearchJob,
    subtopic: SubTopicPlan,
    profile: DepthProfile,
    cfg: DomainConfig,
    focus_block: str,
    system_instruction: str,
    baseline_excerpt: str,
    evidence_notes: list[str],
    scraped_pages: list[FetchedWebPage],
    seen_urls: set[str],
    all_citations: list[SourceCitation],
    structured: StructuredSubTopicAnalysis,
    on_heartbeat: Callable[[], None] | None,
    on_action: Callable[[str], None] | None,
) -> tuple[StructuredSubTopicAnalysis, list[SourceCitation]]:
    """Führt konfigurierte Nachrecherche-Runden inkl. neuem URL-Scraping und Re-Konsolidierung aus."""
    max_q = cfg.limits.max_open_questions_per_followup
    followup_limit = max(1, profile.max_urls_per_subtopic // 2) if profile.max_urls_per_subtopic > 0 else 0
    current = structured
    citations = all_citations

    for round_idx in range(profile.followup_rounds):
        round_questions = _resolve_round_questions(
            current.open_questions, subtopic, profile, job, round_idx, max_q
        )
        if not round_questions:
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
                    "focus_block": focus_block,
                    "subtopic_title": subtopic.title,
                    "open_questions": "; ".join(round_questions),
                },
            )
            followup_res = gateway.generate_grounded(
                prompt=followup_prompt,
                system_instruction=system_instruction,
                use_url_context=False,
                prefer_deep=profile.prefer_deep_model,
            )
            evidence_notes.append(followup_res.text)
            new_urls = collect_new_urls(followup_res, [], seen_urls)
            new_pages = fetch_urls_safely(web_fetcher, new_urls, followup_limit, on_heartbeat, on_action)
            mark_urls_seen(seen_urls, new_urls, new_pages, max(1, followup_limit))
            scraped_pages.extend(new_pages)
            new_cites = [SourceCitation(url=p.url, title=p.title, snippet=p.text_content[:200]) for p in new_pages]
            citations = merge_citations(citations, extract_inline_citations(followup_res), new_cites)
            current = consolidate_evidence_resilient(
                gateway,
                lang_name,
                job,
                subtopic,
                profile,
                cfg,
                evidence_notes,
                scraped_pages,
                baseline_excerpt,
                remaining_followups=profile.followup_rounds - round_idx - 1,
            )
        except JobCancelledError:
            raise
        except ResearchBotError as exc:
            logger.warning("Nachrecherche für '%s' übersprungen (%s).", subtopic.title, exc)
            break

    return current, citations
