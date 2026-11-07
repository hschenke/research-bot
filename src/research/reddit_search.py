"""Reddit Such-Integration für echte Praxisberichte, Meinungen & Diskussionsergebnisse."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from src.domain.models import SourceCitation
from src.security.sanitization import sanitize_display_url

logger = logging.getLogger(__name__)

_DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
_USER_AGENT = "Mozilla/5.0"


def _extract_clean_url(raw_href: str) -> str:
    cleaned = raw_href.strip()
    if "uddg=" in cleaned:
        parsed = urlparse(cleaned)
        vals = parse_qs(parsed.query).get("uddg", [])
        if vals:
            cleaned = unquote(vals[0])
    elif cleaned.startswith("//"):
        cleaned = "https:" + cleaned
    return sanitize_display_url(cleaned)


def search_reddit_community(
    query: str,
    max_results: int = 5,
    timeout_seconds: float = 4.0,
    client_id: str = "",
    client_secret: str = "",
) -> list[SourceCitation]:
    """Durchsucht Reddit nach Community-Diskussionen & Praxiserfahrungen (kostenfrei)."""
    cleaned_query = " ".join(query.split())[:180]
    if not cleaned_query:
        return []

    # Fall 1: Offizielle Reddit-API, falls Secrets konfiguriert sind
    if client_id and client_secret:
        auth_citations = _search_reddit_oauth(cleaned_query, max_results, timeout_seconds, client_id, client_secret)
        if auth_citations:
            return auth_citations

    # Fall 2: Robuster, API-Key-freier Fallback via DuckDuckGo Targeted Search
    return _search_reddit_via_ddg(cleaned_query, max_results, timeout_seconds)


def _search_reddit_via_ddg(query: str, max_results: int, timeout_seconds: float) -> list[SourceCitation]:
    target_query = f"site:reddit.com {query}"
    try:
        resp = requests.post(
            _DDG_LITE_URL,
            data={"q": target_query},
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "de,en;q=0.8"},
            timeout=(2.0, timeout_seconds),
        )
        if resp.status_code != 200 or not resp.text:
            return []

        soup = BeautifulSoup(resp.text[:250_000], "html.parser")
        links = soup.select("a.result-link")
        snippets = soup.select(".result-snippet")
        citations: list[SourceCitation] = []
        seen_urls: set[str] = set()

        for idx, link in enumerate(links):
            safe_url = _extract_clean_url(str(link.get("href") or ""))
            if safe_url == "#" or "reddit.com" not in safe_url or safe_url in seen_urls:
                continue
            seen_urls.add(safe_url)
            title = link.get_text(" ", strip=True)[:180]
            snippet_text = snippets[idx].get_text(" ", strip=True)[:350] if idx < len(snippets) else ""
            citations.append(
                SourceCitation(
                    url=safe_url,
                    title=f"Reddit: {title}",
                    snippet=snippet_text or "[Reddit-Diskussion zum Thema]",
                )
            )
            if len(citations) >= max_results:
                break
        return citations
    except requests.RequestException as exc:
        logger.debug("Reddit DDG-Suche fehlgeschlagen (%s): %s", query, exc)
        return []


def _search_reddit_oauth(
    query: str,
    max_results: int,
    timeout_seconds: float,
    client_id: str,
    client_secret: str,
) -> list[SourceCitation]:
    try:
        auth = requests.auth.HTTPBasicAuth(client_id, client_secret)
        token_resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=auth,
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": _USER_AGENT},
            timeout=(2.0, timeout_seconds),
        )
        if token_resp.status_code != 200:
            return []
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return []

        search_resp = requests.get(
            "https://oauth.reddit.com/search",
            params={"q": query, "limit": min(max_results, 10), "sort": "relevance"},
            headers={"Authorization": f"Bearer {access_token}", "User-Agent": _USER_AGENT},
            timeout=(2.0, timeout_seconds),
        )
        if search_resp.status_code != 200:
            return []
        data: dict[str, Any] = search_resp.json()
        children = data.get("data", {}).get("children", [])
        citations: list[SourceCitation] = []
        for child in children:
            post = child.get("data", {})
            title = str(post.get("title") or "").strip()
            permalink = str(post.get("permalink") or "")
            score = post.get("score") or 0
            comments = post.get("num_comments") or 0
            sub = post.get("subreddit_name_prefixed") or "r/reddit"
            full_url = sanitize_display_url(f"https://www.reddit.com{permalink}")
            if full_url != "#":
                citations.append(
                    SourceCitation(
                        url=full_url,
                        title=f"Reddit ({sub}): {title[:150]}",
                        snippet=f"[{score} Upvotes, {comments} Kommentare] {str(post.get('selftext') or '')[:250]}",
                    )
                )
        return citations
    except (requests.RequestException, ValueError) as exc:
        logger.debug("Reddit OAuth fehlgeschlagen: %s", exc)
        return []
