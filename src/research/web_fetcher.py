"""SSRF-geschützter Web-Scraper mit Redirect-Prüfung, Größenlimit und Text-Extraktion."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src.config.constants import (
    HTTP_CONNECT_TIMEOUT_SECONDS,
    HTTP_READ_TIMEOUT_SECONDS,
    MAX_CANDIDATE_URL_PROBE_FACTOR,
    MAX_EXTRACTED_CHARS_PER_PAGE,
    MAX_HTTP_REDIRECTS,
    MAX_WEB_PAGE_BYTES,
)
from src.domain.exceptions import SSRFBlockedError, WebFetchError
from src.security.ssrf_guard import DnsResolver, validate_safe_external_url

logger = logging.getLogger(__name__)

_ALLOWED_CONTENT_TYPES: tuple[str, ...] = (
    "text/html",
    "text/plain",
    "application/xhtml+xml",
)
_NOISE_TAGS: tuple[str, ...] = (
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
)
_WHITESPACE_BLOCKS = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class FetchedWebPage:
    """Bereinigter Volltext-Auszug einer erfolgreich geladenen Webseite."""

    url: str
    title: str
    text_content: str


def extract_clean_text_from_html(html_text: str, max_chars: int = MAX_EXTRACTED_CHARS_PER_PAGE) -> tuple[str, str]:
    """Entfernt Skripte/Navigation aus HTML und extrahiert Titel sowie kompakten Fließtext."""
    soup = BeautifulSoup(html_text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""

    for tag in soup(_NOISE_TAGS):
        tag.decompose()

    main_node = soup.find("article") or soup.find("main") or soup.body or soup
    lines = [line.strip() for line in main_node.get_text(separator="\n").splitlines()]
    non_empty = [line for line in lines if len(line) > 1]
    normalized = _WHITESPACE_BLOCKS.sub("\n\n", "\n".join(non_empty)).strip()
    return title[:300], normalized[:max_chars]


class SafeWebFetcher:
    """Lädt externe Webseiten unter strikter Einhaltung von SSRF-, Timeout- und Speicherlimits."""

    def __init__(
        self,
        dns_resolver: DnsResolver | None = None,
        connect_timeout: float = HTTP_CONNECT_TIMEOUT_SECONDS,
        read_timeout: float = HTTP_READ_TIMEOUT_SECONDS,
        max_bytes: int = MAX_WEB_PAGE_BYTES,
    ) -> None:
        self._dns_resolver = dns_resolver
        self._timeout = (connect_timeout, read_timeout)
        self._max_bytes = max_bytes
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; ResearchBot/0.1; +https://github.com/hschenke/research-bot)",
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
                "Accept-Language": "de,en;q=0.8",
            }
        )

    def _validate_url(self, url: str) -> str:
        if self._dns_resolver is not None:
            return validate_safe_external_url(url, dns_resolver=self._dns_resolver)
        return validate_safe_external_url(url)

    def fetch_page(self, url: str) -> FetchedWebPage:
        """Lädt eine einzelne URL inkl. sicherer Redirect-Verfolgung und Text-Aufbereitung."""
        current_url = self._validate_url(url)

        for _hop in range(MAX_HTTP_REDIRECTS + 1):
            try:
                with self._session.get(
                    current_url,
                    timeout=self._timeout,
                    allow_redirects=False,
                    stream=True,
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location", "").strip()
                        if not location:
                            raise WebFetchError("Redirect ohne Zieladresse erhalten.")
                        next_url = urljoin(current_url, location)
                        current_url = self._validate_url(next_url)
                        continue

                    if response.status_code != 200:
                        raise WebFetchError(f"HTTP-Status {response.status_code} für '{current_url}'.")

                    content_type = response.headers.get("Content-Type", "").lower()
                    if content_type and not any(ct in content_type for ct in _ALLOWED_CONTENT_TYPES):
                        raise WebFetchError(f"Nicht unterstützter Content-Type '{content_type}'.")

                    raw_bytes = self._read_bounded_body(response)
                    encoding = response.encoding or "utf-8"
                    decoded = raw_bytes.decode(encoding, errors="replace")
                    title, clean_text = extract_clean_text_from_html(decoded)
                    if not clean_text:
                        raise WebFetchError("Kein verwertbarer Textinhalt gefunden.")
                    return FetchedWebPage(url=current_url, title=title, text_content=clean_text)
            except SSRFBlockedError:
                raise
            except requests.RequestException as exc:
                raise WebFetchError(f"Netzwerkfehler beim Abruf von '{current_url}': {exc}") from exc

        raise WebFetchError(f"Zu viele Weiterleitungen (> {MAX_HTTP_REDIRECTS}) für '{url}'.")

    def _read_bounded_body(self, response: requests.Response) -> bytes:
        chunks: list[bytes] = []
        total_read = 0
        for chunk in response.iter_content(chunk_size=16_384):
            if not chunk:
                continue
            total_read += len(chunk)
            if total_read > self._max_bytes:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def fetch_many_tolerant(
        self,
        urls: list[str],
        max_pages: int,
        cancel_check: Callable[[], None] | None = None,
        on_action: Callable[[str], None] | None = None,
    ) -> list[FetchedWebPage]:
        """Lädt bis zu `max_pages` Webseiten; begrenzt Fehlversuche und meldet Live-Aktionen."""
        if max_pages <= 0 or not urls:
            return []

        fetched: list[FetchedWebPage] = []
        seen_urls: set[str] = set()
        attempts = 0
        max_attempts = max(max_pages, max_pages * MAX_CANDIDATE_URL_PROBE_FACTOR)

        for raw_url in urls:
            if len(fetched) >= max_pages or attempts >= max_attempts:
                break
            if cancel_check is not None:
                cancel_check()

            candidate = raw_url.strip()
            if not candidate or candidate in seen_urls:
                continue
            seen_urls.add(candidate)
            attempts += 1

            if on_action is not None:
                host = (urlparse(candidate).hostname or candidate)[:45]
                on_action(f"📄 Lade Webseite ({len(fetched) + 1}/{max_pages}): {host}...")

            try:
                page = self.fetch_page(candidate)
                fetched.append(page)
            except (SSRFBlockedError, WebFetchError) as exc:
                logger.debug("Überspringe Webseite '%s': %s", candidate, exc)
        return fetched

    def close(self) -> None:
        self._session.close()
