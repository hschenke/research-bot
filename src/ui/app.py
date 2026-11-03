"""Haupt-Controller der Streamlit-Applikation (reine Präsentations-Orchestrierung)."""

from __future__ import annotations

import streamlit as st

from src.__version__ import __version__
from src.config.settings import AppSettings, load_app_settings
from src.storage.base_repository import ResearchRepository
from src.storage.factory import create_repository
from src.ui.auth_view import determine_session_role, render_login_gate_or_sidebar
from src.ui.styles import inject_static_styles
from src.ui.views.active_jobs_view import ACTIVE_JOB_STATUSES, render_active_jobs_view
from src.ui.views.new_research_view import render_new_research_view
from src.ui.views.reports_view import render_reports_view
from src.ui.views.settings_view import render_settings_view


@st.cache_resource
def _get_cached_repository(turso_url: str, sqlite_path: str) -> ResearchRepository:
    """Erstellt eine wiederverwendbare Repository-Instanz pro Server-Prozess."""
    _ = (turso_url, sqlite_path)
    settings = load_app_settings()
    return create_repository(settings, initialize=True)


def run_streamlit_app() -> None:
    """Initialisiert Seitenlayout, Authentifizierung und synchronisierte Tab-Navigation."""
    st.set_page_config(
        page_title="Research-Bot",
        page_icon="🔬",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    inject_static_styles()

    settings: AppSettings = load_app_settings()
    repository = _get_cached_repository(settings.turso_url, settings.sqlite_path)
    repository.cleanup_stuck_cancellations()

    st.sidebar.markdown(f"### 🔬 Research-Bot `v{__version__}`")
    role = determine_session_role(settings)
    if not render_login_gate_or_sidebar(settings, role):
        return

    st.title("🔬 Deep Web Research-Bot")
    st.caption(
        "Autonome, mehrstufige Web-Recherche im Hintergrund (auch > 1 Stunde) "
        "mit automatischer Selbstheilung & E-Mail-Benachrichtigung bei Fertigstellung."
    )

    running_count = repository.count_jobs(statuses=ACTIVE_JOB_STATUSES)
    st.session_state["_tab_active_count"] = running_count
    jobs_tab_label = (
        f"⏳ Laufende Jobs ({running_count})" if running_count > 0 else "⏳ Laufende Jobs"
    )

    requested_tab = str(st.query_params.get("tab", "") or "").lower()
    if requested_tab == "reports":
        tab_reports, tab_new, tab_jobs, tab_settings = st.tabs(
            ["📚 Ergebnisse & Archiv", "🚀 Neue Recherche", jobs_tab_label, "⚙️ Systemstatus"]
        )
    else:
        tab_new, tab_jobs, tab_reports, tab_settings = st.tabs(
            ["🚀 Neue Recherche", jobs_tab_label, "📚 Ergebnisse & Archiv", "⚙️ Systemstatus"]
        )

    with tab_new:
        render_new_research_view(settings=settings, repository=repository, role=role)
    with tab_jobs:
        render_active_jobs_view(settings=settings, repository=repository, role=role)
    with tab_reports:
        render_reports_view(settings=settings, repository=repository, role=role)
    with tab_settings:
        render_settings_view(settings=settings, repository=repository, role=role)
