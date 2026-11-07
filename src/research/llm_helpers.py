"""Hilfsfunktionen für SDK-Setup, Modell-Filterung, Antwort-Parsing, Quota-Status & Backoff."""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from google import genai

from src.config.constants import (
    CANCEL_POLL_SLICE_SECONDS,
    DEFAULT_MODEL,
    GROUNDING_QUOTA_COOLDOWN_SECONDS,
    LLM_DEFAULT_TIMEOUT_SECONDS,
    RETRY_BACKOFF_BASE_SECONDS,
    RETRY_BACKOFF_MAX_SECONDS,
)
from src.domain.models import SourceCitation
from src.security.sanitization import sanitize_display_url

logger = logging.getLogger(__name__)

_TRANSIENT_ERROR_KEYWORDS: tuple[str, ...] = (
    "503",
    "502",
    "504",
    "500",
    "429",
    "too_many_requests",
    "service_unavailable",
    "high demand",
    "overloaded",
    "unavailable",
    "timeout",
    "timed out",
    "connection",
    "reset",
    "temporarily",
    "exhausted",
    "ratelimit",
    "leere ",
)

_QUOTA_KEYWORDS: tuple[str, ...] = (
    "429",
    "too_many_requests",
    "quota",
    "resource_exhausted",
    "ratelimit",
)
_JSON_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class SharedQuotaState:
    """Thread-sicherer Cooldown-Tracker, damit parallele Worker erschöpfte Quotas sofort überspringen."""

    def __init__(self, cooldown_seconds: float = GROUNDING_QUOTA_COOLDOWN_SECONDS) -> None:
        self._cooldown = cooldown_seconds
        self._lock = threading.Lock()
        self._exhausted_until: float = 0.0
        self._warnings: list[str] = []

    def is_grounding_exhausted(self) -> bool:
        with self._lock:
            return time.monotonic() < self._exhausted_until

    def mark_grounding_exhausted(self) -> None:
        with self._lock:
            self._exhausted_until = time.monotonic() + self._cooldown

    def add_warning(self, msg: str) -> None:
        with self._lock:
            clean = msg.strip()
            if clean and clean not in self._warnings:
                self._warnings.append(clean)

    def get_warnings(self) -> list[str]:
        with self._lock:
            return list(self._warnings)


def create_fast_genai_client(api_key: str) -> genai.Client:
    """Erstellt einen `genai.Client` mit großzügigem Client-Timeout ohne versteckte SDK-Endlos-Retries."""
    http_opts = {
        "timeout": int(LLM_DEFAULT_TIMEOUT_SECONDS * 1000),
        "retry_options": {"attempts": 1},
    }
    try:
        client = genai.Client(api_key=api_key, http_options=http_opts)
    except TypeError:
        client = genai.Client(api_key=api_key)

    sdk_cfg = getattr(getattr(client, "interactions", None), "sdk_configuration", None)
    retry_cfg = getattr(sdk_cfg, "retry_config", None)
    if retry_cfg is not None and isinstance(getattr(retry_cfg, "strategy", None), str):
        retry_cfg.strategy = "none"
    return client


def is_supported_generative_model(
    model_name: str,
    allowed_models: list[str] | tuple[str, ...] | None = None,
) -> bool:
    """Prüft, ob ein Modell in der freigegebenen Modellliste enthalten ist."""
    cleaned = model_name.strip()
    allowed = list(allowed_models) if allowed_models else [DEFAULT_MODEL]
    return cleaned in allowed


def normalize_preferred_model(
    model_name: str,
    allowed_models: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Validiert den gewünschten Modellnamen gegen die freigegebenen Modelle mit Fallback."""
    cleaned = model_name.strip()
    allowed = [m.strip() for m in (allowed_models or [DEFAULT_MODEL]) if m.strip()]
    fallback = allowed[0] if allowed else DEFAULT_MODEL
    return cleaned if cleaned in allowed else fallback


def build_model_chain(
    preferred: str,
    defaults: tuple[str, ...] | list[str],
) -> list[str]:
    """Erstellt eine deduplizierte Modell-Fallback-Kette mit dem Wunschmodell an erster Stelle."""
    allowed = [m.strip() for m in defaults if m.strip()] or [DEFAULT_MODEL]
    primary = normalize_preferred_model(preferred, allowed)
    chain = [primary]
    for candidate in allowed:
        if candidate not in chain:
            chain.append(candidate)
    return chain


def fetch_available_gemini_models(
    api_key: str = "",
    allowed_models: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Gibt die konfigurierten freigegebenen KI-Modelle zurück."""
    _ = api_key
    if allowed_models:
        cleaned = [m.strip() for m in allowed_models if m.strip()]
        if cleaned:
            return cleaned
    return [DEFAULT_MODEL]


def strip_json_code_fences(raw_text: str) -> str:
    """Entfernt optionale Markdown-Codeblöcke um eine JSON-Antwort."""
    return _JSON_FENCE_PATTERN.sub("", raw_text.strip()).strip()


def extract_search_query_from_prompt(prompt: str) -> str:
    """Extrahiert eine prägnante Web-Suchanfrage aus dem Analyse-Prompt für den Direkt-Fallback."""
    topic_part = ""
    focus_part = ""
    for raw_line in prompt.splitlines():
        line = raw_line.strip()
        if line.startswith(("Gesamtthema:", "Thema:")):
            topic_part = line.split(":", 1)[1].strip()
        elif line.startswith(("Suchfokus:", "Empfohlene Suchbegriffe:", "Teilgebiet", "Teilthema")):
            val = line.split(":", 1)[1].strip() if ":" in line else line
            if not focus_part:
                focus_part = val.split(",")[0].strip()
    combined = f"{topic_part} {focus_part}".strip()
    if combined:
        return combined[:150]
    first_line = next((ln.strip() for ln in prompt.splitlines() if ln.strip()), "")
    return first_line[:150]


def extract_interaction_text(interaction: Any) -> str:
    """Liest `output_text` oder fällt auf beliebige `model_output`-Textblöcke in `steps` zurück."""
    direct = str(getattr(interaction, "output_text", "") or "").strip()
    if direct:
        return direct

    parts: list[str] = []
    for step in getattr(interaction, "steps", None) or []:
        if getattr(step, "type", None) != "model_output":
            continue
        for block in getattr(step, "content", None) or []:
            if getattr(block, "type", None) == "text":
                txt = str(getattr(block, "text", "") or "").strip()
                if txt:
                    parts.append(txt)
    return "\n\n".join(parts).strip()


def extract_citations_from_interaction(interaction: Any) -> list[SourceCitation]:
    """Extrahiert verifizierte `url_citation`-Annotationen aus den Interaction-Steps."""
    citations: list[SourceCitation] = []
    seen_urls: set[str] = set()

    for step in getattr(interaction, "steps", None) or []:
        if getattr(step, "type", None) != "model_output":
            continue
        for block in getattr(step, "content", None) or []:
            if getattr(block, "type", None) != "text":
                continue
            for annotation in getattr(block, "annotations", None) or []:
                if getattr(annotation, "type", None) != "url_citation":
                    continue
                safe_url = sanitize_display_url(str(getattr(annotation, "url", "") or ""))
                if safe_url == "#" or safe_url in seen_urls:
                    continue
                seen_urls.add(safe_url)
                title = str(getattr(annotation, "title", "") or "").strip()
                citations.append(SourceCitation(url=safe_url, title=title[:200]))
    return citations


def is_transient_llm_error(exc: Exception) -> bool:
    """Erkennt vorübergehende Überlastungs-, Rate-Limit- oder Netzwerkfehler (z. B. HTTP 503/429)."""
    msg = str(exc).lower()
    return any(keyword in msg for keyword in _TRANSIENT_ERROR_KEYWORDS)


def is_quota_error(exc: Exception) -> bool:
    """Erkennt spezifische Quota-/Rate-Limit-Meldungen (HTTP 429 / too_many_requests)."""
    msg = str(exc).lower()
    return any(keyword in msg for keyword in _QUOTA_KEYWORDS)


def compute_backoff_seconds(attempt_index: int) -> float:
    """Berechnet kurzen exponentiellen Backoff mit leichtem Jitter gegen Lastspitzen."""
    raw = min(RETRY_BACKOFF_MAX_SECONDS, RETRY_BACKOFF_BASE_SECONDS * (2**attempt_index))
    jitter = random.uniform(0.0, 0.35)  # noqa: S311 - nur für unkritischen Retry-Jitter
    return raw + jitter


def sleep_with_cancel_check(
    duration_seconds: float,
    cancel_check: Callable[[], None] | None = None,
) -> None:
    """Wartet unterbrechbar in kurzen Intervallen, damit ein Nutzer-Abbruch sofort greift."""
    deadline = time.monotonic() + max(0.0, duration_seconds)
    while True:
        if cancel_check is not None:
            cancel_check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(CANCEL_POLL_SLICE_SECONDS, remaining))
