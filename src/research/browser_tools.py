"""Moderne Browser- & Extraktions-Tools (Jina AI Reader & Browserbase-Anbindung)."""

from __future__ import annotations

import logging
from typing import Any

import requests

from src.config.settings import AppSettings
from src.security.sanitization import sanitize_display_url

logger = logging.getLogger(__name__)

_JINA_READER_PREFIX = "https://r.jina.ai/"
_BROWSERBASE_API_URL = "https://api.browserbase.com/v1/sessions"
_USER_AGENT = "ResearchBot/0.8 (BrowserTools; +https://github.com/hschenke/research-bot)"


def fetch_via_jina_reader(
    url: str,
    timeout_seconds: float = 6.0,
    max_chars: int = 4000,
) -> str:
    """Extrahiert sauberen Markdown-Volltext einer Webseite über Jina Reader (bypasst Cookie-Banner & JS-Bloat)."""
    safe_url = sanitize_display_url(url)
    if safe_url == "#" or not safe_url.startswith("http"):
        return ""

    endpoint = f"{_JINA_READER_PREFIX}{safe_url}"
    try:
        resp = requests.get(
            endpoint,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/markdown",
                "X-Return-Format": "markdown",
            },
            timeout=(2.5, timeout_seconds),
        )
        if resp.status_code == 200 and resp.text.strip():
            return resp.text.strip()[:max_chars]
        return ""
    except requests.RequestException as exc:
        logger.debug("Jina Reader Fehler für '%s': %s", url, exc)
        return ""


class BrowserbaseIntegration:
    """Schnittstelle zu Browserbase für serverlose Headless-Browser-Sitzungen."""

    def __init__(self, settings: AppSettings) -> None:
        self._api_key = settings.browserbase_api_key
        self._project_id = settings.browserbase_project_id

    @property
    def is_available(self) -> bool:
        """Gibt an, ob gültige Browserbase-Zugangsdaten hinterlegt sind."""
        return bool(self._api_key and not self._api_key.startswith("your_"))

    def get_status_summary(self) -> dict[str, Any]:
        """Liefert eine Status-Übersicht für UI- & Routing-Anzeigen."""
        if not self.is_available:
            return {
                "active": False,
                "label": "Browserbase: Nicht konfiguriert",
                "info": "Kostenfreier Key auf browserbase.com erhältlich. Jina Reader & Direkt-HTTP aktiv.",
            }
        return {
            "active": True,
            "label": "Browserbase: Bereit",
            "info": f"Cloud-Headless-Browser aktiv (Projekt-ID: {self._project_id or 'Standard'}).",
        }

    def create_session(self, timeout_seconds: float = 5.0) -> str | None:
        """Erstellt eine neue Browserbase-Sitzung, falls API-Key vorhanden."""
        if not self.is_available:
            return None
        try:
            payload: dict[str, Any] = {}
            if self._project_id:
                payload["projectId"] = self._project_id
            resp = requests.post(
                _BROWSERBASE_API_URL,
                json=payload,
                headers={"X-BB-API-Key": self._api_key, "Content-Type": "application/json"},
                timeout=(2.5, timeout_seconds),
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return str(data.get("id") or "")
            return None
        except (requests.RequestException, ValueError) as exc:
            logger.debug("Browserbase Session-Erstellung fehlgeschlagen: %s", exc)
            return None
