"""Moderner Gemini Interactions API Client (`google-genai` >= 2.30.1) mit Retry, Timeout & Fallback."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from google import genai
from pydantic import BaseModel

from src.config.constants import (
    CANDIDATE_DEEP_MODELS,
    CANDIDATE_FAST_MODELS,
    DEFAULT_MODEL,
    LLM_DEFAULT_TIMEOUT_SECONDS,
    LLM_FAST_TIMEOUT_SECONDS,
    LLM_PER_MODEL_RETRIES,
    MODEL_SWITCH_PAUSE_SECONDS,
)
from src.config.settings import AppSettings
from src.domain.exceptions import JobCancelledError, LLMProviderError
from src.domain.models import SourceCitation
from src.research.deep_agent_runner import poll_deep_research_agent
from src.research.llm_helpers import (
    build_model_chain,
    compute_backoff_seconds,
    extract_citations_from_interaction,
    extract_interaction_text,
    fetch_available_gemini_models,
    is_quota_error,
    is_transient_llm_error,
    sleep_with_cancel_check,
    strip_json_code_fences,
)

logger = logging.getLogger(__name__)
ModelT = TypeVar("ModelT", bound=BaseModel)

__all__ = [
    "GeminiInteractionsGateway",
    "GroundedGeneration",
    "ResearchLLMGateway",
    "extract_citations_from_interaction",
    "fetch_available_gemini_models",
]


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
    ) -> ModelT: ...

    def generate_grounded(
        self,
        prompt: str,
        system_instruction: str,
        use_url_context: bool = True,
        prefer_deep: bool = False,
    ) -> GroundedGeneration: ...

    def run_deep_research_agent(
        self,
        prompt: str,
        agent_name: str,
        on_poll_tick: Callable[[], None] | None = None,
    ) -> GroundedGeneration: ...


class GeminiInteractionsGateway:
    """Resiliente Implementierung über die Google GenAI Interactions API mit Timeouts & Backoff."""

    def __init__(
        self,
        settings: AppSettings,
        preferred_model: str | None = None,
        cancel_check: Callable[[], None] | None = None,
        shared_client: genai.Client | None = None,
    ) -> None:
        if not settings.has_gemini_configured:
            raise LLMProviderError(
                "Kein gültiger GEMINI_API_KEY konfiguriert. Bitte in .env oder Secrets hinterlegen."
            )
        self._settings = settings
        self._cancel_check = cancel_check
        self._client = shared_client or genai.Client(api_key=settings.gemini_api_key)
        fast_target = preferred_model or settings.gemini_fast_model or DEFAULT_MODEL
        deep_target = preferred_model or settings.gemini_deep_model or DEFAULT_MODEL
        self._fast_chain = build_model_chain(fast_target, CANDIDATE_FAST_MODELS)
        self._deep_chain = build_model_chain(deep_target, CANDIDATE_DEEP_MODELS)
        self._search_grounding_quota_exhausted = False

    def with_preferred_model(
        self,
        model: str,
        cancel_check: Callable[[], None] | None = None,
    ) -> GeminiInteractionsGateway:
        """Erzeugt eine Gateway-Sicht mit spezifischem Primärmodell und optionalem Abbruch-Check."""
        gw = GeminiInteractionsGateway(
            self._settings,
            preferred_model=model,
            cancel_check=cancel_check or self._cancel_check,
            shared_client=self._client,
        )
        gw._search_grounding_quota_exhausted = self._search_grounding_quota_exhausted
        return gw

    def _check_cancelled(self) -> None:
        if self._cancel_check is not None:
            self._cancel_check()

    def _select_models(self, prefer_deep: bool) -> list[str]:
        return self._deep_chain if prefer_deep else self._fast_chain

    def _timeout_for(self, prefer_deep: bool) -> float:
        return LLM_DEFAULT_TIMEOUT_SECONDS if prefer_deep else LLM_FAST_TIMEOUT_SECONDS

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[ModelT],
        prefer_deep: bool = False,
    ) -> ModelT:
        last_exc: Exception | None = None
        models = self._select_models(prefer_deep)
        timeout_sec = self._timeout_for(prefer_deep)

        for m_idx, model_name in enumerate(models):
            for attempt in range(LLM_PER_MODEL_RETRIES + 1):
                self._check_cancelled()
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
                        timeout=timeout_sec,
                    )
                    raw_text = strip_json_code_fences(extract_interaction_text(interaction))
                    if not raw_text:
                        raise ValueError("Leere Struktur-Antwort vom Modell erhalten.")
                    return schema_cls.model_validate_json(raw_text)
                except JobCancelledError:
                    raise
                except Exception as exc:
                    last_exc = exc
                    if attempt < LLM_PER_MODEL_RETRIES and is_transient_llm_error(exc):
                        sleep_with_cancel_check(compute_backoff_seconds(attempt), self._cancel_check)
                        continue
                    logger.warning("Modell '%s' fehlgeschlagen (%s). Wechsle Fallback...", model_name, exc)
                    break

            if m_idx + 1 < len(models):
                sleep_with_cancel_check(MODEL_SWITCH_PAUSE_SECONDS, self._cancel_check)

        raise LLMProviderError(f"Alle Gemini-Modelle für Struktur-Output fehlgeschlagen: {last_exc}")

    def generate_grounded(
        self,
        prompt: str,
        system_instruction: str,
        use_url_context: bool = True,
        prefer_deep: bool = False,
    ) -> GroundedGeneration:
        last_exc: Exception | None = None
        models = self._select_models(prefer_deep)
        timeout_sec = self._timeout_for(prefer_deep)

        if not self._search_grounding_quota_exhausted:
            tools: list[dict[str, str]] = [{"type": "google_search"}]
            if use_url_context and ("http://" in prompt or "https://" in prompt):
                tools.append({"type": "url_context"})

            quota_failures = 0
            for m_idx, model_name in enumerate(models):
                res, last_exc, hit_quota = self._try_grounded_with_model(
                    model_name, prompt, system_instruction, tools, timeout_sec
                )
                if res is not None:
                    return res
                if hit_quota:
                    quota_failures += 1
                if m_idx + 1 < len(models):
                    sleep_with_cancel_check(MODEL_SWITCH_PAUSE_SECONDS, self._cancel_check)

            if quota_failures >= len(models):
                self._search_grounding_quota_exhausted = True

        return self._generate_ungrounded_fallback(prompt, system_instruction, models, timeout_sec, last_exc)

    def _try_grounded_with_model(
        self,
        model_name: str,
        prompt: str,
        system_instruction: str,
        tools: list[dict[str, str]],
        timeout_sec: float,
    ) -> tuple[GroundedGeneration | None, Exception | None, bool]:
        last_exc: Exception | None = None
        for attempt in range(LLM_PER_MODEL_RETRIES + 1):
            self._check_cancelled()
            try:
                interaction = self._client.interactions.create(
                    model=model_name,
                    input=prompt,
                    system_instruction=system_instruction,
                    tools=tools,
                    timeout=timeout_sec,
                )
                output = extract_interaction_text(interaction)
                if not output:
                    raise ValueError("Leere Grounding-Antwort vom Modell erhalten.")
                return (
                    GroundedGeneration(
                        text=output,
                        citations=extract_citations_from_interaction(interaction),
                        model_used=model_name,
                    ),
                    None,
                    False,
                )
            except JobCancelledError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < LLM_PER_MODEL_RETRIES and is_transient_llm_error(exc) and not is_quota_error(exc):
                    sleep_with_cancel_check(compute_backoff_seconds(attempt), self._cancel_check)
                    continue
                logger.warning("Grounded-Aufruf mit '%s' fehlgeschlagen: %s", model_name, exc)
                return None, exc, is_quota_error(exc)
        return None, last_exc, False

    def _generate_ungrounded_fallback(
        self,
        prompt: str,
        system_instruction: str,
        models: list[str],
        timeout_sec: float,
        prior_exc: Exception | None,
    ) -> GroundedGeneration:
        last_exc = prior_exc
        for model_name in models:
            self._check_cancelled()
            try:
                interaction = self._client.interactions.create(
                    model=model_name,
                    input=prompt,
                    system_instruction=system_instruction,
                    timeout=timeout_sec,
                )
                output = extract_interaction_text(interaction)
                if output:
                    return GroundedGeneration(text=output, citations=[], model_used=model_name)
            except JobCancelledError:
                raise
            except Exception as exc:
                last_exc = exc
        raise LLMProviderError(f"Alle Gemini-Modelle für Web-Grounding fehlgeschlagen: {last_exc}")

    def run_deep_research_agent(
        self,
        prompt: str,
        agent_name: str,
        on_poll_tick: Callable[[], None] | None = None,
    ) -> GroundedGeneration:
        polled = poll_deep_research_agent(
            client=self._client,
            prompt=prompt,
            agent_name=agent_name,
            cancel_check=self._cancel_check,
            on_poll_tick=on_poll_tick,
        )
        if polled is not None:
            text, citations = polled
            return GroundedGeneration(text=text, citations=citations, model_used=agent_name)

        return self.generate_grounded(
            prompt=prompt,
            system_instruction="Führe eine umfassende Vorabrecherche mit verifizierten Web-Quellen durch.",
            use_url_context=False,
            prefer_deep=True,
        )
