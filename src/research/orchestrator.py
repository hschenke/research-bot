"""Orchestrator: Steuert mehrstufige & >1h Recherche-Läufe inkl. Checkpointing, Resume & Mail."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Protocol

from src.config.constants import DEPTH_PROFILES
from src.config.settings import AppSettings
from src.domain.enums import JobPhase
from src.domain.exceptions import JobCancelledError, ResearchBotError
from src.domain.models import (
    DepthProfile,
    ResearchJob,
    ResearchPlan,
    ResearchReport,
    SubTopicFinding,
)
from src.research.gemini_client import ResearchLLMGateway
from src.research.investigator import investigate_subtopic
from src.research.planner import create_research_plan
from src.research.synthesizer import synthesize_final_report
from src.research.web_fetcher import SafeWebFetcher
from src.storage.base_repository import ResearchRepository

logger = logging.getLogger(__name__)


class ReportNotifier(Protocol):
    """Entkoppelte Benachrichtigungsschnittstelle für fertiggestellte Berichte."""

    def notify_report_ready(self, job: ResearchJob, report: ResearchReport) -> bool:
        """Informiert den Nutzer per E-Mail über das fertige Recherche-Ergebnis."""
        ...


def _calculate_elapsed_seconds(started_at_iso: str | None) -> int:
    """Berechnet die Gesamtlaufzeit eines Auftrags ab dem ersten Startzeitpunkt."""
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
    """
    Führt Recherche-Aufträge absturzsicher und wiederaufnehmbar aus.
    Jeder Zwischenschritt wird sofort im `ResearchRepository` persistiert.
    """

    def __init__(
        self,
        settings: AppSettings,
        repository: ResearchRepository,
        llm_gateway: ResearchLLMGateway,
        web_fetcher: SafeWebFetcher,
        notifier: ReportNotifier | None = None,
    ) -> None:
        self._settings = settings
        self._repo = repository
        self._llm = llm_gateway
        self._fetcher = web_fetcher
        self._notifier = notifier

    def execute_job(self, job_id: str, worker_id: str = "worker") -> ResearchReport | None:
        """Beansprucht und verarbeitet einen Auftrag komplett von Planung bis Benachrichtigung."""
        claimed = self._repo.claim_job(
            job_id=job_id,
            worker_id=worker_id,
            stale_timeout_minutes=self._settings.stale_job_timeout_minutes,
        )
        if not claimed:
            logger.info("Job '%s' wird bereits verarbeitet oder ist nicht mehr aktiv.", job_id)
            return None

        job = self._repo.get_job(job_id)
        if job is None:
            return None

        try:
            report = self._run_pipeline(job)
            self._send_notification_if_configured(job.id, report)
            return report
        except JobCancelledError:
            logger.info("Job '%s' wurde regulär abgebrochen.", job_id)
            self._repo.mark_job_cancelled(job_id)
            return None
        except ResearchBotError as exc:
            logger.error("Fachlicher Fehler in Job '%s': %s", job_id, exc)
            self._repo.mark_job_failed(job_id, str(exc))
            return None
        except Exception as exc:
            logger.exception("Unerwarteter Fehler in Job '%s'", job_id)
            self._repo.mark_job_failed(
                job_id,
                f"Interner Verarbeitungsfehler ({type(exc).__name__}). Siehe Server-Logs.",
            )
            return None

    def _check_alive(self, job_id: str) -> None:
        """Aktualisiert den Heartbeat und wirft `JobCancelledError` bei Nutzerabbruch."""
        if self._repo.is_cancellation_requested(job_id):
            raise JobCancelledError(f"Abbruch für Job '{job_id}' angefordert.")
        self._repo.touch_heartbeat(job_id)

    def _run_pipeline(self, job: ResearchJob) -> ResearchReport:
        profile = DEPTH_PROFILES[job.depth]
        checkpoint = self._repo.load_checkpoint(job.id)
        self._check_alive(job.id)

        job_model = getattr(job, "model", None) or self._settings.gemini_fast_model
        llm = (
            self._llm.with_preferred_model(job_model)
            if hasattr(self._llm, "with_preferred_model")
            else self._llm
        )

        plan = checkpoint.plan or self._run_planning_step(job, profile, llm)
        baseline_md = checkpoint.baseline_markdown or self._run_baseline_step(job, profile, llm)
        findings = self._run_investigation_steps(
            job=job,
            plan=plan,
            profile=profile,
            existing_findings=checkpoint.findings,
            baseline_markdown=baseline_md,
            gateway=llm,
        )

        self._check_alive(job.id)
        self._repo.update_progress(
            job_id=job.id,
            phase=JobPhase.SYNTHESIZING,
            progress_percent=88,
            message="Synthetisiere Gesamtbericht, Executive Summary und Quellenverzeichnis...",
        )

        refreshed_job = self._repo.get_job(job.id) or job
        duration_sec = _calculate_elapsed_seconds(refreshed_job.started_at)
        model_label = (
            profile.deep_agent_name
            if (profile.use_deep_research_agent and self._settings.enable_deep_research_agent)
            else (job_model or self._settings.gemini_fast_model)
        )

        report = synthesize_final_report(
            gateway=llm,
            job=refreshed_job,
            plan=plan,
            findings=findings,
            baseline_markdown=baseline_md,
            model_used=model_label,
            duration_seconds=duration_sec,
        )
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
            progress_percent=6,
            message=f"Erstelle Recherche-Plan ({profile.subtopic_count} Teilgebiete)...",
        )
        plan = create_research_plan(gateway, job, profile)
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
            progress_percent=14,
            message=f"Führe autonome Deep-Research-Voranalyse ({profile.deep_agent_name}) aus...",
        )
        baseline = gateway.run_deep_research_agent(
            prompt=f"Recherchiere umfassend zum Thema: {job.topic}\n{job.focus_instructions}",
            agent_name=profile.deep_agent_name,
            on_poll_tick=lambda: self._check_alive(job.id),
        )
        self._repo.save_baseline_checkpoint(job.id, baseline.text)
        return baseline.text

    def _run_investigation_steps(
        self,
        job: ResearchJob,
        plan: ResearchPlan,
        profile: DepthProfile,
        existing_findings: list[SubTopicFinding],
        baseline_markdown: str,
        gateway: ResearchLLMGateway,
    ) -> list[SubTopicFinding]:
        completed_by_index: dict[int, SubTopicFinding] = {
            f.subtopic_index: f for f in existing_findings
        }
        total = max(1, len(plan.subtopics))

        for pos, subtopic in enumerate(plan.subtopics, start=1):
            self._check_alive(job.id)
            if subtopic.index in completed_by_index:
                continue

            pct = 20 + int(((pos - 1) / total) * 65)
            self._repo.update_progress(
                job_id=job.id,
                phase=JobPhase.INVESTIGATING,
                progress_percent=pct,
                message=f"Untersuche Teilgebiet {pos}/{total}: {subtopic.title}",
            )

            finding = investigate_subtopic(
                gateway=gateway,
                web_fetcher=self._fetcher,
                job=job,
                subtopic=subtopic,
                profile=profile,
                baseline_excerpt=baseline_markdown,
                on_heartbeat=lambda: self._check_alive(job.id),
            )
            completed_by_index[subtopic.index] = finding
            ordered = [completed_by_index[idx] for idx in sorted(completed_by_index)]
            self._repo.save_findings_checkpoint(job.id, ordered)

            if profile.inter_step_pause_seconds > 0 and pos < total:
                time.sleep(profile.inter_step_pause_seconds)

        return [completed_by_index[idx] for idx in sorted(completed_by_index)]

    def _send_notification_if_configured(self, job_id: str, report: ResearchReport) -> None:
        if self._notifier is None:
            return
        completed_job = self._repo.get_job(job_id)
        if completed_job is None:
            return
        recipient = completed_job.notify_email or self._settings.email_to
        if not recipient:
            return

        if self._notifier.notify_report_ready(completed_job, report):
            self._repo.mark_job_email_sent(job_id)
