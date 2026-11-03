"""Orchestrator: Steuert Recherche-Läufe inkl. Checkpointing, Auto-Resume, Heartbeat & Abbruch."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Protocol

from src.config.constants import (
    AUTO_RESUME_COOLDOWN_SECONDS,
    DEPTH_PROFILES,
    HEARTBEAT_INTERVAL_SECONDS,
    MAX_AUTO_JOB_RESUMES,
)
from src.config.settings import AppSettings
from src.domain.enums import JobPhase
from src.domain.exceptions import JobCancelledError, ResearchBotError
from src.domain.models import DepthProfile, ResearchJob, ResearchPlan, ResearchReport
from src.research.gemini_client import ResearchLLMGateway
from src.research.llm_helpers import sleep_with_cancel_check
from src.research.planner import create_research_plan
from src.research.subtopic_runner import execute_subtopics_batch
from src.research.synthesizer import synthesize_final_report
from src.research.web_fetcher import SafeWebFetcher
from src.storage.base_repository import ResearchRepository

logger = logging.getLogger(__name__)


class ReportNotifier(Protocol):
    """Entkoppelte Benachrichtigungsschnittstelle für fertiggestellte Berichte."""

    def notify_report_ready(self, job: ResearchJob, report: ResearchReport) -> bool: ...


def _calculate_elapsed_seconds(started_at_iso: str | None) -> int:
    if not started_at_iso:
        return 1
    try:
        started_dt = datetime.fromisoformat(started_at_iso)
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=UTC)
        return max(1, int((datetime.now(UTC) - started_dt).total_seconds()))
    except ValueError:
        return 1


class ResearchOrchestrator:
    """Führt Recherche-Aufträge absturzsicher, selbstheilend und wiederaufnehmbar aus."""

    def __init__(
        self,
        settings: AppSettings,
        repository: ResearchRepository,
        llm_gateway: ResearchLLMGateway,
        web_fetcher: SafeWebFetcher,
        notifier: ReportNotifier | None = None,
        external_cancel_event: threading.Event | None = None,
    ) -> None:
        self._settings = settings
        self._repo = repository
        self._llm = llm_gateway
        self._fetcher = web_fetcher
        self._notifier = notifier
        self._cancel_event = external_cancel_event

    def execute_job(self, job_id: str, worker_id: str = "worker") -> ResearchReport | None:
        """Beansprucht und verarbeitet einen Auftrag inkl. automatischer Selbstheilung ab Checkpoint."""
        claimed = self._repo.claim_job(
            job_id=job_id,
            worker_id=worker_id,
            stale_timeout_minutes=self._settings.stale_job_timeout_minutes,
        )
        if not claimed:
            logger.info("Job '%s' wird bereits verarbeitet oder ist nicht mehr aktiv.", job_id)
            return None

        stop_hb = threading.Event()
        hb_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(job_id, stop_hb),
            daemon=True,
            name=f"hb-{job_id}",
        )
        hb_thread.start()

        try:
            return self._execute_with_auto_resume(job_id)
        finally:
            stop_hb.set()
            hb_thread.join(timeout=1.0)

    def _heartbeat_loop(self, job_id: str, stop_event: threading.Event) -> None:
        while not stop_event.wait(timeout=HEARTBEAT_INTERVAL_SECONDS):
            try:
                if self._is_cancelled_fast(job_id):
                    return
                self._repo.touch_heartbeat(job_id)
            except Exception as exc:
                logger.debug("Heartbeat-Tick für '%s' übersprungen: %s", job_id, exc)

    def _execute_with_auto_resume(self, job_id: str) -> ResearchReport | None:
        for resume_idx in range(MAX_AUTO_JOB_RESUMES + 1):
            job = self._repo.get_job(job_id)
            if job is None:
                return None
            is_final_try = resume_idx >= MAX_AUTO_JOB_RESUMES
            try:
                report = self._run_pipeline(job, allow_partial_on_error=is_final_try)
                self._send_notification_if_configured(job.id, report)
                return report
            except JobCancelledError:
                logger.info("Job '%s' wurde durch Nutzer abgebrochen.", job_id)
                self._repo.mark_job_cancelled(job_id)
                return None
            except ResearchBotError as exc:
                if self._is_cancelled_fast(job_id):
                    self._repo.mark_job_cancelled(job_id)
                    return None
                if not is_final_try:
                    self._schedule_in_worker_resume(job_id, resume_idx + 1, exc)
                    continue
                logger.error("Fachlicher Fehler in Job '%s' nach Auto-Resumes: %s", job_id, exc)
                self._repo.mark_job_failed(job_id, str(exc))
                return None
            except Exception as exc:
                if self._is_cancelled_fast(job_id):
                    self._repo.mark_job_cancelled(job_id)
                    return None
                logger.exception("Unerwarteter Fehler in Job '%s'", job_id)
                self._repo.mark_job_failed(
                    job_id,
                    f"Interner Verarbeitungsfehler ({type(exc).__name__}). Siehe Server-Logs.",
                )
                return None
        return None

    def _schedule_in_worker_resume(self, job_id: str, attempt_no: int, exc: Exception) -> None:
        wait_sec = AUTO_RESUME_COOLDOWN_SECONDS * attempt_no
        current_pct = (self._repo.get_job(job_id) or ResearchJob(id=job_id, topic="...")).progress_percent
        logger.warning(
            "Job '%s' stieß auf temporäre Störung (%s) – automatischer Wiederanlauf %d/%d in %.1fs...",
            job_id,
            exc,
            attempt_no,
            MAX_AUTO_JOB_RESUMES,
            wait_sec,
        )
        self._repo.update_progress(
            job_id=job_id,
            phase=JobPhase.INVESTIGATING,
            progress_percent=max(10, current_pct),
            message=(
                f"Vorübergehende API-Auslastung erkannt – setze automatisch vom letzten "
                f"Checkpoint fort (Versuch {attempt_no}/{MAX_AUTO_JOB_RESUMES})..."
            ),
        )
        sleep_with_cancel_check(wait_sec, lambda: self._check_alive(job_id))

    def _is_cancelled_fast(self, job_id: str) -> bool:
        if self._cancel_event is not None and self._cancel_event.is_set():
            return True
        return self._repo.is_cancellation_requested(job_id)

    def _check_alive(self, job_id: str) -> None:
        if self._is_cancelled_fast(job_id):
            raise JobCancelledError(f"Abbruch für Job '{job_id}' angefordert.")
        self._repo.touch_heartbeat(job_id)

    def _bind_gateway(self, job: ResearchJob) -> ResearchLLMGateway:
        job_model = getattr(job, "model", None) or self._settings.gemini_fast_model
        if hasattr(self._llm, "with_preferred_model"):
            try:
                return self._llm.with_preferred_model(
                    job_model,
                    cancel_check=lambda: self._check_alive(job.id),
                )
            except TypeError:
                return self._llm.with_preferred_model(job_model)
        return self._llm

    def _run_pipeline(self, job: ResearchJob, allow_partial_on_error: bool = False) -> ResearchReport:
        profile = DEPTH_PROFILES[job.depth]
        checkpoint = self._repo.load_checkpoint(job.id)
        self._check_alive(job.id)

        llm = self._bind_gateway(job)
        plan = checkpoint.plan or self._run_planning_step(job, profile, llm)
        baseline_md = checkpoint.baseline_markdown or self._run_baseline_step(job, profile, llm)
        findings = execute_subtopics_batch(
            repository=self._repo,
            gateway=llm,
            web_fetcher=self._fetcher,
            job=job,
            plan=plan,
            profile=profile,
            existing_findings=checkpoint.findings,
            baseline_markdown=baseline_md,
            check_alive=lambda: self._check_alive(job.id),
            allow_partial_on_error=allow_partial_on_error,
        )

        self._check_alive(job.id)
        self._repo.update_progress(
            job_id=job.id,
            phase=JobPhase.SYNTHESIZING,
            progress_percent=88,
            message="Synthetisiere Gesamtbericht, Executive Summary und Quellenverzeichnis...",
        )

        refreshed_job = self._repo.get_job(job.id) or job
        job_model = getattr(job, "model", None) or self._settings.gemini_fast_model
        model_label = (
            profile.deep_agent_name
            if (profile.use_deep_research_agent and self._settings.enable_deep_research_agent)
            else job_model
        )
        report = synthesize_final_report(
            gateway=llm,
            job=refreshed_job,
            plan=plan,
            findings=findings,
            baseline_markdown=baseline_md,
            model_used=model_label,
            duration_seconds=_calculate_elapsed_seconds(refreshed_job.started_at),
        )
        self._check_alive(job.id)
        self._repo.mark_job_completed(job.id, report)
        return report

    def _run_planning_step(
        self,
        job: ResearchJob,
        profile: DepthProfile,
        gateway: ResearchLLMGateway,
    ) -> ResearchPlan:
        self._repo.update_progress(
            job_id=job.id,
            phase=JobPhase.PLANNING,
            progress_percent=8,
            message=f"Erstelle Recherche-Plan ({profile.subtopic_count} Teilgebiete)...",
        )
        plan = create_research_plan(gateway, job, profile)
        self._check_alive(job.id)
        self._repo.save_plan_checkpoint(job.id, plan)
        return plan

    def _run_baseline_step(
        self,
        job: ResearchJob,
        profile: DepthProfile,
        gateway: ResearchLLMGateway,
    ) -> str:
        if not (profile.use_deep_research_agent and self._settings.enable_deep_research_agent):
            return ""
        self._repo.update_progress(
            job_id=job.id,
            phase=JobPhase.DEEP_BASELINE,
            progress_percent=15,
            message=f"Führe autonome Deep-Research-Voranalyse ({profile.deep_agent_name}) aus...",
        )
        baseline = gateway.run_deep_research_agent(
            prompt=f"Recherchiere umfassend zum Thema: {job.topic}\n{job.focus_instructions}",
            agent_name=profile.deep_agent_name,
            on_poll_tick=lambda: self._check_alive(job.id),
        )
        self._check_alive(job.id)
        self._repo.save_baseline_checkpoint(job.id, baseline.text)
        return baseline.text

    def _send_notification_if_configured(self, job_id: str, report: ResearchReport) -> None:
        if self._notifier is None or self._is_cancelled_fast(job_id):
            return
        completed_job = self._repo.get_job(job_id)
        if completed_job is None:
            return
        recipient = completed_job.notify_email or self._settings.email_to
        if recipient and self._notifier.notify_report_ready(completed_job, report):
            self._repo.mark_job_email_sent(job_id)
