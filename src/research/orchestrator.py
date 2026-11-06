"""Orchestrator: Steuert Recherche-Läufe inkl. Live-Aktionsanzeige, Zeitbudget & Teilergebnis."""

from __future__ import annotations

import contextlib
import logging
import threading
from datetime import UTC, datetime
from typing import Protocol

from src.config.constants import (
    AUTO_RESUME_COOLDOWN_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    MAX_AUTO_JOB_RESUMES,
    QUICK_RESUME_COOLDOWN_SECONDS,
)
from src.config.domain_loader import render_prompt_template
from src.config.settings import AppSettings
from src.domain.domain_config import DomainConfig
from src.domain.enums import JobPhase, ResearchDepth
from src.domain.exceptions import JobCancelledError, ResearchBotError
from src.domain.models import DepthProfile, ResearchJob, ResearchPlan, ResearchReport
from src.research.gemini_client import ResearchLLMGateway
from src.research.llm_helpers import is_quota_error, sleep_with_cancel_check
from src.research.planner import create_research_plan, resolve_max_subtopics
from src.research.rate_limiter import get_global_rate_limiter
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
    """Führt Recherche-Aufträge absturzsicher, zeitbudgetiert und teilergebnisfähig aus."""

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
        """Beansprucht und verarbeitet einen Auftrag inkl. Live-Status & Selbstheilung."""
        domain_cfg = self._repo.get_domain_config()
        claimed = self._repo.claim_job(
            job_id=job_id,
            worker_id=worker_id,
            stale_timeout_minutes=domain_cfg.stale_job_timeout_minutes,
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
        first_job = self._repo.get_job(job_id)
        if first_job is None:
            return None

        max_resumes = MAX_AUTO_JOB_RESUMES

        for resume_idx in range(max_resumes + 1):
            job = self._repo.get_job(job_id)
            if job is None:
                return None
            is_final_try = resume_idx >= max_resumes
            try:
                report = self._run_pipeline(
                    job,
                    allow_partial_on_error=is_final_try,
                    deadline_monotonic=None,
                )
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
                if not is_final_try and not is_quota_error(exc):
                    self._schedule_in_worker_resume(job, resume_idx + 1, max_resumes, exc)
                    continue
                logger.error("Fachlicher Fehler in Job '%s': %s", job_id, exc)
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

    def _schedule_in_worker_resume(
        self,
        job: ResearchJob,
        attempt_no: int,
        max_resumes: int,
        exc: Exception,
    ) -> None:
        wait_sec = (
            QUICK_RESUME_COOLDOWN_SECONDS
            if job.depth == ResearchDepth.QUICK
            else AUTO_RESUME_COOLDOWN_SECONDS * attempt_no
        )
        logger.warning("Job '%s' temporäre Störung (%s) – Auto-Resume %d/%d...", job.id, exc, attempt_no, max_resumes)
        self._repo.update_progress(
            job_id=job.id,
            phase=JobPhase.INVESTIGATING,
            progress_percent=max(10, job.progress_percent),
            message=f"Temporäre API-Spitze – setze ab Checkpoint fort ({attempt_no}/{max_resumes})...",
            live_status_override="🔄 Starte sofortigen Wiederanlauf für offene Teilgebiete...",
        )
        sleep_with_cancel_check(wait_sec, lambda: self._check_alive(job.id))

    def _is_cancelled_fast(self, job_id: str) -> bool:
        if self._cancel_event is not None and self._cancel_event.is_set():
            return True
        return self._repo.is_cancellation_requested(job_id)

    def _check_alive(self, job_id: str) -> None:
        if self._is_cancelled_fast(job_id):
            raise JobCancelledError(f"Abbruch für Job '{job_id}' angefordert.")
        self._repo.touch_heartbeat(job_id)

    def _bind_gateway(
        self,
        job: ResearchJob,
        deadline_monotonic: float | None,
        domain_config: DomainConfig,
    ) -> ResearchLLMGateway:
        routing = getattr(domain_config, "model_routing", None)
        if routing is not None:
            get_global_rate_limiter().update_config(routing.max_rpm, routing.min_call_interval_seconds)

        job_model = getattr(job, "model", None)
        if not job_model and routing:
            job_model = routing.research_primary_model
        if not job_model:
            job_model = domain_config.default_model

        if hasattr(self._llm, "with_preferred_model"):
            try:
                return self._llm.with_preferred_model(
                    job_model,
                    cancel_check=lambda: self._check_alive(job.id),
                    on_action=lambda msg: self._repo.update_live_action(job.id, msg),
                    fast_mode=(job.depth == ResearchDepth.QUICK),
                    deadline_monotonic=deadline_monotonic,
                    language=job.language,
                    available_models=domain_config.available_models,
                )
            except TypeError:
                return self._llm.with_preferred_model(job_model)
        return self._llm

    def _run_pipeline(
        self,
        job: ResearchJob,
        allow_partial_on_error: bool = False,
        deadline_monotonic: float | None = None,
    ) -> ResearchReport:
        domain_config = self._repo.get_domain_config()
        profile = domain_config.get_depth_profile(job.depth)
        checkpoint = self._repo.load_checkpoint(job.id)
        self._check_alive(job.id)

        llm = self._bind_gateway(job, deadline_monotonic, domain_config)
        plan = checkpoint.plan or self._run_planning_step(job, profile, llm, domain_config)
        baseline_md = checkpoint.baseline_markdown or self._run_baseline_step(
            job, profile, llm, domain_config
        )
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
            deadline_monotonic=deadline_monotonic,
            domain_config=domain_config,
        )

        self._check_alive(job.id)
        is_partial = len(findings) < len(plan.subtopics)
        synth_log = (
            f"Erstelle Teilergebnis-Bericht ({len(findings)}/{len(plan.subtopics)} Teilgebiete ausgewertet)..."
            if is_partial
            else "Synthetisiere kompakten Bericht und Quellenverzeichnis..."
        )
        self._repo.update_progress(
            job_id=job.id,
            phase=JobPhase.SYNTHESIZING,
            progress_percent=88,
            message=synth_log,
            live_status_override="✍️ Verdichte Ergebnisse in einen knackigen Bericht...",
        )

        refreshed_job = self._repo.get_job(job.id) or job
        job_model = getattr(job, "model", None) or domain_config.default_model
        use_agent = (
            profile.use_deep_research_agent
            and self._settings.enable_deep_research_agent
            and domain_config.enable_deep_research_agent
        )
        routing = getattr(domain_config, "model_routing", None)
        synth_model = (
            profile.deep_agent_name
            if use_agent
            else (routing.synthesizer_model if routing else job_model)
        )
        model_label = synth_model
        synth_llm = self._bind_gateway(job, deadline_monotonic=None, domain_config=domain_config)
        if hasattr(synth_llm, "with_preferred_model") and not use_agent and routing:
            with contextlib.suppress(TypeError):
                synth_llm = synth_llm.with_preferred_model(synth_model)
        report = synthesize_final_report(
            gateway=synth_llm,
            job=refreshed_job,
            plan=plan,
            findings=findings,
            baseline_markdown=baseline_md,
            model_used=model_label,
            duration_seconds=_calculate_elapsed_seconds(refreshed_job.started_at),
            on_action=lambda msg: self._repo.update_live_action(job.id, msg),
            profile=profile,
            domain_config=domain_config,
        )
        self._check_alive(job.id)
        completion_note = (
            f"Mit Teilergebnis abgeschlossen ({len(findings)}/{len(plan.subtopics)} Teilgebiete)."
            if is_partial
            else None
        )
        self._repo.mark_job_completed(job.id, report, status_note=completion_note)
        return report

    def _run_planning_step(
        self,
        job: ResearchJob,
        profile: DepthProfile,
        gateway: ResearchLLMGateway,
        domain_config: DomainConfig,
    ) -> ResearchPlan:
        max_subtopics = resolve_max_subtopics(job, profile, domain_config)
        self._repo.update_progress(
            job_id=job.id,
            phase=JobPhase.PLANNING,
            progress_percent=8,
            message=f"Erstelle Recherche-Plan (bis zu {max_subtopics} Teilgebiete)...",
            live_status_override=f"🧠 Entwerfe schlanke Suchstrategie (max. {max_subtopics} Fokusfragen)...",
        )
        routing = getattr(domain_config, "model_routing", None)
        plan_gateway = gateway
        if routing and hasattr(gateway, "with_preferred_model"):
            with contextlib.suppress(TypeError):
                plan_gateway = gateway.with_preferred_model(routing.planning_model)
        plan = create_research_plan(plan_gateway, job, profile, domain_config=domain_config)
        self._check_alive(job.id)
        self._repo.save_plan_checkpoint(job.id, plan)
        return plan

    def _run_baseline_step(
        self,
        job: ResearchJob,
        profile: DepthProfile,
        gateway: ResearchLLMGateway,
        domain_config: DomainConfig,
    ) -> str:
        agent_enabled = (
            profile.use_deep_research_agent
            and self._settings.enable_deep_research_agent
            and domain_config.enable_deep_research_agent
        )
        if not agent_enabled:
            return ""
        self._repo.update_progress(
            job_id=job.id,
            phase=JobPhase.DEEP_BASELINE,
            progress_percent=15,
            message=f"Führe autonome Deep-Research-Voranalyse ({profile.deep_agent_name}) aus...",
            live_status_override=f"🔬 Deep-Research-Agent ({profile.deep_agent_name}) recherchiert im Hintergrund...",
        )
        focus_block = job.focus_instructions.strip()
        baseline_prompt = render_prompt_template(
            domain_config.prompts.deep_baseline_prompt,
            {"topic": job.topic, "focus_block": focus_block},
        )
        try:
            baseline = gateway.run_deep_research_agent(
                prompt=baseline_prompt,
                agent_name=profile.deep_agent_name,
                on_poll_tick=lambda: self._check_alive(job.id),
                fallback_system_instruction=domain_config.prompts.deep_baseline_fallback_system_prompt,
            )
        except TypeError:
            baseline = gateway.run_deep_research_agent(
                prompt=baseline_prompt,
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
