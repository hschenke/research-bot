"""Hacker News Such-Integration über die offizielle, kostenfreie Algolia-Search-API."""

from __future__ import annotations

import logging
from typing import Any

import requests

from src.domain.models import SourceCitation
from src.security.sanitization import sanitize_display_url

logger = logging.getLogger(__name__)

_HN_API_ENDPOINT = "https://hn.algolia.com/api/v1/search"
_USER_AGENT = "ResearchBot/0.8 (HN; +https://github.com/hschenke/research-bot)"


def search_hackernews(
    query: str,
    max_results: int = 5,
    timeout_seconds: float = 4.0,
) -> list[SourceCitation]:
    """Sucht Tech-, Startup- & Fachthemen auf Hacker News (kostenfrei, kein Key erforderlich)."""
    cleaned_query = " ".join(query.split())[:180]
    if not cleaned_query:
        return []

    try:
        resp = requests.get(
            _HN_API_ENDPOINT,
            params={
                "query": cleaned_query,
                "tags": "story",
                "hitsPerPage": min(max(1, max_results), 15),
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=(2.0, timeout_seconds),
        )
        if resp.status_code != 200:
            logger.debug("Hacker News API Status %d für '%s'", resp.status_code, cleaned_query)
            return []

        payload: dict[str, Any] = resp.json()
        hits = payload.get("hits", [])
        citations: list[SourceCitation] = []
        seen_urls: set[str] = set()

        for item in hits:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue

            raw_url = str(item.get("url") or "").strip()
            obj_id = str(item.get("objectID") or "")
            target_url = raw_url if raw_url.startswith("http") else f"https://news.ycombinator.com/item?id={obj_id}"
            safe_url = sanitize_display_url(target_url)
            if safe_url == "#" or safe_url in seen_urls:
                continue
            seen_urls.add(safe_url)

            points = item.get("points") or 0
            comments = item.get("num_comments") or 0
            author = str(item.get("author") or "").strip()
            snippet = f"[Hacker News: {points} Punkte, {comments} Kommentare | Autor: {author}]"

            citations.append(
                SourceCitation(
                    url=safe_url,
                    title=f"Hacker News: {title[:160]}",
                    snippet=snippet,
                )
            )
            if len(citations) >= max_results:
                break

        return citations
    except (requests.RequestException, ValueError) as exc:
        logger.debug("Hacker News Suche fehlgeschlagen (%s): %s", cleaned_query, exc)
        return []
