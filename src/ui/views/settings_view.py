"""Tab 5: Kompakte Systemdiagnose, Infrastrukturstatus und Stale-Job-Recovery."""

from __future__ import annotations

import streamlit as st

from src.config.domain_loader import load_default_domain_config
from src.config.settings import AppSettings
from src.domain.domain_config import DomainConfig
from src.domain.enums import AuthRole
from src.storage.base_repository import ResearchRepository
from src.worker.dispatcher import JobDispatcher, resolve_effective_worker_mode


def render_settings_view(
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
    domain_config: DomainConfig | None = None,
) -> None:
    """Zeigt den aktuellen Verbindungsstatus aller Komponenten ohne Preisgabe von Secrets."""
    cfg = domain_config or repository.get_domain_config() or load_default_domain_config()
    effective_worker = resolve_effective_worker_mode(settings)

    col1, col2 = st.columns(2)
    with col1:
        with st.container(border=True):
            db_status = "🟢 Turso Cloud" if settings.has_turso_configured else "🟢 SQLite Local"
            st.markdown(f"**🗄️ Storage** · {db_status}")
            st.caption(f"Backend: `{repository.backend_name}`")

        with st.container(border=True):
            ai_status = "🟢 Ready" if settings.has_gemini_configured else "🔴 Key fehlt"
            st.markdown(f"**🧠 AI Engine** · {ai_status}")
            st.caption(f"Google GenAI · Primary `{cfg.model_routing.research_primary_model}`")

    with col2:
        with st.container(border=True):
            w_label = (
                f"🟢 GitHub Actions (`{settings.github_repo}`)"
                if effective_worker.value == "github"
                else "🟢 Local ThreadPool"
            )
            st.markdown(f"**⚙️ Worker** · {w_label}")
            st.caption(f"Mode: `{settings.worker_mode.value}` → `{effective_worker.value}`")

        with st.container(border=True):
            mail_status = "🟢 Ready" if settings.has_email_configured else "⚪ Off"
            st.markdown(f"**📧 Notifications** · {mail_status}")
            st.caption(f"Provider: `{settings.email_provider.value}`")

    if role == AuthRole.ADMIN:
        claimable = repository.find_claimable_job_ids(
            stale_timeout_minutes=cfg.stale_job_timeout_minutes,
            limit=10,
        )
        with st.container(border=True):
            c_info, c_btn = st.columns([3.5, 1.3], vertical_alignment="center")
            with c_info:
                st.markdown(f"**🛠️ Recovery** · Stale Jobs (>{cfg.stale_job_timeout_minutes}m): **{len(claimable)}**")
            with c_btn:
                if claimable and st.button("🔄 Stale Jobs starten", type="secondary", width="stretch"):
                    dispatcher = JobDispatcher(settings)
                    for job_id in claimable:
                        job = repository.get_job(job_id)
                        dispatcher.dispatch(job_id, depth=job.depth if job else None)
                    st.success(f"{len(claimable)} Job(s) neu gestartet.")

