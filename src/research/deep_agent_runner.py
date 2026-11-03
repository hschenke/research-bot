"""Hintergrund-Polling und Abbruch-Steuerung für den Gemini Deep Research Agenten."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from google import genai

from src.config.constants import (
    DEEP_AGENT_MAX_WAIT_SECONDS,
    DEEP_AGENT_POLL_INTERVAL_SECONDS,
    LLM_DEFAULT_TIMEOUT_SECONDS,
    LLM_FAST_TIMEOUT_SECONDS,
)
from src.domain.exceptions import JobCancelledError
from src.research.llm_helpers import (
    extract_citations_from_interaction,
    extract_interaction_text,
    sleep_with_cancel_check,
)

logger = logging.getLogger(__name__)


def cancel_remote_interaction_quietly(client: genai.Client, interaction_id: str) -> None:
    """Bricht eine laufende Gemini-Hintergrund-Interaktion fehlertolerant ab."""
    try:
        client.interactions.cancel(id=interaction_id, timeout=10.0)
    except Exception as exc:
        logger.debug("Konnte Remote-Interaction '%s' nicht abbrechen: %s", interaction_id, exc)


def poll_deep_research_agent(
    client: genai.Client,
    prompt: str,
    agent_name: str,
    cancel_check: Callable[[], None] | None = None,
    on_poll_tick: Callable[[], None] | None = None,
) -> tuple[str, list[Any]] | None:
    """Startet und pollt den Deep-Research-Agenten; bricht bei `JobCancelledError` sofort remote ab."""
    started_id = ""
    try:
        if cancel_check is not None:
            cancel_check()
        started = client.interactions.create(
            agent=agent_name,
            input=prompt,
            background=True,
            timeout=LLM_DEFAULT_TIMEOUT_SECONDS,
        )
        started_id = str(getattr(started, "id", "") or "")
        deadline = time.monotonic() + DEEP_AGENT_MAX_WAIT_SECONDS

        while time.monotonic() < deadline:
            if on_poll_tick is not None:
                on_poll_tick()
            if cancel_check is not None:
                cancel_check()

            current = client.interactions.get(started_id, timeout=LLM_FAST_TIMEOUT_SECONDS)
            status = str(getattr(current, "status", "")).lower()
            if status == "completed":
                text = extract_interaction_text(current)
                if text:
                    return text, extract_citations_from_interaction(current)
                return None
            if status in {"failed", "cancelled", "incomplete"}:
                logger.warning("Deep-Research-Agent endete mit Status '%s' – nutze Fallback.", status)
                return None
            sleep_with_cancel_check(DEEP_AGENT_POLL_INTERVAL_SECONDS, on_poll_tick or cancel_check)
    except JobCancelledError:
        if started_id:
            cancel_remote_interaction_quietly(client, started_id)
        raise
    except Exception as exc:
        logger.warning("Deep-Research-Agent '%s' nicht verfügbar (%s) – nutze Fallback.", agent_name, exc)
    return None
