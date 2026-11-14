"""Haupt-Controller der Streamlit-Applikation (reine Präsentations-Orchestrierung)."""

from __future__ import annotations

import streamlit as st

from src.__version__ import __version__
from src.config.settings import AppSettings, load_app_settings
from src.storage.base_repository import ResearchRepository
from src.storage.factory import create_repository
from src.ui.auth_view import (
    check_login_gate,
    determine_session_role,
    render_sidebar_auth_controls,
)
from src.ui.styles import inject_static_styles
from src.ui.views.active_jobs_view import render_active_jobs_view
from src.ui.views.domain_config_view import render_domain_config_view
from src.ui.views.new_research_view import render_new_research_view
from src.ui.views.reports_view import render_reports_view
from src.ui.views.settings_view import render_settings_view

MAIN_NAV_TAB_KEY: str = "main_navigation_tab"
NAV_TAB_KEYS: tuple[str, ...] = ("new", "jobs", "reports", "rules", "settings")
NAV_TAB_TITLES: tuple[str, ...] = (
    "🚀 Neu",
    "⏳ Jobs",
    "📚 Reports",
    "🎛️ Prompts & Rules",
    "⚙️ System",
)
NAV_KEY_TO_TITLE: dict[str, str] = dict(zip(NAV_TAB_KEYS, NAV_TAB_TITLES, strict=True))
TITLE_TO_NAV_KEY: dict[str, str] = {v: k for k, v in NAV_KEY_TO_TITLE.items()}


@st.cache_resource
def _get_cached_repository(turso_url: str, sqlite_path: str) -> ResearchRepository:
    """Erstellt eine wiederverwendbare Repository-Instanz pro Server-Prozess."""
    _ = (turso_url, sqlite_path)
    settings = load_app_settings()
    return create_repository(settings, initialize=True)


def _sync_navigation_tab_state() -> str:
    """Synchronisiert den aktiven Tab vor dem Rendern von `st.tabs` ohne Widget-Reset."""
    forced_tab = st.session_state.pop("_force_nav_tab", None)
    if forced_tab in NAV_KEY_TO_TITLE:
        target_title = NAV_KEY_TO_TITLE[forced_tab]
        st.session_state[MAIN_NAV_TAB_KEY] = target_title
        st.query_params["tab"] = forced_tab
        st.session_state["_last_seen_qp_tab"] = forced_tab
        return target_title

    requested_tab = str(st.query_params.get("tab", "") or "").lower()
    last_qp = st.session_state.get("_last_seen_qp_tab")
    if requested_tab in NAV_KEY_TO_TITLE and requested_tab != last_qp:
        target_title = NAV_KEY_TO_TITLE[requested_tab]
        st.session_state[MAIN_NAV_TAB_KEY] = target_title
        st.session_state["_last_seen_qp_tab"] = requested_tab
        return target_title

    if MAIN_NAV_TAB_KEY not in st.session_state:
        initial_title = NAV_KEY_TO_TITLE.get(requested_tab, NAV_TAB_TITLES[0])
        st.session_state[MAIN_NAV_TAB_KEY] = initial_title
        initial_key = TITLE_TO_NAV_KEY[initial_title]
        st.session_state["_last_seen_qp_tab"] = initial_key
        return initial_title

    current = st.session_state.get(MAIN_NAV_TAB_KEY)
    if current not in NAV_TAB_TITLES:
        current = NAV_TAB_TITLES[0]
        st.session_state[MAIN_NAV_TAB_KEY] = current

    active_key = TITLE_TO_NAV_KEY[str(current)]
    if st.query_params.get("tab") != active_key:
        st.query_params["tab"] = active_key
        st.session_state["_last_seen_qp_tab"] = active_key

    return str(current)


def _on_top_tab_change() -> None:
    """Hält URL-Query-Parameter bei Klick auf einen oberen Tab synchron."""
    current = st.session_state.get(MAIN_NAV_TAB_KEY, NAV_TAB_TITLES[0])
    active_key = TITLE_TO_NAV_KEY.get(str(current), "new")
    st.query_params["tab"] = active_key
    st.session_state["_last_seen_qp_tab"] = active_key


def _on_sidebar_nav_click(nav_key: str) -> None:
    """Callback für den direkten Tab-Wechsel über die linke Seitenleiste."""
    if nav_key in NAV_KEY_TO_TITLE:
        target_title = NAV_KEY_TO_TITLE[nav_key]
        st.session_state[MAIN_NAV_TAB_KEY] = target_title
        st.query_params["tab"] = nav_key
        st.session_state["_last_seen_qp_tab"] = nav_key


def _render_sidebar_navigation(active_tab_title: str) -> None:
    """Rendert ausschließlich die Tab-Navigation in der linken Seitenleiste."""
    for nav_key, nav_title in NAV_KEY_TO_TITLE.items():
        is_active = nav_title == active_tab_title
        st.sidebar.button(
            nav_title,
            key=f"sidebar_nav_{nav_key}",
            type="primary" if is_active else "secondary",
            width="stretch",
            on_click=_on_sidebar_nav_click,
            args=(nav_key,),
        )


def run_streamlit_app() -> None:
    """Initialisiert Seitenlayout, Authentifizierung, Fachlogik-Laden und Tab-Navigation."""
    st.set_page_config(
        page_title="Research-Bot",
        page_icon="🔬",
        layout="wide",
        initial_sidebar_state="auto",
    )
    inject_static_styles()

    st.sidebar.markdown(f"### 🔬 Research-Bot `v{__version__}`")

    settings: AppSettings = load_app_settings()
    role = determine_session_role(settings)
    if not check_login_gate(settings, role):
        return

    repository = _get_cached_repository(settings.turso_url, settings.sqlite_path)
    if not st.session_state.get("_stuck_cancellations_cleaned"):
        repository.cleanup_stuck_cancellations()
        st.session_state["_stuck_cancellations_cleaned"] = True
    domain_config = repository.get_domain_config()

    active_tab_title = _sync_navigation_tab_state()
    _render_sidebar_navigation(active_tab_title)
    render_sidebar_auth_controls(settings, role)

    st.markdown(
        '<div class="rb-header"><div class="rb-brand"><span>🔬</span><span>Research-Bot</span></div></div>',
        unsafe_allow_html=True,
    )

    tab_new, tab_jobs, tab_reports, tab_rules, tab_settings = st.tabs(
        list(NAV_TAB_TITLES),
        key=MAIN_NAV_TAB_KEY,
        on_change=_on_top_tab_change,
    )

    if tab_new.open:
        with tab_new:
            render_new_research_view(
                settings=settings,
                repository=repository,
                role=role,
                domain_config=domain_config,
            )
    if tab_jobs.open:
        with tab_jobs:
            render_active_jobs_view(
                settings=settings,
                repository=repository,
                role=role,
                domain_config=domain_config,
            )
    if tab_reports.open:
        with tab_reports:
            render_reports_view(
                settings=settings,
                repository=repository,
                role=role,
                domain_config=domain_config,
            )
    if tab_rules.open:
        with tab_rules:
            render_domain_config_view(
                repository=repository,
                role=role,
                domain_config=domain_config,
            )
    if tab_settings.open:
        with tab_settings:
            render_settings_view(
                settings=settings,
                repository=repository,
                role=role,
                domain_config=domain_config,
            )
