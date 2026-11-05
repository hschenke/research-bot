"""Extraktion und Aufbereitung von Zitaten, Kandidaten-URLs und Stichpunkten aus Web-Evidenz."""

from __future__ import annotations

import re

from src.domain.models import SourceCitation
from src.research.gemini_client import GroundedGeneration
from src.research.web_fetcher import FetchedWebPage
from src.security.sanitization import sanitize_display_url

_INLINE_URL_PATTERN = re.compile(r"https?://[^\s\)\]>\"']+")
_BULLET_LINE_PATTERN = re.compile(r"^\s*(?:[-*•]|\d+\.)\s+(.+)$")


def merge_citations(*citation_lists: list[SourceCitation]) -> list[SourceCitation]:
    """Vereint und dedupliziert Quellenlisten anhand der bereinigten URL."""
    merged: list[SourceCitation] = []
    seen: set[str] = set()
    for items in citation_lists:
        for item in items:
            safe_url = sanitize_display_url(item.url)
            if safe_url == "#" or safe_url in seen:
                continue
            seen.add(safe_url)
            merged.append(
                SourceCitation(
                    url=safe_url,
                    title=item.title.strip(),
                    snippet=item.snippet.strip(),
                )
            )
    return merged


def discover_candidate_urls(grounded: GroundedGeneration) -> list[str]:
    """Sammelt URLs aus Grounding-Annotationen sowie direkt genannten Markdown-Links."""
    urls = [c.url for c in grounded.citations]
    for match in _INLINE_URL_PATTERN.findall(grounded.text):
        safe = sanitize_display_url(match)
        if safe != "#" and safe not in urls:
            urls.append(safe)
    return urls


def extract_inline_citations(grounded: GroundedGeneration) -> list[SourceCitation]:
    """Ergänzt Grounding-Citations um im Text verlinkte HTTP(S)-Quellen."""
    inline = [SourceCitation(url=url, title=url) for url in discover_candidate_urls(grounded)]
    return merge_citations(grounded.citations, inline)


def extract_bullet_facts(
    markdown_text: str,
    fallback_title: str,
    max_facts: int = 5,
) -> list[str]:
    """Extrahiert prägnante Stichpunkte direkt aus einer Markdown-Antwort ohne Zweit-Aufruf."""
    facts: list[str] = []
    for line in markdown_text.splitlines():
        match = _BULLET_LINE_PATTERN.match(line)
        if match:
            cleaned = match.group(1).strip()
            if 12 <= len(cleaned) <= 300:
                facts.append(cleaned)
        if len(facts) >= max_facts:
            break
    if facts:
        return facts
    first_para = next((p.strip() for p in markdown_text.splitlines() if len(p.strip()) > 25), "")
    return [first_para[:240]] if first_para else [fallback_title]


def format_scraped_pages(
    pages: list[FetchedWebPage],
    max_chars_per_page: int = 3500,
) -> str:
    """Bereitet extrahierte Volltexte geladener Webseiten für die Tiefenanalyse auf."""
    if not pages:
        return "Keine zusätzlichen Volltext-Seiten geladen."
    return "\n\n".join(
        f"### Volltext-Quelle: {page.title or page.url}\nURL: {page.url}\nAuszug:\n{page.text_content[:max_chars_per_page]}"
        for page in pages
    )
