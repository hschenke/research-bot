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
from src.worker.dispatcher import JobDispatcher


def _resolve_ui_models(api_key: str, cfg: DomainConfig) -> list[str]:
    """Ermittelt die freigegebenen KI-Modelle aus der aktiven Fachlogik-Konfiguration."""
    try:
        return fetch_available_gemini_models(api_key, allowed_models=cfg.available_models)
    except TypeError:
        return fetch_available_gemini_models(api_key)


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
            st.success("Auftrag gestartet! Der Job läuft nun im Hintergrund.")
        with col_btn:
            if st.button("👉 Zum laufenden Job", type="primary", use_container_width=True, key="btn_jump_to_jobs"):
                st.session_state.pop("_new_job_started", None)
                st.session_state["_force_nav_tab"] = "jobs"
                st.rerun()

    flash_msg = st.session_state.pop("_new_job_flash", None)
    if flash_msg:
        st.success(str(flash_msg))

    if role != AuthRole.ADMIN:
        st.info(
            "Du befindest dich im **Lese-Modus**. Melde dich links in der Seitenleiste als Admin an, "
            "um neue Recherche-Aufträge zu starten."
        )
        return

    if not settings.has_gemini_configured:
        st.warning(
            "⚠️ Noch kein gültiger `GEMINI_API_KEY` konfiguriert. "
            "Hinterlege den Key in `.env` bzw. den Streamlit Secrets, bevor du einen Auftrag startest."
        )

    depth_options = list(ResearchDepth)
    default_depth_idx = depth_options.index(cfg.default_depth)
    format_keys = list(cfg.report_formats.keys())
    default_fmt_idx = format_keys.index(cfg.default_report_format) if cfg.default_report_format in format_keys else 0
    model_options = _resolve_ui_models(settings.gemini_api_key, cfg)
    default_model_idx = model_options.index(cfg.default_model) if cfg.default_model in model_options else 0

    with st.form("create_research_job_form", clear_on_submit=False):
        topic = st.text_input(
            "🔎 Recherche-Thema oder Leitfrage *",
            placeholder="z. B. Top 7 Badeseen in Bayern – nur eine knappe Liste",
            max_chars=300,
        )
        focus_instructions = st.text_area(
            "🎯 Optionale Schwerpunkte, Formatwünsche oder auszuschließende Aspekte",
            placeholder="z. B. Nur eine knappe Stichpunktliste mit Wasserqualität & Anfahrt – kein langer Fließtext...",
            height=95,
            max_chars=2000,
        )

        col_fmt, col_depth, col_model = st.columns([1.2, 1.3, 1.0])
        with col_fmt:
            selected_format = st.selectbox(
                "📐 Berichtsformat & Stil",
                options=format_keys,
                index=default_fmt_idx,
                format_func=lambda k: cfg.report_formats[k].label,
                help="Steuert Struktur und Kürze des Berichts (anpassbar im Tab '🎛️ KI- & Fachlogik').",
            )
        with col_depth:
            selected_depth = st.selectbox(
                "🔎 Recherche-Tiefe & Zielumfang",
                options=depth_options,
                index=default_depth_idx,
                format_func=lambda d: (
                    f"{cfg.get_depth_profile(d).label} "
                    f"({cfg.get_depth_profile(d).estimated_duration_label} · {cfg.get_depth_profile(d).target_word_range})"
                ),
                help="Alle Tiefenprofile und Wortumfänge sind unter '🎛️ KI- & Fachlogik' frei editierbar.",
            )
        with col_model:
            selected_model = st.selectbox(
                "🧠 KI-Modell",
                options=model_options,
                index=default_model_idx,
                help=f"Konfigurierbar in '🎛️ KI- & Fachlogik'. Standard: {cfg.default_model}.",
            )

        col_lang, col_mail = st.columns([1, 2])
        with col_lang:
            selected_lang = st.selectbox(
                "Berichtssprache",
                options=["de", "en"],
                index=0 if cfg.default_language == "de" else 1,
                format_func=lambda code: "Deutsch" if code == "de" else "English",
            )
        with col_mail:
            notify_email = st.text_input(
                "📧 E-Mail bei Fertigstellung",
                value=settings.email_to,
                placeholder="name@beispiel.de",
                help="Optionaler automatischer Versand des fertigen Berichts.",
            )

        submitted = st.form_submit_button(
            "🚀 Recherche im Hintergrund starten",
            type="primary",
            use_container_width=True,
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
        notify_email=notify_email,
        model=selected_model,
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
        st.session_state["_jobs_tab_flash"] = (
            "✅ Recherche-Auftrag erfolgreich gestartet! Der Job läuft nun im Hintergrund."
        )
        st.rerun()
    except InputValidationError as exc:
        st.error(f"Eingabefehler: {exc}")
    except DispatchError as exc:
        st.warning(
            f"Auftrag wurde in der Warteschlange gespeichert, aber der Direktstart meldete: {exc}"
        )
    except ResearchBotError as exc:
        st.error(f"Auftrag konnte nicht angelegt werden: {exc}")
