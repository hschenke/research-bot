"""Tab 2: Live-Monitoring laufender Jobs inkl. Sofort-Abbruch, Direkt-Löschung & Auto-Recovery."""

from __future__ import annotations

import streamlit as st

from src.config.constants import DEPTH_PROFILES
from src.config.settings import AppSettings
from src.domain.enums import AuthRole, JobStatus
from src.domain.models import ResearchJob
from src.storage.base_repository import ResearchRepository
from src.worker.dispatcher import JobDispatcher

ACTIVE_JOB_STATUSES: tuple[JobStatus, ...] = (JobStatus.RUNNING, JobStatus.QUEUED)

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
) -> None:
    """Zeigt aktive Jobs mit automatischer Teil-Aktualisierung (alle 10s via `st.fragment`)."""
    _render_live_jobs_fragment(settings=settings, repository=repository, role=role)


@st.fragment(run_every="10s")
def _render_live_jobs_fragment(
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
) -> None:
    dispatcher = JobDispatcher(settings)
    dispatcher.auto_recover_stale_jobs(repository)

    total_active_count = repository.count_jobs(statuses=ACTIVE_JOB_STATUSES)
    rendered_tab_count = st.session_state.get("_tab_active_count")
    if rendered_tab_count is not None and rendered_tab_count != total_active_count:
        st.rerun()

    col_head, col_btn = st.columns([4, 1], vertical_alignment="center")
    with col_head:
        st.caption("🔄 Aktualisiert sich automatisch alle 10 Sekunden ohne Seiten-Reload.")
    with col_btn:
        if st.button("Jetzt prüfen", use_container_width=True):
            st.rerun(scope="fragment")

    active_jobs = repository.list_jobs(statuses=ACTIVE_JOB_STATUSES, limit=50)
    recent_finished = repository.list_jobs(
        statuses=(JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.COMPLETED),
        limit=15,
    )

    if not active_jobs:
        st.info("Aktuell laufen keine Hintergrund-Recherchen.")
    else:
        st.subheader(f"Aktive Recherchen ({total_active_count})")
        for job in active_jobs:
            _render_job_card(
                job=job,
                settings=settings,
                repository=repository,
                dispatcher=dispatcher,
                role=role,
                is_active=True,
            )

    if recent_finished:
        _render_recent_finished_section(recent_finished, settings, repository, dispatcher, role, bool(active_jobs))


def _render_recent_finished_section(
    recent_finished: list[ResearchJob],
    settings: AppSettings,
    repository: ResearchRepository,
    dispatcher: JobDispatcher,
    role: AuthRole,
    has_active: bool,
) -> None:
    with st.expander("📋 Letzte abgeschlossene / abgebrochene Läufe", expanded=not has_active):
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
            st.rerun(scope="fragment")

        for job in recent_finished:
            _render_job_card(
                job=job,
                settings=settings,
                repository=repository,
                dispatcher=dispatcher,
                role=role,
                is_active=False,
            )


def _render_job_card(
    job: ResearchJob,
    settings: AppSettings,
    repository: ResearchRepository,
    dispatcher: JobDispatcher,
    role: AuthRole,
    is_active: bool,
) -> None:
    profile = DEPTH_PROFILES[job.depth]
    badge = _STATUS_BADGES.get(job.status, job.status.value)

    with st.container(border=True):
        top_left, top_right = st.columns([3.4, 1.6], vertical_alignment="center")
        with top_left:
            job_model = getattr(job, "model", None) or "gemini-3.5-flash-lite"
            st.markdown(f"**{job.topic}**")
            st.caption(
                f"{badge} · ID `{job.id}` · 🤖 `{job_model}` · {profile.label} · Angelegt: `{job.created_at[:19]} UTC`"
            )
        with top_right:
            if role == AuthRole.ADMIN:
                _render_admin_job_actions(job, settings, repository, dispatcher, is_active)

        if is_active:
            st.progress(job.progress_percent / 100.0, text=f"{job.progress_percent}% – {job.status_message}")
        elif job.error_message:
            st.error(f"Fehler: {job.error_message}")
        else:
            st.caption(job.status_message)

        if job.progress_log:
            with st.expander("Aktivitätsprotokoll anzeigen", expanded=False):
                for entry in reversed(job.progress_log[-15:]):
                    st.text(f"[{entry.timestamp_iso[11:19]}] ({entry.phase.value}) {entry.message}")


def _render_admin_job_actions(
    job: ResearchJob,
    settings: AppSettings,
    repository: ResearchRepository,
    dispatcher: JobDispatcher,
    is_active: bool,
) -> None:
    if is_active:
        btn_stop, btn_del = st.columns(2)
        with btn_stop:
            if st.button("⏹️ Stopp", key=f"cancel_{job.id}", use_container_width=True, help="Prozess sofort beenden"):
                repository.request_cancellation(job.id)
                dispatcher.stop_job_execution(job.id, worker_id=job.worker_id)
                st.rerun()
        with btn_del:
            if st.button("🗑️ Löschen", key=f"del_active_{job.id}", use_container_width=True, help="Prozess beenden & Auftrag direkt löschen"):
                repository.request_cancellation(job.id)
                dispatcher.stop_job_execution(job.id, worker_id=job.worker_id)
                repository.delete_job_and_report(job.id)
                st.rerun()
        return

    can_resume = job.status in {JobStatus.FAILED, JobStatus.CANCELLED}
    if can_resume:
        btn_res, btn_del = st.columns(2)
        with btn_res:
            if st.button("🔁 Fortsetzen", key=f"retry_{job.id}", use_container_width=True):
                _resume_interrupted_job(job, settings, repository)
                st.rerun()
        with btn_del:
            if st.button("🗑️ Löschen", key=f"del_fin_{job.id}", use_container_width=True):
                repository.delete_job_and_report(job.id)
                st.rerun(scope="fragment")
    else:
        if st.button("🗑️ Löschen", key=f"del_done_{job.id}", use_container_width=True):
            repository.delete_job_and_report(job.id)
            st.rerun(scope="fragment")


def _resume_interrupted_job(
    job: ResearchJob,
    settings: AppSettings,
    repository: ResearchRepository,
) -> None:
    """Reiht einen unterbrochenen Auftrag unter Erhalt seiner Checkpoints erneut ein."""
    if repository.requeue_job_from_checkpoint(job.id):
        dispatcher = JobDispatcher(settings)
        dispatcher.dispatch(job.id, depth=job.depth)
