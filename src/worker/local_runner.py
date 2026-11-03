"""Verwalteter In-Process Hintergrund-Runner (ThreadPoolExecutor) für lokale Entwicklung & Fallback."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from src.config.settings import AppSettings
from src.notifications.notifier import EmailNotifier
from src.research.gemini_client import GeminiInteractionsGateway
from src.research.orchestrator import ResearchOrchestrator
from src.research.web_fetcher import SafeWebFetcher
from src.storage.factory import create_repository

logger = logging.getLogger(__name__)


class LocalBackgroundRunner:
    """Führt Recherche-Jobs nicht-blockierend in einem begrenzten Thread-Pool aus."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, settings.max_concurrent_local_jobs),
            thread_name_prefix="research-local-worker",
        )
        self._lock = threading.Lock()
        self._submitted_jobs: set[str] = set()

    def submit_job(self, job_id: str) -> bool:
        """Reicht `job_id` zur asynchronen Hintergrundausführung ein (idempotent)."""
        clean_id = job_id.strip()
        with self._lock:
            if clean_id in self._submitted_jobs:
                return False
            self._submitted_jobs.add(clean_id)

        self._executor.submit(self._run_isolated_job, clean_id)
        logger.info("Job '%s' an lokalen Hintergrund-Worker übergeben.", clean_id)
        return True

    def _run_isolated_job(self, job_id: str) -> None:
        repo = create_repository(self._settings, initialize=False)
        fetcher = SafeWebFetcher()
        try:
            gateway = GeminiInteractionsGateway(self._settings)
            notifier = EmailNotifier(self._settings)
            orchestrator = ResearchOrchestrator(
                settings=self._settings,
                repository=repo,
                llm_gateway=gateway,
                web_fetcher=fetcher,
                notifier=notifier,
            )
            orchestrator.execute_job(job_id=job_id, worker_id="local-thread")
        except Exception as exc:
            logger.exception("Lokaler Worker konnte Job '%s' nicht starten", job_id)
            repo.mark_job_failed(job_id, str(exc))
        finally:
            fetcher.close()
            repo.close()
            with self._lock:
                self._submitted_jobs.discard(job_id)


_GLOBAL_RUNNER_LOCK = threading.Lock()
_GLOBAL_RUNNER_INSTANCE: LocalBackgroundRunner | None = None


def get_shared_local_runner(settings: AppSettings) -> LocalBackgroundRunner:
    """Liefert eine prozessweite Singleton-Instanz, die Streamlit-Reruns stabil überlebt."""
    global _GLOBAL_RUNNER_INSTANCE
    with _GLOBAL_RUNNER_LOCK:
        if _GLOBAL_RUNNER_INSTANCE is None:
            _GLOBAL_RUNNER_INSTANCE = LocalBackgroundRunner(settings)
        return _GLOBAL_RUNNER_INSTANCE
