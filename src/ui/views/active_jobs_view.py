"""Tab 2: Live-Monitoring laufender, wartender und fehlgeschlagener Hintergrund-Jobs."""

from __future__ import annotations

import streamlit as st

from src.config.constants import DEPTH_PROFILES
from src.config.settings import AppSettings
from src.domain.enums import AuthRole, JobStatus
from src.domain.models import ResearchJob
from src.storage.base_repository import ResearchRepository
from src.worker.dispatcher import JobDispatcher

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
    col_head, col_btn = st.columns([4, 1], vertical_alignment="center")
    with col_head:
        st.caption("🔄 Aktualisiert sich automatisch alle 10 Sekunden ohne Seiten-Reload.")
    with col_btn:
        if st.button("Jetzt prüfen", use_container_width=True):
            st.rerun(scope="fragment")

    active_jobs = repository.list_jobs(
        statuses=(JobStatus.RUNNING, JobStatus.QUEUED),
        limit=30,
    )
    recent_finished = repository.list_jobs(
        statuses=(JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.COMPLETED),
        limit=10,
    )

    if not active_jobs:
        st.info("Aktuell laufen keine Hintergrund-Recherchen.")
    else:
        st.subheader(f"Aktive Recherchen ({len(active_jobs)})")
        for job in active_jobs:
            _render_job_card(job=job, settings=settings, repository=repository, role=role, is_active=True)

    if recent_finished:
        with st.expander("📋 Letzte abgeschlossene / abgebrochene Läufe", expanded=not active_jobs):
            for job in recent_finished:
                _render_job_card(
                    job=job,
                    settings=settings,
                    repository=repository,
                    role=role,
                    is_active=False,
                )


def _render_job_card(
    job: ResearchJob,
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
    is_active: bool,
) -> None:
    profile = DEPTH_PROFILES[job.depth]
    badge = _STATUS_BADGES.get(job.status, job.status.value)

    with st.container(border=True):
        top_left, top_right = st.columns([4, 1], vertical_alignment="center")
        with top_left:
            job_model = getattr(job, "model", None) or "gemini-3.5-flash-lite"
            st.markdown(f"**{job.topic}**")
            st.caption(
                f"{badge} · ID `{job.id}` · 🤖 `{job_model}` · {profile.label} · Angelegt: `{job.created_at[:19]} UTC`"
            )
        with top_right:
            can_cancel = is_active and role == AuthRole.ADMIN and not job.cancel_requested
            can_retry = job.status == JobStatus.FAILED and role == AuthRole.ADMIN
            if can_cancel and st.button("⏹️ Stopp", key=f"cancel_{job.id}", use_container_width=True):
                repository.request_cancellation(job.id)
                st.rerun(scope="fragment")
            elif can_retry and st.button("🔁 Fortsetzen", key=f"retry_{job.id}", use_container_width=True):
                _resume_failed_job(job, settings, repository)
                st.rerun(scope="fragment")

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


def _resume_failed_job(
    job: ResearchJob,
    settings: AppSettings,
    repository: ResearchRepository,
) -> None:
    """Reiht einen unterbrochenen Auftrag unter Erhalt seiner Checkpoints erneut ein."""
    if repository.requeue_job_from_checkpoint(job.id):
        dispatcher = JobDispatcher(settings)
        dispatcher.dispatch(job.id)
