"""Tab 2: Live-Monitoring laufender Jobs inkl. Live-Aktionsanzeige, Sofort-Stopp & Direkt-Löschung."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from src.config.constants import UI_LIVE_REFRESH_INTERVAL
from src.config.domain_loader import load_default_domain_config
from src.config.settings import AppSettings
from src.domain.domain_config import DomainConfig
from src.domain.enums import AuthRole, JobStatus
from src.domain.models import ResearchJob
from src.notifications.email_renderer import format_duration_human
from src.research.llm_helpers import normalize_preferred_model
from src.storage.base_repository import ResearchRepository
from src.ui.date_formatter import format_datetime_german
from src.worker.dispatcher import JobDispatcher

ACTIVE_JOB_STATUSES: tuple[JobStatus, ...] = (JobStatus.RUNNING, JobStatus.QUEUED)
RECENT_FINISHED_LIMIT: int = 10

_STATUS_BADGES: dict[JobStatus, str] = {
    JobStatus.QUEUED: "🕒 Warteschlange",
    JobStatus.RUNNING: "⚙️ Läuft im Hintergrund",
    JobStatus.COMPLETED: "✅ Abgeschlossen",
    JobStatus.FAILED: "❌ Fehlgeschlagen",
    JobStatus.CANCELLED: "⏹️ Abgebrochen",
}


def render_active_jobs_view(
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
    domain_config: DomainConfig | None = None,
) -> None:
    """Zeigt aktive Jobs; schaltet Auto-Refresh nur ein, wenn tatsächlich Jobs aktiv sind."""
    flash_msg = st.session_state.pop("_jobs_tab_flash", None)
    if flash_msg:
        st.success(str(flash_msg))

    cfg = domain_config or repository.get_domain_config() or load_default_domain_config()
    active_count = repository.count_jobs(statuses=ACTIVE_JOB_STATUSES)
    if active_count > 0:
        _render_live_jobs_fragment(settings=settings, repository=repository, role=role, cfg=cfg)
    else:
        _render_idle_jobs_view(settings=settings, repository=repository, role=role, cfg=cfg)


def _render_idle_jobs_view(
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
    cfg: DomainConfig,
) -> None:
    """Rendert die Ruheansicht ohne periodische Polling-WebSockets (verhindert 2s-Flackern)."""
    dispatcher = JobDispatcher(settings)
    dispatcher.auto_recover_stale_jobs(repository)

    col_head, col_btn = st.columns([4, 1], vertical_alignment="center")
    with col_head:
        st.caption("Keine Hintergrund-Jobs aktiv. Auto-Refresh pausiert.")
    with col_btn:
        if st.button("Jetzt prüfen", key="idle_check_btn", width="stretch"):
            st.rerun()

    st.info("Aktuell laufen keine Hintergrund-Recherchen.")

    recent_finished = repository.list_jobs(
        statuses=(JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.COMPLETED),
        limit=RECENT_FINISHED_LIMIT,
    )
    if recent_finished:
        _render_recent_finished_section(
            recent_finished, settings, repository, dispatcher, role, cfg, has_active=False
        )


@st.fragment(run_every=UI_LIVE_REFRESH_INTERVAL)
def _render_live_jobs_fragment(
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
    cfg: DomainConfig,
) -> None:
    """Rendert die Live-Aktualisierung (alle 3s) ausschließlich bei aktiven Jobs."""
    dispatcher = JobDispatcher(settings)
    dispatcher.auto_recover_stale_jobs(repository)

    total_active_count = repository.count_jobs(statuses=ACTIVE_JOB_STATUSES)
    if total_active_count == 0:
        st.rerun()
        return

    col_head, col_btn = st.columns([4, 1], vertical_alignment="center")
    with col_head:
        st.caption("🔄 Aktualisiert sich automatisch alle 3 Sekunden (inkl. Live-Aktionsanzeige).")
    with col_btn:
        if st.button("Jetzt prüfen", key="live_check_btn", width="stretch"):
            st.rerun(scope="fragment")

    active_jobs = repository.list_jobs(statuses=ACTIVE_JOB_STATUSES, limit=50)
    st.subheader(f"Aktive Recherchen ({total_active_count})")
    for job in active_jobs:
        _render_active_job_card(
            job=job,
            settings=settings,
            repository=repository,
            dispatcher=dispatcher,
            role=role,
            cfg=cfg,
        )

    recent_finished = repository.list_jobs(
        statuses=(JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.COMPLETED),
        limit=RECENT_FINISHED_LIMIT,
    )
    if recent_finished:
        _render_recent_finished_section(
            recent_finished, settings, repository, dispatcher, role, cfg, has_active=True
        )


def _render_recent_finished_section(
    recent_finished: list[ResearchJob],
    settings: AppSettings,
    repository: ResearchRepository,
    dispatcher: JobDispatcher,
    role: AuthRole,
    cfg: DomainConfig,
    has_active: bool,
) -> None:
    """Zeigt die letzten bis zu 10 Recherchen mit schlanker Status-, Laufzeit- und Modellzeile."""
    with st.expander("Letzte Recherchen", expanded=not has_active):
        has_failed_or_cancelled = any(
            j.status in {JobStatus.FAILED, JobStatus.CANCELLED} for j in recent_finished
        )
        if (
            role == AuthRole.ADMIN
            and has_failed_or_cancelled
            and st.button(
                "🧹 Fehlgeschlagene & abgebrochene Einträge entfernen",
                key="purge_failed_cancelled_jobs",
            )
        ):
            repository.delete_jobs_by_status((JobStatus.CANCELLED, JobStatus.FAILED))
            st.rerun()

        for job in recent_finished:
            _render_recent_job_card(
                job=job,
                settings=settings,
                repository=repository,
                dispatcher=dispatcher,
                role=role,
                cfg=cfg,
            )


def _compute_job_duration_label(job: ResearchJob) -> str:
    """Berechnet die tatsächliche Laufzeit des Auftrags in menschenlesbarem Format."""
    start_iso = job.started_at or job.created_at
    if start_iso and job.completed_at:
        try:
            start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(job.completed_at.replace("Z", "+00:00"))
            diff_seconds = int((end_dt - start_dt).total_seconds())
            if diff_seconds >= 0:
                return format_duration_human(diff_seconds)
        except (ValueError, TypeError):
            pass
    return "-"


def _render_recent_job_card(
    job: ResearchJob,
    settings: AppSettings,
    repository: ResearchRepository,
    dispatcher: JobDispatcher,
    role: AuthRole,
    cfg: DomainConfig,
) -> None:
    """Rendert eine kompakte Karte für vergangene Recherchen ohne Anzeige der internen ID."""
    _ = dispatcher
    profile = cfg.get_depth_profile(job.depth)
    job_model = normalize_preferred_model(
        getattr(job, "model", None) or cfg.default_model,
        allowed_models=cfg.available_models,
    )
    _fmt_key, fmt_preset = cfg.resolve_effective_format(
        job.topic, job.focus_instructions, getattr(job, "report_format", "compact")
    )
    date_str = format_datetime_german(job.created_at)
    is_completed = job.status == JobStatus.COMPLETED
    completed_str = "Abgeschlossen: Ja" if is_completed else "Abgeschlossen: Nein"
    duration_str = _compute_job_duration_label(job)

    with st.container(border=True):
        col_main, col_actions = st.columns([3.6, 1.4], vertical_alignment="center")
        with col_main:
            st.markdown(f"**{job.topic}**")
            st.caption(
                f"{date_str} · {completed_str} · Laufzeit: {duration_str} · {profile.label} · {fmt_preset.label} · 🤖 `{job_model}`"
            )
        with col_actions:
            _render_finished_job_actions(
                job=job,
                settings=settings,
                repository=repository,
                role=role,
                is_completed=is_completed,
            )

        if job.error_message and not is_completed:
            st.error(f"Fehler: {job.error_message}")


def _render_finished_job_actions(
    job: ResearchJob,
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
    is_completed: bool,
) -> None:
    """Verwaltungsaktionen für abgeschlossene oder abgebrochene Recherchen."""
    _ = settings
    if is_completed:
        col_view, col_del = st.columns([1.2, 1.0])
        with col_view:
            if st.button("📄 Bericht", key=f"view_rep_{job.id}", width="stretch"):
                st.query_params["tab"] = "reports"
                st.query_params["job_id"] = job.id
                st.session_state["_force_nav_tab"] = "reports"
                st.rerun()
        with col_del:
            if role == AuthRole.ADMIN and st.button(
                "🗑️",
                key=f"del_done_{job.id}",
                help="Auftrag & Bericht löschen",
                width="stretch",
            ):
                repository.delete_job_and_report(job.id)
                st.rerun()
        return

    if role == AuthRole.ADMIN:
        col_res, col_del = st.columns(2)
        with col_res:
            if st.button("🔁", key=f"retry_{job.id}", help="Auftrag fortsetzen", width="stretch"):
                _resume_interrupted_job(job, settings, repository)
                _stay_on_jobs_tab_and_rerun()
        with col_del:
            if st.button("🗑️", key=f"del_fin_{job.id}", help="Auftrag löschen", width="stretch"):
                repository.delete_job_and_report(job.id)
                st.rerun()


def _render_active_job_card(
    job: ResearchJob,
    settings: AppSettings,
    repository: ResearchRepository,
    dispatcher: JobDispatcher,
    role: AuthRole,
    cfg: DomainConfig,
) -> None:
    """Rendert die Live-Karte eines aktuell laufenden Auftrags ohne Anzeige der internen ID."""
    profile = cfg.get_depth_profile(job.depth)
    badge = _STATUS_BADGES.get(job.status, job.status.value)
    job_model = normalize_preferred_model(
        getattr(job, "model", None) or cfg.default_model,
        allowed_models=cfg.available_models,
    )
    _fmt_key, fmt_preset = cfg.resolve_effective_format(
        job.topic, job.focus_instructions, getattr(job, "report_format", "compact")
    )
    date_str = format_datetime_german(job.created_at)

    with st.container(border=True):
        top_left, top_right = st.columns([3.4, 1.6], vertical_alignment="center")
        with top_left:
            st.markdown(f"**{job.topic}**")
            st.caption(f"{badge} · {date_str} · {profile.label} · {fmt_preset.label} · 🤖 `{job_model}`")
        with top_right:
            if role == AuthRole.ADMIN:
                _render_active_admin_actions(job, settings, repository, dispatcher)

        st.progress(job.progress_percent / 100.0, text=f"{job.progress_percent}% – {job.status_message}")

        if job.progress_log:
            with st.expander("Aktivitätsprotokoll anzeigen", expanded=False):
                for entry in reversed(job.progress_log[-15:]):
                    log_time = format_datetime_german(entry.timestamp_iso)
                    st.text(f"[{log_time}] ({entry.phase.value}) {entry.message}")


def _render_active_admin_actions(
    job: ResearchJob,
    settings: AppSettings,
    repository: ResearchRepository,
    dispatcher: JobDispatcher,
) -> None:
    _ = settings
    btn_stop, btn_del = st.columns(2)
    with btn_stop:
        if st.button(
            "⏹️ Stopp",
            key=f"cancel_{job.id}",
            width="stretch",
            help="Prozess sofort beenden",
        ):
            repository.request_cancellation(job.id)
            dispatcher.stop_job_execution(job.id, worker_id=job.worker_id)
            _stay_on_jobs_tab_and_rerun()
    with btn_del:
        if st.button(
            "🗑️ Löschen",
            key=f"del_active_{job.id}",
            width="stretch",
            help="Prozess beenden & Auftrag direkt löschen",
        ):
            repository.request_cancellation(job.id)
            dispatcher.stop_job_execution(job.id, worker_id=job.worker_id)
            repository.delete_job_and_report(job.id)
            _stay_on_jobs_tab_and_rerun()


def _stay_on_jobs_tab_and_rerun() -> None:
    st.session_state["_force_nav_tab"] = "jobs"
    st.rerun()


def _resume_interrupted_job(
    job: ResearchJob,
    settings: AppSettings,
    repository: ResearchRepository,
) -> None:
    """Reiht einen unterbrochenen Auftrag unter Erhalt seiner Checkpoints erneut ein."""
    if repository.requeue_job_from_checkpoint(job.id):
        dispatcher = JobDispatcher(settings)
        dispatcher.dispatch(job.id, depth=job.depth)
