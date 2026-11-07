"""Multi-Source Recherche-Aggregator für kostenfreie Web-, Community- & Fachquellen."""

from __future__ import annotations

import logging

from src.config.settings import AppSettings
from src.domain.domain_config import ModelRoutingConfig
from src.domain.models import SourceCitation
from src.research.arxiv_search import search_arxiv
from src.research.hackernews_search import search_hackernews
from src.research.reddit_search import search_reddit_community
from src.research.web_search_fallback import _search_duckduckgo_html, _search_wikipedia_api

logger = logging.getLogger(__name__)


def search_multi_source_tools(
    query: str,
    language: str = "de",
    routing: ModelRoutingConfig | None = None,
    max_results: int = 8,
    settings: AppSettings | None = None,
) -> list[SourceCitation]:
    """Kombiniert Live-Websuche, Reddit, Hacker News, Wikipedia & arXiv gemäß Routing-Konfiguration."""
    cleaned_query = " ".join(query.split())[:180]
    if not cleaned_query:
        return []

    enable_ddg = routing.enable_duckduckgo_search if routing else True
    enable_reddit = routing.enable_reddit_search if routing else True
    enable_hn = routing.enable_hackernews_search if routing else True
    enable_wiki = routing.enable_wikipedia_search if routing else True
    enable_arxiv = routing.enable_arxiv_search if routing else False

    collected: list[SourceCitation] = []
    seen_urls: set[str] = set()

    def _append_many(items: list[SourceCitation], limit: int) -> None:
        added = 0
        for item in items:
            if item.url not in seen_urls:
                seen_urls.add(item.url)
                collected.append(item)
                added += 1
                if added >= limit:
                    break

    # 1. DuckDuckGo (Allgemeine Websuche)
    if enable_ddg:
        _append_many(_search_duckduckgo_html(cleaned_query, max_results=3), 3)

    # 2. Reddit Community-Suche
    if enable_reddit:
        client_id = settings.reddit_client_id if settings else ""
        client_sec = settings.reddit_client_secret if settings else ""
        _append_many(
            search_reddit_community(
                cleaned_query,
                max_results=3,
                client_id=client_id,
                client_secret=client_sec,
            ),
            2,
        )

    # 3. Hacker News Tech & Startup Suche
    if enable_hn:
        _append_many(search_hackernews(cleaned_query, max_results=3), 2)

    # 4. Wikipedia Enzyklopädie
    if enable_wiki:
        _append_many(_search_wikipedia_api(cleaned_query, language=language, max_results=2), 2)

    # 5. ArXiv wissenschaftliche Preprints
    if enable_arxiv:
        _append_many(search_arxiv(cleaned_query, max_results=2), 2)

    return collected[:max_results]
