"""Schneller, API-Key-freier Web-Such-Fallback bei erschöpftem Gemini-Search-Grounding-Kontingent."""

from __future__ import annotations

import html
import logging
import re
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from src.config.constants import WEB_FALLBACK_TIMEOUT_SECONDS
from src.domain.models import SourceCitation
from src.security.sanitization import sanitize_display_url

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_USER_AGENT = "ResearchBot/0.3 (FallbackSearch; +https://github.com/hschenke/research-bot)"


def _extract_ddg_target_url(raw_href: str) -> str:
    """Entpackt DuckDuckGo-Redirect-Links (`/l/?uddg=...`) in bereinigte Direkt-URLs."""
    cleaned = raw_href.strip()
    if "uddg=" in cleaned:
        parsed = urlparse(cleaned)
        uddg_vals = parse_qs(parsed.query).get("uddg", [])
        if uddg_vals:
            cleaned = unquote(uddg_vals[0])
    elif cleaned.startswith("//"):
        cleaned = "https:" + cleaned
    return sanitize_display_url(cleaned)


def _search_duckduckgo_html(query: str, max_results: int = 5) -> list[SourceCitation]:
    """Fragt DuckDuckGo Lite via robustem POST-Aufruf mit kurzem Timeout ab."""
    try:
        resp = requests.post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": query[:200]},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=(2.5, WEB_FALLBACK_TIMEOUT_SECONDS),
        )
        if resp.status_code != 200 or not resp.text:
            return []
        soup = BeautifulSoup(resp.text[:250_000], "html.parser")
        links = soup.select("a.result-link")
        snippets = soup.select(".result-snippet")
        results: list[SourceCitation] = []
        seen: set[str] = set()
        for idx, link in enumerate(links):
            safe_url = _extract_ddg_target_url(str(link.get("href") or ""))
            if safe_url == "#" or safe_url in seen:
                continue
            seen.add(safe_url)
            title = link.get_text(" ", strip=True)[:180]
            snippet_text = snippets[idx].get_text(" ", strip=True)[:350] if idx < len(snippets) else ""
            results.append(SourceCitation(url=safe_url, title=title, snippet=snippet_text))
            if len(results) >= max_results:
                break
        return results
    except requests.RequestException as exc:
        logger.debug("DuckDuckGo-Fallback nicht erreichbar: %s", exc)
        return []


def _search_wikipedia_api(query: str, language: str = "de", max_results: int = 4) -> list[SourceCitation]:
    """Fragt die öffentliche Wikipedia-Such-API als extrem schnellen, zuverlässigen Wissens-Fallback ab."""
    wiki_lang = "en" if language.lower() == "en" else "de"
    endpoint = f"https://{wiki_lang}.wikipedia.org/w/api.php"
    try:
        resp = requests.get(
            endpoint,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query[:180],
                "srlimit": max_results,
                "format": "json",
                "utf8": 1,
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=(2.5, WEB_FALLBACK_TIMEOUT_SECONDS),
            allow_redirects=False,
        )
        if resp.status_code != 200:
            return []
        payload = resp.json()
        search_items = payload.get("query", {}).get("search", [])
        citations: list[SourceCitation] = []
        for item in search_items:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            raw_snippet = html.unescape(_HTML_TAG_RE.sub("", str(item.get("snippet") or ""))).strip()
            encoded_slug = quote(title.replace(" ", "_"), safe="_:/-")
            article_url = f"https://{wiki_lang}.wikipedia.org/wiki/{encoded_slug}"
            safe_url = sanitize_display_url(article_url)
            if safe_url != "#":
                citations.append(
                    SourceCitation(url=safe_url, title=f"Wikipedia: {title}", snippet=raw_snippet[:350])
                )
        return citations
    except (requests.RequestException, ValueError) as exc:
        logger.debug("Wikipedia-Fallback nicht erreichbar: %s", exc)
        return []


def search_external_web_fallback(
    query: str,
    language: str = "de",
    max_results: int = 6,
) -> list[SourceCitation]:
    """Kombiniert Web- & Wikipedia-Treffer ohne Gemini-Such-Quota (Antwortzeit i. d. R. < 1,5 Sek.)."""
    cleaned_query = " ".join(query.split())[:180]
    if not cleaned_query:
        return []

    combined: list[SourceCitation] = []
    seen_urls: set[str] = set()
    for item in _search_duckduckgo_html(cleaned_query, max_results=4) + _search_wikipedia_api(
        cleaned_query, language=language, max_results=4
    ):
        if item.url not in seen_urls:
            seen_urls.add(item.url)
            combined.append(item)
        if len(combined) >= max_results:
            break
    return combined


def format_fallback_citations_context(citations: list[SourceCitation]) -> str:
    """Formatiert gefundene Fallback-Quellen als kompakten Kontextblock für direkte LLM-Synthese."""
    if not citations:
        return ""
    lines = ["Aktuell abgerufene Web-Quellen (bitte als `[Titel](URL)` zitieren):"]
    for idx, cite in enumerate(citations, start=1):
        lines.append(f"{idx}. **{cite.title}** ({cite.url}): {cite.snippet}")
    return "\n".join(lines)


def build_extractive_markdown_from_citations(
    title: str,
    citations: list[SourceCitation],
    language: str = "de",
) -> str:
    """Erstellt einen lesbaren Notfall-Abschnitt direkt aus Web-Treffern, falls alle KI-Modelle 429 liefern."""
    if not citations:
        return (
            f"_Für das Teilgebiet **{title}** lag ein API-Ratenlimit (HTTP 429) vor._"
            if language == "de"
            else f"_Subtopic **{title}** hit an API rate limit (HTTP 429)._"
        )
    intro = (
        f"Aufgrund eines aktuellen Gemini-API-Kontingentlimits (HTTP 429) wurden für **{title}** "
        "folgende verifizierte Direktquellen ausgewertet:"
        if language == "de"
        else f"Due to a temporary Gemini API quota limit (HTTP 429), verified direct sources for **{title}**:"
    )
    bullets = [
        f"- **[{c.title or c.url}]({c.url})**: {c.snippet}" if c.snippet else f"- [{c.title or c.url}]({c.url})"
        for c in citations
    ]
    return intro + "\n\n" + "\n".join(bullets)
