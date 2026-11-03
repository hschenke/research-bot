"""Hilfsfunktionen für Modell-Filterung, Antwort-Parsing, Backoff und Fehlerklassifikation."""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from typing import Any

from google import genai

from src.config.constants import (
    CANCEL_POLL_SLICE_SECONDS,
    DEFAULT_MODEL,
    DEPRECATED_EXACT_MODELS,
    DEPRECATED_MODEL_PREFIXES,
    FALLBACK_AVAILABLE_MODELS,
    RETRY_BACKOFF_BASE_SECONDS,
    RETRY_BACKOFF_MAX_SECONDS,
)
from src.domain.models import SourceCitation
from src.security.sanitization import sanitize_display_url

logger = logging.getLogger(__name__)

_EXCLUDED_MODEL_KEYWORDS: tuple[str, ...] = (
    "tts",
    "image",
    "banana",
    "lyria",
    "robotics",
    "computer-use",
    "embedding",
    "transcribe",
    "live",
    "gemma",
    "customtools",
    "aqa",
    "audio",
    "native",
)

_TRANSIENT_ERROR_KEYWORDS: tuple[str, ...] = (
    "503",
    "502",
    "504",
    "500",
    "429",
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

_QUOTA_KEYWORDS: tuple[str, ...] = ("429", "quota", "resource_exhausted", "ratelimit")
_JSON_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def is_supported_generative_model(model_name: str) -> bool:
    """Prüft, ob ein Modell ein aktuelles, nicht-abgekündigtes Gemini-Textmodell ist."""
    cleaned = model_name.strip().lower()
    if not cleaned.startswith("gemini-"):
        return False
    if cleaned.startswith(DEPRECATED_MODEL_PREFIXES) or cleaned in DEPRECATED_EXACT_MODELS:
        return False
    return not any(keyword in cleaned for keyword in _EXCLUDED_MODEL_KEYWORDS)


def normalize_preferred_model(model_name: str) -> str:
    """Ersetzt veraltete oder leere Modellnamen defensiv durch `DEFAULT_MODEL`."""
    cleaned = (model_name or "").strip()
    if not cleaned:
        return DEFAULT_MODEL
    low = cleaned.lower()
    if low.startswith(DEPRECATED_MODEL_PREFIXES) or low in DEPRECATED_EXACT_MODELS:
        return DEFAULT_MODEL
    return cleaned


def build_model_chain(preferred: str, defaults: tuple[str, ...]) -> list[str]:
    """Baut eine deduplizierte Fallback-Kette aus aktiven Gemini-Modellen auf."""
    chain: list[str] = []
    primary = normalize_preferred_model(preferred)
    if primary:
        chain.append(primary)
    for candidate in defaults:
        if candidate not in chain and is_supported_generative_model(candidate):
            chain.append(candidate)
    return chain


def fetch_available_gemini_models(api_key: str) -> list[str]:
    """Fragt verfügbare Textmodelle über das SDK ab und setzt `DEFAULT_MODEL` an Position 1."""
    clean_key = (api_key or "").strip()
    if not clean_key or clean_key.startswith("your_"):
        return list(FALLBACK_AVAILABLE_MODELS)

    try:
        client = genai.Client(api_key=clean_key)
        raw_names = [m.name.replace("models/", "") for m in client.models.list()]
        filtered = [m for m in raw_names if is_supported_generative_model(m)]
        if DEFAULT_MODEL in filtered:
            filtered.remove(DEFAULT_MODEL)
        filtered.insert(0, DEFAULT_MODEL)
        return filtered if filtered else list(FALLBACK_AVAILABLE_MODELS)
    except Exception as exc:
        logger.warning(
            "Konnte Gemini-Modellliste nicht dynamisch abrufen (%s) – nutze Standardliste.",
            exc,
        )
        return list(FALLBACK_AVAILABLE_MODELS)


def strip_json_code_fences(raw_text: str) -> str:
    """Entfernt optionale Markdown-Codeblöcke um eine JSON-Antwort."""
    return _JSON_FENCE_PATTERN.sub("", raw_text.strip()).strip()


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
    """Erkennt spezifische Quota-/Rate-Limit-Meldungen."""
    msg = str(exc).lower()
    return any(keyword in msg for keyword in _QUOTA_KEYWORDS)


def compute_backoff_seconds(attempt_index: int) -> float:
    """Berechnet exponentiellen Backoff mit leichtem Jitter gegen Lastspitzen."""
    raw = min(RETRY_BACKOFF_MAX_SECONDS, RETRY_BACKOFF_BASE_SECONDS * (2**attempt_index))
    jitter = random.uniform(0.0, 0.6)  # noqa: S311 - nur für unkritischen Retry-Jitter
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
