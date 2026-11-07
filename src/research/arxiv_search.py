"""Wissenschaftliche Preprints & Fachaufsätze über die offizielle ArXiv-API."""

from __future__ import annotations

import logging

try:
    import defusedxml.ElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET  # noqa: S314
import requests

from src.domain.models import SourceCitation
from src.security.sanitization import sanitize_display_url

logger = logging.getLogger(__name__)

_ARXIV_API_URL = "https://export.arxiv.org/api/query"
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
_USER_AGENT = "ResearchBot/0.8 (ArXiv; +https://github.com/hschenke/research-bot)"


def search_arxiv(
    query: str,
    max_results: int = 4,
    timeout_seconds: float = 4.0,
) -> list[SourceCitation]:
    """Sucht wissenschaftliche Preprints und Fachaufsätze auf arXiv (kostenfrei, kein Key)."""
    cleaned_query = " ".join(query.split())[:180]
    if not cleaned_query:
        return []

    try:
        resp = requests.get(
            _ARXIV_API_URL,
            params={
                "search_query": f"all:{cleaned_query}",
                "start": 0,
                "max_results": min(max(1, max_results), 10),
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=(2.0, timeout_seconds),
        )
        if resp.status_code != 200 or not resp.content:
            return []

        root = ET.fromstring(resp.content)  # noqa: S314
        citations: list[SourceCitation] = []
        seen_urls: set[str] = set()

        for entry in root.findall("atom:entry", _ATOM_NS):
            title_node = entry.find("atom:title", _ATOM_NS)
            id_node = entry.find("atom:id", _ATOM_NS)
            summary_node = entry.find("atom:summary", _ATOM_NS)

            if title_node is None or id_node is None:
                continue

            raw_title = " ".join((title_node.text or "").split())
            raw_url = (id_node.text or "").strip()
            safe_url = sanitize_display_url(raw_url)
            if safe_url == "#" or safe_url in seen_urls:
                continue
            seen_urls.add(safe_url)

            authors: list[str] = []
            for author_node in entry.findall("atom:author", _ATOM_NS)[:3]:
                name_node = author_node.find("atom:name", _ATOM_NS)
                if name_node is not None and name_node.text:
                    authors.append(name_node.text.strip())

            author_str = ", ".join(authors) if authors else "arXiv"
            summary_text = " ".join((summary_node.text or "").split())[:300] if summary_node is not None else ""
            snippet = f"[arXiv: {author_str}] {summary_text}"

            citations.append(
                SourceCitation(
                    url=safe_url,
                    title=f"arXiv: {raw_title[:160]}",
                    snippet=snippet,
                )
            )
            if len(citations) >= max_results:
                break

        return citations
    except (requests.RequestException, ET.ParseError) as exc:
        logger.debug("ArXiv-Suche fehlgeschlagen (%s): %s", cleaned_query, exc)
        return []
