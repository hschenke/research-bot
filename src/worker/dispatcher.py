"""Intelligenter Job-Dispatcher: Wählt automatisch zwischen GitHub Actions (>1h Cloud) und lokalem Thread-Pool."""

from __future__ import annotations

import logging

from src.config.settings import AppSettings
from src.domain.enums import WorkerMode
from src.domain.exceptions import DispatchError
from src.worker.github_dispatcher import GitHubActionsDispatcher
from src.worker.local_runner import get_shared_local_runner

logger = logging.getLogger(__name__)


def resolve_effective_worker_mode(settings: AppSettings) -> WorkerMode:
    """
    Ermittelt den tatsächlich genutzten Worker-Modus:
    - `GITHUB`: Nur sinnvoll, wenn sowohl GitHub-Credentials als auch Turso Cloud-DB aktiv sind.
    - `LOCAL`: Lokaler Hintergrund-ThreadPoolExecutor.
    """
    if settings.worker_mode == WorkerMode.LOCAL:
        return WorkerMode.LOCAL
    if settings.worker_mode == WorkerMode.GITHUB:
        return WorkerMode.GITHUB
    if settings.has_github_worker_configured and settings.has_turso_configured:
        return WorkerMode.GITHUB
    return WorkerMode.LOCAL


class JobDispatcher:
    """Übergibt neu angelegte Aufträge an den passenden Hintergrund-Mechanismus."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._gh_dispatcher = GitHubActionsDispatcher(settings)

    def dispatch(self, job_id: str) -> str:
        """
        Startet die Hintergrundverarbeitung und gibt eine kurze Modus-Beschreibung zurück.
        Fällt im `AUTO`-Modus bei vorübergehenden GitHub-API-Fehlern auf den lokalen Runner zurück.
        """
        effective_mode = resolve_effective_worker_mode(self._settings)

        if effective_mode == WorkerMode.GITHUB:
            try:
                self._gh_dispatcher.trigger_workflow(job_id)
                return "GitHub Actions Cloud-Worker (bis zu 5 Std. Laufzeit)"
            except DispatchError as exc:
                if self._settings.worker_mode == WorkerMode.GITHUB:
                    raise
                logger.warning(
                    "GitHub-Dispatch fehlgeschlagen (%s) – nutze lokalen Hintergrund-Worker als Fallback.",
                    exc,
                )

        runner = get_shared_local_runner(self._settings)
        runner.submit_job(job_id)
        return "Lokaler Hintergrund-Worker (ThreadPool)"
