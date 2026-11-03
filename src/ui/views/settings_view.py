"""Tab 4: Systemdiagnose, Infrastrukturstatus und Wartung verwaister Hintergrund-Jobs."""

from __future__ import annotations

import streamlit as st

from src.__version__ import __version__
from src.config.constants import DEFAULT_MODEL
from src.config.settings import AppSettings
from src.domain.enums import AuthRole
from src.storage.base_repository import ResearchRepository
from src.worker.dispatcher import JobDispatcher, resolve_effective_worker_mode


def render_settings_view(
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
) -> None:
    """Zeigt den aktuellen Verbindungsstatus aller Komponenten ohne Preisgabe von Secrets."""
    st.subheader("⚙️ System- & Infrastrukturstatus")

    effective_worker = resolve_effective_worker_mode(settings)
    col1, col2 = st.columns(2)

    with col1:
        with st.container(border=True):
            st.markdown("**🗄️ Persistenz-Backend**")
            st.write(f"Aktiv: `{repository.backend_name}`")
            if settings.has_turso_configured:
                st.success("Turso Cloud verbunden – Web-App & GitHub-Worker teilen denselben Zustand.")
            else:
                st.info("Lokale SQLite-Datenbank aktiv (`data/research_bot.db`).")

        with st.container(border=True):
            st.markdown("**🧠 KI-Engine (Google GenAI)**")
            st.write(f"Standard-Modell: `{DEFAULT_MODEL}`")
            if settings.has_gemini_configured:
                st.success("GEMINI_API_KEY ist hinterlegt (Modelle dynamisch abrufbar).")
            else:
                st.error("GEMINI_API_KEY fehlt.")

    with col2:
        with st.container(border=True):
            st.markdown("**⚙️ Hintergrund-Worker (> 1 Std. fähig)**")
            st.write(f"Konfiguriert: `{settings.worker_mode.value}` -> Aktiv: `{effective_worker.value}`")
            if effective_worker.value == "github":
                st.success(f"GitHub Actions Worker aktiv (`{settings.github_repo}`).")
            else:
                st.info(
                    "Lokaler ThreadPool-Worker aktiv. Für >1h Läufe auf Streamlit Cloud "
                    "`TURSO_URL`, `TURSO_KEY`, `GITHUB_TOKEN` & `GITHUB_REPO` setzen."
                )

        with st.container(border=True):
            st.markdown("**📧 E-Mail-Benachrichtigung**")
            st.write(f"Provider: `{settings.email_provider.value}`")
            if settings.has_email_configured:
                st.success("E-Mail-Zugangsdaten sind konfiguriert.")
            else:
                st.warning("Kein E-Mail-Provider aktiv konfiguriert.")

    if role == AuthRole.ADMIN:
        st.divider()
        st.markdown("**🛠️ Wartung & Recovery**")
        claimable = repository.find_claimable_job_ids(
            stale_timeout_minutes=settings.stale_job_timeout_minutes,
            limit=10,
        )
        st.caption(
            f"Wartende oder verwaiste Jobs (Heartbeat älter als {settings.stale_job_timeout_minutes} Min.): "
            f"**{len(claimable)}**"
        )
        if claimable and st.button("🔄 Verwaiste Jobs jetzt anstoßen", type="secondary"):
            dispatcher = JobDispatcher(settings)
            for job_id in claimable:
                job = repository.get_job(job_id)
                dispatcher.dispatch(job_id, depth=job.depth if job else None)
            st.success(f"{len(claimable)} Job(s) wurden erneut an den Worker übergeben.")

    st.divider()
    st.caption(f"Research-Bot Version `v{__version__}`")
