"""Moderner Gemini Interactions API Client (`google-genai` >= 2.30.1) mit Fallback & Grounding."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from google import genai
from pydantic import BaseModel

from src.config.constants import (
    CANDIDATE_DEEP_MODELS,
    CANDIDATE_FAST_MODELS,
    DEEP_AGENT_MAX_WAIT_SECONDS,
    DEEP_AGENT_POLL_INTERVAL_SECONDS,
    DEFAULT_MODEL,
    FALLBACK_AVAILABLE_MODELS,
)
from src.config.settings import AppSettings
from src.domain.exceptions import LLMProviderError
from src.domain.models import SourceCitation
from src.security.sanitization import sanitize_display_url

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class GroundedGeneration:
    """Antwort eines KI-Aufrufs inklusive extrahierter Web-Quellen."""

    text: str
    citations: list[SourceCitation]
    model_used: str


class ResearchLLMGateway(Protocol):
    """Abstrakte KI-Schnittstelle für Planung, Web-Grounding und Synthese."""

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[ModelT],
        prefer_deep: bool = False,
    ) -> ModelT:
        """Erzeugt eine strikt typisierte JSON-Antwort gemäß Pydantic-Schema."""
        ...

    def generate_grounded(
        self,
        prompt: str,
        system_instruction: str,
        use_url_context: bool = True,
        prefer_deep: bool = False,
    ) -> GroundedGeneration:
        """Führt eine webfundierte Recherche mit Google Search & URL Context aus."""
        ...

    def run_deep_research_agent(
        self,
        prompt: str,
        agent_name: str,
        on_poll_tick: Callable[[], None] | None = None,
    ) -> GroundedGeneration:
        """Startet den autonomen Gemini Deep Research Agenten im Hintergrundmodus."""
        ...


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


def fetch_available_gemini_models(api_key: str) -> list[str]:
    """
    Fragt die über den Gemini-API-Key verfügbaren Modelle dynamisch über das Google GenAI SDK ab.
    Filtert auf generative Textmodelle und platziert `DEFAULT_MODEL` ('gemini-3.5-flash-lite')
    als Standard an die erste Stelle. Fällt bei Netzwerk-/Auth-Problemen sicher auf Fallback-Modelle zurück.
    """
    clean_key = (api_key or "").strip()
    if not clean_key or clean_key.startswith("your_"):
        return list(FALLBACK_AVAILABLE_MODELS)

    try:
        client = genai.Client(api_key=clean_key)
        all_models = [m.name.replace("models/", "") for m in client.models.list()]
        filtered = [
            m
            for m in all_models
            if m.startswith("gemini-")
            and not any(keyword in m.lower() for keyword in _EXCLUDED_MODEL_KEYWORDS)
            and m != "gemini-2.5-flash"  # Veraltetes Modell (Google 404)
        ]

        if DEFAULT_MODEL in filtered:
            filtered.remove(DEFAULT_MODEL)
            filtered.insert(0, DEFAULT_MODEL)
        elif DEFAULT_MODEL not in filtered:
            filtered.insert(0, DEFAULT_MODEL)

        return filtered if filtered else list(FALLBACK_AVAILABLE_MODELS)
    except Exception as exc:
        logger.warning(
            "Konnte Gemini-Modellliste nicht dynamisch von API abrufen (%s) – nutze Standardliste.",
            exc,
        )
        return list(FALLBACK_AVAILABLE_MODELS)


def _build_model_chain(preferred: str, defaults: tuple[str, ...]) -> list[str]:
    """Baut eine deduplizierte Modell-Fallback-Kette auf."""
    chain: list[str] = []
    if preferred.strip():
        chain.append(preferred.strip())
    for candidate in defaults:
        if candidate not in chain:
            chain.append(candidate)
    return chain


def extract_citations_from_interaction(interaction: Any) -> list[SourceCitation]:
    """Extrahiert verifizierte `url_citation`-Annotationen aus den Interaction-Steps."""
    citations: list[SourceCitation] = []
    seen_urls: set[str] = set()

    steps = getattr(interaction, "steps", None) or []
    for step in steps:
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


class GeminiInteractionsGateway:
    """Konkrete Implementierung über die Google GenAI Interactions API."""

    def __init__(
        self,
        settings: AppSettings,
        preferred_model: str | None = None,
    ) -> None:
        if not settings.has_gemini_configured:
            raise LLMProviderError(
                "Kein gültiger GEMINI_API_KEY konfiguriert. Bitte in .env oder Secrets hinterlegen."
            )
        self._settings = settings
        self._preferred_model = preferred_model or ""
        self._client = genai.Client(api_key=settings.gemini_api_key)
        fast_target = preferred_model or settings.gemini_fast_model or DEFAULT_MODEL
        deep_target = preferred_model or settings.gemini_deep_model or DEFAULT_MODEL
        self._fast_chain = _build_model_chain(fast_target, CANDIDATE_FAST_MODELS)
        self._deep_chain = _build_model_chain(deep_target, CANDIDATE_DEEP_MODELS)
        self._search_grounding_quota_exhausted = False

    def with_preferred_model(self, model: str) -> GeminiInteractionsGateway:
        """Erzeugt eine Gateway-Kopie mit einem spezifischen Modell als Priorität 1."""
        gw = GeminiInteractionsGateway(self._settings, preferred_model=model)
        gw._search_grounding_quota_exhausted = self._search_grounding_quota_exhausted
        return gw

    def _select_models(self, prefer_deep: bool) -> list[str]:
        return self._deep_chain if prefer_deep else self._fast_chain

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[ModelT],
        prefer_deep: bool = False,
    ) -> ModelT:
        last_exc: Exception | None = None
        for model_name in self._select_models(prefer_deep):
            try:
                interaction = self._client.interactions.create(
                    model=model_name,
                    input=prompt,
                    system_instruction=system_instruction,
                    response_format={
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": schema_cls.model_json_schema(),
                    },
                )
                raw_text = (interaction.output_text or "").strip()
                if not raw_text:
                    raise ValueError("Leere Struktur-Antwort vom Modell erhalten.")
                return schema_cls.model_validate_json(raw_text)
            except Exception as exc:
                logger.warning(
                    "Strukturierter Aufruf mit '%s' fehlgeschlagen (%s). Prüfe Fallback...",
                    model_name,
                    exc,
                )
                last_exc = exc

        raise LLMProviderError(f"Alle Gemini-Modelle für Struktur-Output fehlgeschlagen: {last_exc}")

    def generate_grounded(
        self,
        prompt: str,
        system_instruction: str,
        use_url_context: bool = True,
        prefer_deep: bool = False,
    ) -> GroundedGeneration:
        last_exc: Exception | None = None

        if not self._search_grounding_quota_exhausted:
            tools: list[dict[str, str]] = [{"type": "google_search"}]
            if use_url_context:
                tools.append({"type": "url_context"})

            for model_name in self._select_models(prefer_deep):
                try:
                    interaction = self._client.interactions.create(
                        model=model_name,
                        input=prompt,
                        system_instruction=system_instruction,
                        tools=tools,
                    )
                    output = (interaction.output_text or "").strip()
                    if not output:
                        raise ValueError("Leere Grounding-Antwort vom Modell erhalten.")
                    citations = extract_citations_from_interaction(interaction)
                    return GroundedGeneration(
                        text=output,
                        citations=citations,
                        model_used=model_name,
                    )
                except Exception as exc:
                    logger.warning(
                        "Grounded-Aufruf mit '%s' fehlgeschlagen (%s). Prüfe Fallback...",
                        model_name,
                        exc,
                    )
                    last_exc = exc
                    err_text = str(exc).lower()
                    if any(k in err_text for k in ("429", "quota", "exhausted", "ratelimit")):
                        logger.warning(
                            "Such-Tool-Quota erschöpft – deaktiviere Tool für nachfolgende Schritte."
                        )
                        self._search_grounding_quota_exhausted = True
                        break

        # Fallback auf direkte Modellgenerierung (ohne Google Search Tool)
        logger.info("Führe Recherche-Analyse ohne Such-Tools aus (Fallback)...")
        for model_name in self._select_models(prefer_deep):
            try:
                interaction = self._client.interactions.create(
                    model=model_name,
                    input=prompt,
                    system_instruction=system_instruction,
                )
                output = (interaction.output_text or "").strip()
                if output:
                    return GroundedGeneration(
                        text=output,
                        citations=[],
                        model_used=model_name,
                    )
            except Exception as exc:
                last_exc = exc

        raise LLMProviderError(f"Alle Gemini-Modelle für Web-Grounding fehlgeschlagen: {last_exc}")

    def run_deep_research_agent(
        self,
        prompt: str,
        agent_name: str,
        on_poll_tick: Callable[[], None] | None = None,
    ) -> GroundedGeneration:
        try:
            started = self._client.interactions.create(
                agent=agent_name,
                input=prompt,
                background=True,
            )
            deadline = time.monotonic() + DEEP_AGENT_MAX_WAIT_SECONDS
            while time.monotonic() < deadline:
                if on_poll_tick is not None:
                    on_poll_tick()
                current = self._client.interactions.get(started.id)
                status = str(getattr(current, "status", "")).lower()
                if status == "completed":
                    text = (current.output_text or "").strip()
                    if text:
                        return GroundedGeneration(
                            text=text,
                            citations=extract_citations_from_interaction(current),
                            model_used=agent_name,
                        )
                    break
                if status in {"failed", "cancelled", "incomplete"}:
                    logger.warning("Deep-Research-Agent endete mit Status '%s' – nutze Fallback.", status)
                    break
                time.sleep(DEEP_AGENT_POLL_INTERVAL_SECONDS)
        except Exception as exc:
            logger.warning(
                "Deep-Research-Agent '%s' nicht verfügbar (%s) – weiche auf Grounded Deep Model aus.",
                agent_name,
                exc,
            )

        return self.generate_grounded(
            prompt=prompt,
            system_instruction="Führe eine umfassende, faktenbasierte Vorabrecherche mit verifizierten Web-Quellen durch.",
            use_url_context=True,
            prefer_deep=True,
        )
