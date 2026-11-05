"""Haupt-Controller der Streamlit-Applikation (reine Präsentations-Orchestrierung)."""

from __future__ import annotations

import streamlit as st

from src.__version__ import __version__
from src.config.settings import AppSettings, load_app_settings
from src.storage.base_repository import ResearchRepository
from src.storage.factory import create_repository
from src.ui.auth_view import determine_session_role, render_login_gate_or_sidebar
from src.ui.styles import inject_static_styles
from src.ui.views.active_jobs_view import render_active_jobs_view
from src.ui.views.domain_config_view import render_domain_config_view
from src.ui.views.new_research_view import render_new_research_view
from src.ui.views.reports_view import render_reports_view
from src.ui.views.settings_view import render_settings_view

NAV_TAB_KEYS: tuple[str, ...] = ("new", "jobs", "reports", "rules", "settings")
NAV_TAB_TITLES: tuple[str, ...] = (
    "🚀 Neue Recherche",
    "⏳ Laufende Jobs",
    "📚 Ergebnisse & Archiv",
    "🎛️ KI- & Fachlogik",
    "⚙️ Systemstatus",
)
NAV_KEY_TO_TITLE: dict[str, str] = dict(zip(NAV_TAB_KEYS, NAV_TAB_TITLES, strict=True))


@st.cache_resource
def _get_cached_repository(turso_url: str, sqlite_path: str) -> ResearchRepository:
    """Erstellt eine wiederverwendbare Repository-Instanz pro Server-Prozess."""
    _ = (turso_url, sqlite_path)
    settings = load_app_settings()
    return create_repository(settings, initialize=True)


def _resolve_default_tab_title() -> str:
    """Ermittelt den initial oder programmatisch ausgewählten Navigations-Tab."""
    forced_tab = st.session_state.pop("_force_nav_tab", None)
    if forced_tab in NAV_KEY_TO_TITLE:
        return NAV_KEY_TO_TITLE[forced_tab]

    requested_tab = str(st.query_params.get("tab", "") or "").lower()
    if requested_tab in NAV_KEY_TO_TITLE:
        return NAV_KEY_TO_TITLE[requested_tab]

    return NAV_TAB_TITLES[0]


def run_streamlit_app() -> None:
    """Initialisiert Seitenlayout, Authentifizierung, Fachlogik-Laden und Tab-Navigation."""
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
    domain_config = repository.get_domain_config()

    st.sidebar.markdown(f"### 🔬 Research-Bot `v{__version__}`")
    role = determine_session_role(settings)
    if not render_login_gate_or_sidebar(settings, role):
        return

    st.title("🔬 Deep Web Research-Bot")
    st.caption(
        "Knackige, faktenbasierte Web-Recherche im Hintergrund – mit frei editierbaren KI-Regeln, "
        "Prompts & Berichtsformaten direkt in der Web-App."
    )

    default_tab_title = _resolve_default_tab_title()
    tab_new, tab_jobs, tab_reports, tab_rules, tab_settings = st.tabs(
        list(NAV_TAB_TITLES),
        default=default_tab_title,
    )

    with tab_new:
        render_new_research_view(
            settings=settings,
            repository=repository,
            role=role,
            domain_config=domain_config,
        )
    with tab_jobs:
        render_active_jobs_view(
            settings=settings,
            repository=repository,
            role=role,
            domain_config=domain_config,
        )
    with tab_reports:
        render_reports_view(
            settings=settings,
            repository=repository,
            role=role,
            domain_config=domain_config,
        )
    with tab_rules:
        render_domain_config_view(
            repository=repository,
            role=role,
            domain_config=domain_config,
        )
    with tab_settings:
        render_settings_view(
            settings=settings,
            repository=repository,
            role=role,
            domain_config=domain_config,
        )
