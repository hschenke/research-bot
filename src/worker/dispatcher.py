"""Intelligenter Job-Dispatcher: Start, Sofort-Stopp und Auto-Recovery für Hintergrund-Jobs."""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from src.config.constants import AUTO_RECOVERY_CHECK_INTERVAL_SECONDS
from src.config.settings import AppSettings
from src.domain.enums import JobStatus, ResearchDepth, WorkerMode
from src.domain.exceptions import DispatchError
from src.storage.base_repository import ResearchRepository
from src.worker.github_dispatcher import GitHubActionsDispatcher
from src.worker.local_runner import get_shared_local_runner

logger = logging.getLogger(__name__)

_RECOVERY_LOCK = threading.Lock()
_LAST_RECOVERY_CHECK_MONOTONIC: float = 0.0


def resolve_effective_worker_mode(settings: AppSettings) -> WorkerMode:
    """Ermittelt den Standard-Worker-Modus (`GITHUB` bei Cloud-DB + Token, sonst `LOCAL`)."""
    if settings.worker_mode == WorkerMode.LOCAL:
        return WorkerMode.LOCAL
    if settings.worker_mode == WorkerMode.GITHUB:
        return WorkerMode.GITHUB
    if settings.has_github_worker_configured and settings.has_turso_configured:
        return WorkerMode.GITHUB
    return WorkerMode.LOCAL


def _is_queued_too_long(created_at_iso: str, min_age_seconds: int = 120) -> bool:
    """Prüft, ob ein `queued`-Auftrag ungewöhnlich lange unbeansprucht wartet."""
    if not created_at_iso:
        return True
    try:
        dt = datetime.fromisoformat(created_at_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return datetime.now(UTC) - dt > timedelta(seconds=min_age_seconds)
    except ValueError:
        return True


class JobDispatcher:
    """Übergibt Aufträge an Hintergrund-Worker, beendet Prozesse bei Stopp und heilt verwaiste Läufe."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._gh_dispatcher = GitHubActionsDispatcher(settings)

    def dispatch(self, job_id: str, depth: ResearchDepth | None = None) -> str:
        """
        Startet die Hintergrundverarbeitung:
        - Im `AUTO`-Modus läuft `QUICK` (Schnellüberblick) direkt lokal ohne VM-Boot-Verzögerung.
        - Längere Profile laufen über GitHub Actions (falls konfiguriert) mit lokalem Fallback.
        """
        if self._settings.worker_mode == WorkerMode.AUTO and depth == ResearchDepth.QUICK:
            runner = get_shared_local_runner(self._settings)
            runner.submit_job(job_id)
            return "Lokaler Turbo-Worker (Sofortstart ohne Cloud-Bootzeit)"

        effective_mode = resolve_effective_worker_mode(self._settings)
        if effective_mode == WorkerMode.GITHUB:
            try:
                self._gh_dispatcher.trigger_workflow(job_id)
                return "GitHub Actions Cloud-Worker (bis zu 5 Std. Laufzeit)"
            except DispatchError as exc:
                if self._settings.worker_mode == WorkerMode.GITHUB:
                    raise
                logger.warning("GitHub-Dispatch fehlgeschlagen (%s) – nutze lokalen Fallback.", exc)

        runner = get_shared_local_runner(self._settings)
        runner.submit_job(job_id)
        return "Lokaler Hintergrund-Worker (ThreadPool)"

    def stop_job_execution(
        self,
        job_id: str,
        worker_id: str = "",
        async_remote: bool = True,
    ) -> None:
        """Beendet laufende lokale Threads und aktive GitHub-Actions-Runs für `job_id`."""
        runner = get_shared_local_runner(self._settings)
        runner.cancel_job(job_id)

        if not (worker_id.startswith("gh-run-") and self._settings.has_github_worker_configured):
            return

        if async_remote:
            threading.Thread(
                target=self._gh_dispatcher.cancel_workflow_for_worker,
                args=(worker_id,),
                daemon=True,
                name=f"gh-cancel-{job_id}",
            ).start()
        else:
            self._gh_dispatcher.cancel_workflow_for_worker(worker_id)

    def auto_recover_stale_jobs(self, repository: ResearchRepository) -> int:
        """Prüft gedrosselt auf verwaiste Aufträge und setzt sie automatisch am Checkpoint fort."""
        global _LAST_RECOVERY_CHECK_MONOTONIC
        now_mono = time.monotonic()
        with _RECOVERY_LOCK:
            if now_mono - _LAST_RECOVERY_CHECK_MONOTONIC < AUTO_RECOVERY_CHECK_INTERVAL_SECONDS:
                return 0
            _LAST_RECOVERY_CHECK_MONOTONIC = now_mono

        repository.cleanup_stuck_cancellations()
        claimable_ids = repository.find_claimable_job_ids(
            stale_timeout_minutes=self._settings.stale_job_timeout_minutes,
            limit=3,
        )
        if not claimable_ids:
            return 0

        runner = get_shared_local_runner(self._settings)
        recovered = 0
        for candidate_id in claimable_ids:
            if runner.is_job_active_locally(candidate_id):
                continue
            job = repository.get_job(candidate_id)
            if job is None or job.cancel_requested:
                continue
            if job.status == JobStatus.QUEUED and not _is_queued_too_long(job.created_at):
                continue
            try:
                self.dispatch(job.id, depth=job.depth)
                recovered += 1
                logger.info("Verwaister Auftrag '%s' wurde automatisch wiederaufgenommen.", job.id)
            except DispatchError as exc:
                logger.warning("Auto-Recovery für '%s' fehlgeschlagen: %s", job.id, exc)
        return recovered
