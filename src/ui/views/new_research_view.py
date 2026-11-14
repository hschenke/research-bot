"""Tab 1: Formular zum Starten neuer Web-Recherchen (gespeist aus `DomainConfig`)."""

from __future__ import annotations

import streamlit as st

from src.config.domain_loader import load_default_domain_config
from src.config.settings import AppSettings
from src.domain.domain_config import DomainConfig
from src.domain.enums import AuthRole, ResearchDepth
from src.domain.exceptions import DispatchError, InputValidationError, ResearchBotError
from src.research.gemini_client import fetch_available_gemini_models
from src.security.sanitization import validate_research_request
from src.storage.base_repository import ResearchRepository
from src.ui.views.model_routing_dialog import render_model_routing_dialog
from src.worker.dispatcher import JobDispatcher

__all__ = ["fetch_available_gemini_models", "render_new_research_view"]


def render_new_research_view(
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
    domain_config: DomainConfig | None = None,
) -> None:
    """Rendert das Eingabeformular mit Format-, Modell- und Tiefenauswahl aus `DomainConfig`."""
    cfg = domain_config or repository.get_domain_config() or load_default_domain_config()

    if st.session_state.get("_new_job_started", False):
        col_alert, col_btn = st.columns([3, 1], vertical_alignment="center")
        with col_alert:
            st.success("Job gestartet und läuft im Hintergrund.")
        with col_btn:
            if st.button("👉 Zu den Jobs", type="primary", width="stretch", key="btn_jump_to_jobs"):
                st.session_state.pop("_new_job_started", None)
                st.session_state["_force_nav_tab"] = "jobs"
                st.rerun()

    flash_msg = st.session_state.pop("_new_job_flash", None)
    if flash_msg:
        st.success(str(flash_msg))

    if role != AuthRole.ADMIN:
        st.info("🔒 **Read-Only** – Bitte in der Sidebar als Admin einloggen.")
        return

    if not settings.has_gemini_configured:
        st.warning("⚠️ `GEMINI_API_KEY` fehlt in `.env` oder `st.secrets`.")

    depth_options = list(ResearchDepth)
    default_depth_idx = depth_options.index(cfg.default_depth)
    format_keys = list(cfg.report_formats.keys())
    default_fmt_idx = format_keys.index(cfg.default_report_format) if cfg.default_report_format in format_keys else 0
    routing = cfg.model_routing

    if st.button("🎛️ Routing & Tools", help="Models, Search Tools & Rate Limits"):
        render_model_routing_dialog(repository, cfg, role)

    with st.form("create_research_job_form", clear_on_submit=False):
        topic = st.text_input(
            "🔎 Recherche-Thema *",
            placeholder="z. B. Top 7 Badeseen in Bayern – kurze Liste",
            max_chars=300,
        )
        focus_instructions = st.text_area(
            "🎯 Fokus & Constraints (optional)",
            placeholder="z. B. Fokus auf Wasserqualität & Anfahrt, kein Fließtext...",
            height=80,
            max_chars=2000,
        )

        col_fmt, col_depth, col_lang = st.columns([1.3, 1.3, 0.8])
        with col_fmt:
            selected_format = st.selectbox(
                "📐 Format",
                options=format_keys,
                index=default_fmt_idx,
                format_func=lambda k: cfg.report_formats[k].label,
            )
        with col_depth:
            selected_depth = st.selectbox(
                "⚡ Depth",
                options=depth_options,
                index=default_depth_idx,
                format_func=lambda d: (
                    f"{cfg.get_depth_profile(d).label} ({cfg.get_depth_profile(d).estimated_duration_label})"
                ),
            )
        with col_lang:
            selected_lang = st.selectbox(
                "🌐 Sprache",
                options=["de", "en"],
                index=0 if cfg.default_language == "de" else 1,
                format_func=lambda code: "Deutsch" if code == "de" else "English",
            )

        submitted = st.form_submit_button(
            "🚀 Recherche starten",
            type="primary",
            width="stretch",
            disabled=not settings.has_gemini_configured,
        )

    if not submitted:
        return

    _handle_job_submission(
        settings=settings,
        repository=repository,
        topic=topic,
        focus_instructions=focus_instructions,
        depth=selected_depth,
        report_format=selected_format,
        language=selected_lang,
        notify_email=settings.email_to,
        model=routing.research_primary_model or cfg.default_model,
    )


def _handle_job_submission(
    settings: AppSettings,
    repository: ResearchRepository,
    topic: str,
    focus_instructions: str,
    depth: ResearchDepth,
    report_format: str,
    language: str,
    notify_email: str,
    model: str,
) -> None:
    try:
        validated = validate_research_request(
            topic=topic,
            focus_instructions=focus_instructions,
            depth=depth,
            language=language,
            notify_email=notify_email,
            model=model,
            report_format=report_format,
        )
        job = repository.create_job(validated)
        dispatcher = JobDispatcher(settings)
        dispatcher.dispatch(job.id, depth=validated.depth)
        st.session_state["_force_nav_tab"] = "jobs"
        st.session_state["_jobs_tab_flash"] = "✅ Job erfolgreich gestartet."
        st.rerun()
    except InputValidationError as exc:
        st.error(f"Eingabefehler: {exc}")
    except DispatchError as exc:
        st.warning(f"In Queue gespeichert (Direktstart-Hinweis: {exc})")
    except ResearchBotError as exc:
        st.error(f"Fehler beim Starten: {exc}")

