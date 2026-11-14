"""Moderner Gemini Interactions API Client (`google-genai` >= 2.30.1) mit 3-Versuche-Budget & Web-Fallback."""

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
    DEFAULT_MODEL,
    LLM_DEFAULT_TIMEOUT_SECONDS,
    LLM_FAST_TIMEOUT_SECONDS,
    LLM_PER_MODEL_RETRIES,
    MAX_STEP_ERROR_ATTEMPTS,
    MIN_LLM_TIMEOUT_SECONDS,
    MODEL_SWITCH_PAUSE_SECONDS,
)
from src.config.domain_loader import load_default_domain_config
from src.config.settings import AppSettings
from src.domain.domain_config import ModelRoutingConfig
from src.domain.exceptions import JobCancelledError, LLMProviderError
from src.domain.models import SourceCitation
from src.research.deep_agent_runner import poll_deep_research_agent
from src.research.llm_helpers import (
    SharedQuotaState,
    build_model_chain,
    compute_backoff_seconds,
    create_fast_genai_client,
    extract_citations_from_interaction,
    extract_interaction_text,
    extract_search_query_from_prompt,
    fetch_available_gemini_models,
    is_quota_error,
    is_transient_llm_error,
    sleep_with_cancel_check,
    strip_json_code_fences,
)
from src.research.multi_source_search import search_multi_source_tools
from src.research.rate_limiter import LlmRateLimiter, get_global_rate_limiter
from src.research.web_search_fallback import (
    build_extractive_markdown_from_citations,
    format_fallback_citations_context,
)

logger = logging.getLogger(__name__)
ModelT = TypeVar("ModelT", bound=BaseModel)
WebSearchFn = Callable[[str, str], list[SourceCitation]]
_UNSET: Any = object()

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
        fallback_system_instruction: str = "",
    ) -> GroundedGeneration: ...


class GeminiInteractionsGateway:
    """Resiliente Interactions-API-Implementierung mit 3-Fehler-Limit, Kurz-Timeouts & Direkt-Websuche."""

    def __init__(
        self,
        settings: AppSettings,
        preferred_model: str | None = None,
        cancel_check: Callable[[], None] | None = None,
        shared_client: genai.Client | None = None,
        on_action: Callable[[str], None] | None = None,
        fast_mode: bool = False,
        deadline_monotonic: float | None = None,
        language: str = "de",
        quota_state: SharedQuotaState | None = None,
        web_search_fn: WebSearchFn | None = _UNSET,
        available_models: list[str] | tuple[str, ...] | None = None,
        rate_limiter: LlmRateLimiter | None = None,
        enable_google_search: bool = True,
        model_routing: ModelRoutingConfig | None = None,
    ) -> None:
        if not settings.has_gemini_configured:
            raise LLMProviderError(
                "Kein gültiger GEMINI_API_KEY konfiguriert. Bitte in .env oder Secrets hinterlegen."
            )
        self._settings = settings
        self._cancel_check = cancel_check
        self._on_action = on_action
        self._fast_mode = fast_mode
        self._deadline_monotonic = deadline_monotonic
        self._language = language
        self._quota_state = quota_state or SharedQuotaState()
        self._rate_limiter = rate_limiter or get_global_rate_limiter()
        self._enable_google_search = enable_google_search
        self._model_routing = model_routing
        self._custom_web_search = web_search_fn
        self._client = shared_client or create_fast_genai_client(settings.gemini_api_key)
        self._web_search_fn: WebSearchFn | None = (
            (
                lambda q, lang: search_multi_source_tools(
                    q,
                    language=lang,
                    routing=self._model_routing,
                    settings=self._settings,
                )
            )
            if web_search_fn is _UNSET
            else web_search_fn
        )
        self._available_models: tuple[str, ...] = (
            tuple(available_models) if available_models else CANDIDATE_FAST_MODELS
        )
        fast_target = preferred_model or settings.gemini_fast_model or DEFAULT_MODEL
        deep_target = preferred_model or settings.gemini_deep_model or DEFAULT_MODEL
        self._fast_chain = build_model_chain(fast_target, self._available_models)
        self._deep_chain = build_model_chain(deep_target, available_models or CANDIDATE_DEEP_MODELS)

    def with_preferred_model(
        self,
        model: str,
        cancel_check: Callable[[], None] | None = None,
        on_action: Callable[[str], None] | None = None,
        fast_mode: bool | None = None,
        deadline_monotonic: float | None = _UNSET,
        language: str | None = None,
        available_models: list[str] | tuple[str, ...] | None = None,
        enable_google_search: bool | None = None,
        model_routing: ModelRoutingConfig | None = None,
    ) -> GeminiInteractionsGateway:
        """Erzeugt eine Gateway-Sicht mit gemeinsamem Client, Quota-Gedächtnis und Live-Action-Hook."""
        return GeminiInteractionsGateway(
            self._settings,
            preferred_model=model,
            cancel_check=cancel_check or self._cancel_check,
            shared_client=self._client,
            on_action=on_action if on_action is not None else self._on_action,
            fast_mode=self._fast_mode if fast_mode is None else fast_mode,
            deadline_monotonic=self._deadline_monotonic if deadline_monotonic is _UNSET else deadline_monotonic,
            language=language or self._language,
            quota_state=self._quota_state,
            web_search_fn=self._custom_web_search,
            available_models=available_models or self._available_models,
            rate_limiter=self._rate_limiter,
            enable_google_search=self._enable_google_search if enable_google_search is None else enable_google_search,
            model_routing=self._model_routing if model_routing is None else model_routing,
        )

    def with_action_callback(self, on_action: Callable[[str], None]) -> GeminiInteractionsGateway:
        """Erzeugt eine leichtgewichtige Sicht mit teilthemenspezifischer Live-Aktionsanzeige."""
        return self.with_preferred_model(self._fast_chain[0], on_action=on_action)

    def _emit_action(self, text: str) -> None:
        if self._on_action is not None:
            self._on_action(text)

    def _check_cancelled(self) -> None:
        if self._cancel_check is not None:
            self._cancel_check()

    def _select_models(self, prefer_deep: bool) -> list[str]:
        return self._deep_chain if prefer_deep else self._fast_chain

    def _timeout_for(self, prefer_deep: bool) -> float:
        base = LLM_DEFAULT_TIMEOUT_SECONDS if prefer_deep else LLM_FAST_TIMEOUT_SECONDS
        if self._deadline_monotonic is not None:
            remaining = self._deadline_monotonic - time.monotonic()
            if remaining <= 0:
                raise LLMProviderError("Zeitlimit für Schnellüberblick erreicht – schließe ab.")
            return max(MIN_LLM_TIMEOUT_SECONDS, min(base, remaining))
        return base

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[ModelT],
        prefer_deep: bool = False,
    ) -> ModelT:
        last_exc: Exception | None = None
        models = self._select_models(prefer_deep)
        errors = 0

        for model_name in models:
            for same_idx in range(LLM_PER_MODEL_RETRIES + 1):
                self._check_cancelled()
                self._rate_limiter.acquire(self._cancel_check)
                self._emit_action(
                    f"🤖 KI-Strukturanalyse via {model_name} (Versuch {errors + 1}/{MAX_STEP_ERROR_ATTEMPTS})..."
                )
                try:
                    timeout_sec = self._timeout_for(prefer_deep)
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
                    errors += 1
                    if is_quota_error(exc):
                        self._rate_limiter.notify_rate_limit(cooldown_seconds=15.0)
                    logger.warning("Struktur-Aufruf '%s' fehlgeschlagen (%d/%d): %s", model_name, errors, MAX_STEP_ERROR_ATTEMPTS, exc)
                    if errors >= MAX_STEP_ERROR_ATTEMPTS:
                        raise LLMProviderError(
                            f"Struktur-Analyse nach {errors} Fehlversuchen abgebrochen: {last_exc}"
                        ) from exc
                    if same_idx < LLM_PER_MODEL_RETRIES and is_transient_llm_error(exc) and not is_quota_error(exc):
                        sleep_with_cancel_check(compute_backoff_seconds(same_idx), self._cancel_check)
                        continue
                    break
            sleep_with_cancel_check(MODEL_SWITCH_PAUSE_SECONDS, self._cancel_check)

        raise LLMProviderError(f"Alle Gemini-Modelle für Struktur-Output fehlgeschlagen: {last_exc}")

    def get_warnings(self) -> list[str]:
        """Liefert alle während des Gateway-Laufs gesammelten Kontingent- und Tool-Warnungen."""
        return self._quota_state.get_warnings()

    def search_external_sources(self, query: str) -> list[SourceCitation]:
        """Durchsucht konfigurierte Web-, Community- & Fachquellen für ergänzende Evidenz."""
        if self._web_search_fn is None or not query.strip():
            return []
        self._check_cancelled()
        self._emit_action(f"🧭 Durchsuche Zusatzquellen zu »{query[:48]}«...")
        try:
            results = self._web_search_fn(query, self._language)
            return results if isinstance(results, list) else []
        except JobCancelledError:
            raise
        except Exception as exc:
            logger.debug("Zusatzquellen-Suche für '%s' fehlgeschlagen: %s", query, exc)
            return []

    def generate_grounded(
        self,
        prompt: str,
        system_instruction: str,
        use_url_context: bool = True,
        prefer_deep: bool = False,
    ) -> GroundedGeneration:
        models = self._select_models(prefer_deep)
        last_exc: Exception | None = None

        if self._enable_google_search and not self._quota_state.is_grounding_exhausted():
            res, last_exc = self._try_grounded_phase(prompt, system_instruction, use_url_context, prefer_deep, models)
            if res is not None:
                return res

        return self._run_alternative_web_fallback(prompt, system_instruction, prefer_deep, models, last_exc)

    def _try_grounded_phase(
        self,
        prompt: str,
        system_instruction: str,
        use_url_context: bool,
        prefer_deep: bool,
        models: list[str],
    ) -> tuple[GroundedGeneration | None, Exception | None]:
        tools: list[dict[str, str]] = [{"type": "google_search"}]
        if use_url_context and ("http://" in prompt or "https://" in prompt):
            tools.append({"type": "url_context"})

        last_exc: Exception | None = None
        errors = 0

        for model_name in models:
            for same_idx in range(LLM_PER_MODEL_RETRIES + 1):
                self._check_cancelled()
                self._rate_limiter.acquire(self._cancel_check)
                self._emit_action(
                    f"🌐 KI-Websuche via {model_name} (Versuch {errors + 1}/{MAX_STEP_ERROR_ATTEMPTS})..."
                )
                try:
                    timeout_sec = self._timeout_for(prefer_deep)
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
                    )
                except JobCancelledError:
                    raise
                except Exception as exc:
                    last_exc = exc
                    errors += 1
                    hit_quota = is_quota_error(exc)
                    if hit_quota:
                        self._quota_state.mark_grounding_exhausted()
                        warning_msg = (
                            "Google Search Tool im Free Tier / API-Plan nicht verfügbar (HTTP 429: Billing erforderlich) – automatische Web- und Wikipedia-Quellen verwendet."
                            if self._language == "de"
                            else "Google Search Tool unavailable on current API plan (HTTP 429: Billing required) – automated web and Wikipedia fallback sources used."
                        )
                        self._quota_state.add_warning(warning_msg)
                        self._emit_action("⚡ Google Search Tool Kontingentlimit (429) – wechsle sofort zu Web-Fallback...")
                        logger.warning("Grounded-Aufruf wegen Quota-Fehler (429) abgebrochen: %s", exc)
                        return None, last_exc
                    logger.warning("Grounded-Aufruf mit '%s' fehlgeschlagen (%d/%d): %s", model_name, errors, MAX_STEP_ERROR_ATTEMPTS, exc)
                    if errors >= MAX_STEP_ERROR_ATTEMPTS:
                        return None, last_exc
                    has_more_models = len(models) > 1 and model_name != models[-1]
                    if same_idx < LLM_PER_MODEL_RETRIES and is_transient_llm_error(exc):
                        sleep_with_cancel_check(compute_backoff_seconds(same_idx), self._cancel_check)
                        continue
                    if has_more_models:
                        self._emit_action(
                            f"⚡ Fehler bei {model_name} – wechsle Modell ({errors}/{MAX_STEP_ERROR_ATTEMPTS})..."
                        )
                        break
                    sleep_with_cancel_check(compute_backoff_seconds(same_idx), self._cancel_check)
            sleep_with_cancel_check(MODEL_SWITCH_PAUSE_SECONDS, self._cancel_check)

        return None, last_exc

    def _run_alternative_web_fallback(
        self,
        prompt: str,
        system_instruction: str,
        prefer_deep: bool,
        models: list[str],
        prior_exc: Exception | None,
    ) -> GroundedGeneration:
        query = extract_search_query_from_prompt(prompt)
        ext_citations: list[SourceCitation] = []
        if self._web_search_fn is not None and query:
            self._emit_action(f"🧭 Alternative Methode: Direkte Web- & Wikipedia-Suche zu »{query[:55]}«...")
            ext_citations = self._web_search_fn(query, self._language)

        sources_ctx = format_fallback_citations_context(ext_citations)
        augmented_prompt = f"{prompt}\n\n{sources_ctx}" if sources_ctx else prompt
        last_exc = prior_exc
        max_untooled = 2 if prior_exc is not None else MAX_STEP_ERROR_ATTEMPTS

        for idx in range(max_untooled):
            model_name = models[idx % len(models)]
            self._check_cancelled()
            self._rate_limiter.acquire(self._cancel_check)
            try:
                timeout_sec = self._timeout_for(prefer_deep)
                self._emit_action(
                    f"✍️ Werte Direktquellen ohne Such-Tool via {model_name} aus ({idx + 1}/{max_untooled})..."
                )
                interaction = self._client.interactions.create(
                    model=model_name,
                    input=augmented_prompt,
                    system_instruction=system_instruction,
                    timeout=timeout_sec,
                )
                output = extract_interaction_text(interaction)
                if output:
                    label = f"{model_name}+web" if ext_citations else model_name
                    return GroundedGeneration(text=output, citations=ext_citations, model_used=label)
            except JobCancelledError:
                raise
            except Exception as exc:
                last_exc = exc

        if ext_citations:
            warn = (
                "KI-Modell-Kontingent temporär erschöpft – Teilzusammenfassung direkt aus Web-Quellen erstellt."
                if self._language == "de"
                else "LLM quota temporarily exhausted – partial summary generated directly from web sources."
            )
            self._quota_state.add_warning(warn)
            self._emit_action("📋 KI-Kontingent erschöpft – erstelle Direkt-Zusammenfassung aus Web-Quellen...")
            return GroundedGeneration(
                text=build_extractive_markdown_from_citations(query or "Teilgebiet", ext_citations, self._language),
                citations=ext_citations,
                model_used="web-fallback-extractive",
            )
        raise LLMProviderError(f"Alle KI-Modelle & Fallbacks fehlgeschlagen: {last_exc}")

    def run_deep_research_agent(
        self,
        prompt: str,
        agent_name: str,
        on_poll_tick: Callable[[], None] | None = None,
        fallback_system_instruction: str = "",
    ) -> GroundedGeneration:
        self._emit_action(f"🔬 Starte Deep-Research-Agent ({agent_name})...")
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

        sys_inst = (
            fallback_system_instruction.strip()
            or load_default_domain_config().prompts.deep_baseline_fallback_system_prompt
        )
        return self.generate_grounded(
            prompt=prompt,
            system_instruction=sys_inst,
            use_url_context=False,
            prefer_deep=True,
        )
