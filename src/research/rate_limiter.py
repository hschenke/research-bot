"""Thread-sicherer Rate Limiter mit Sliding-Window & Pacing gegen API-429-Limits."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable

from src.research.llm_helpers import sleep_with_cancel_check

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT_RPM = 12
DEFAULT_MIN_CALL_INTERVAL_SECONDS = 4.5
DEFAULT_429_COOLDOWN_SECONDS = 15.0


def _is_test_environment() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ


class LlmRateLimiter:
    """
    Sichert strikt zu, dass das konfigurierte RPM-Limit (z. B. 12 oder 15)
    in jedem rollenden 60-Sekunden-Fenster und pro Thread eingehalten wird.
    """

    def __init__(
        self,
        max_rpm: int = DEFAULT_RATE_LIMIT_RPM,
        min_interval_seconds: float = DEFAULT_MIN_CALL_INTERVAL_SECONDS,
    ) -> None:
        self._max_rpm = max(1, max_rpm)
        self._min_interval = 0.0 if _is_test_environment() else max(0.0, min_interval_seconds)
        self._timestamps: deque[float] = deque()
        self._last_call_monotonic: float = 0.0
        self._cooldown_until: float = 0.0
        self._lock = threading.Lock()

    @property
    def max_rpm(self) -> int:
        with self._lock:
            return self._max_rpm

    @property
    def min_interval_seconds(self) -> float:
        with self._lock:
            return self._min_interval

    def update_config(self, max_rpm: int, min_interval_seconds: float) -> None:
        """Passt Grenzwerte dynamisch aus der aktiven DomainConfig an."""
        with self._lock:
            self._max_rpm = max(1, max_rpm)
            self._min_interval = 0.0 if _is_test_environment() else max(0.0, min_interval_seconds)

    def notify_rate_limit(self, cooldown_seconds: float = DEFAULT_429_COOLDOWN_SECONDS) -> None:
        """Aktiviert nach HTTP 429 eine Pause, damit das Zeitfenster bei Google abfließen kann."""
        if _is_test_environment():
            return
        with self._lock:
            target = time.monotonic() + max(1.0, cooldown_seconds)
            if target > self._cooldown_until:
                self._cooldown_until = target
                logger.warning(
                    "API-Rate-Limit gemeldet. Aktiviere Cooldown für %.1fs.", cooldown_seconds
                )

    def acquire(self, cancel_check: Callable[[], None] | None = None) -> None:
        """Wartet kooperativ, bis ein Token im rollenden 60s-Fenster und der Mindestabstand frei sind."""
        if _is_test_environment():
            if cancel_check is not None:
                cancel_check()
            return

        while True:
            if cancel_check is not None:
                cancel_check()

            wait_needed = 0.0
            with self._lock:
                now = time.monotonic()
                if now < self._cooldown_until:
                    wait_needed = self._cooldown_until - now
                else:
                    window_start = now - 60.0
                    while self._timestamps and self._timestamps[0] <= window_start:
                        self._timestamps.popleft()

                    if len(self._timestamps) >= self._max_rpm:
                        wait_needed = max(wait_needed, self._timestamps[0] + 60.0 - now)

                    interval_remaining = (self._last_call_monotonic + self._min_interval) - now
                    if interval_remaining > 0:
                        wait_needed = max(wait_needed, interval_remaining)

                    if wait_needed <= 0.001:
                        self._timestamps.append(now)
                        self._last_call_monotonic = now
                        return

            sleep_with_cancel_check(max(0.1, wait_needed), cancel_check)

    def get_status(self) -> dict[str, float | int | bool]:
        """Liefert Diagnose-Statistiken für Monitoring und UI."""
        with self._lock:
            now = time.monotonic()
            window_start = now - 60.0
            recent_count = sum(1 for ts in self._timestamps if ts > window_start)
            cooldown_active = now < self._cooldown_until
            remaining_cooldown = max(0.0, self._cooldown_until - now) if cooldown_active else 0.0
            return {
                "max_rpm": self._max_rpm,
                "current_window_rpm": recent_count,
                "min_interval_seconds": self._min_interval,
                "cooldown_active": cooldown_active,
                "cooldown_remaining_seconds": round(remaining_cooldown, 1),
            }

    def reset(self) -> None:
        """Setzt den Verlauf zurück (nur für Tests)."""
        with self._lock:
            self._timestamps.clear()
            self._last_call_monotonic = 0.0
            self._cooldown_until = 0.0


_GLOBAL_RATE_LIMITER = LlmRateLimiter()


def get_global_rate_limiter() -> LlmRateLimiter:
    """Liefert die prozessweit geteilte Rate-Limiter-Instanz."""
    return _GLOBAL_RATE_LIMITER
