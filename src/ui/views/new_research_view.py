"""Tab 1: Formular zum Starten neuer Web-Recherchen."""

from __future__ import annotations

import streamlit as st

from src.config.constants import DEFAULT_MODEL, DEPTH_PROFILES
from src.config.settings import AppSettings
from src.domain.enums import AuthRole, ResearchDepth
from src.domain.exceptions import DispatchError, InputValidationError, ResearchBotError
from src.research.gemini_client import fetch_available_gemini_models
from src.security.sanitization import validate_research_request
from src.storage.base_repository import ResearchRepository
from src.worker.dispatcher import JobDispatcher

# Aktuell auf gemini-3.5-flash-lite fokussiert; modular erweiterbar für zukünftige Modelle
SUPPORTED_UI_MODELS: tuple[str, ...] = (DEFAULT_MODEL,)


@st.cache_data(ttl=3600, show_spinner=False)
def _get_available_gemini_models(api_key: str = "") -> list[str]:
    """Gibt die aktuell freigegebenen KI-Modelle für das UI-Formular zurück."""
    _ = (api_key, fetch_available_gemini_models)
    return list(SUPPORTED_UI_MODELS)


def render_new_research_view(
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
) -> None:
    """Rendert das schlanke Eingabeformular zum Anlegen und Starten eines Auftrags."""
    if st.session_state.pop("_new_job_started", False):
        col_alert, col_btn = st.columns([3, 1], vertical_alignment="center")
        with col_alert:
            st.success("Auftrag gestartet! [👉 Zum laufenden Job](?tab=jobs)")
        with col_btn:
            if st.button("👉 Zum laufenden Job", type="primary", use_container_width=True, key="btn_jump_to_jobs"):
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
    default_idx = depth_options.index(settings.default_depth)

    with st.form("create_research_job_form", clear_on_submit=False):
        topic = st.text_input(
            "🔎 Recherche-Thema oder Leitfrage *",
            placeholder="z. B. Festkörperbatterien im E-Auto-Markt 2026–2030: Hersteller, Kosten & Serienreife",
            max_chars=300,
        )
        focus_instructions = st.text_area(
            "🎯 Optionale Schwerpunkte, Teilfragen oder auszuschließende Aspekte",
            placeholder="z. B. Fokus auf europäische und asiatische Zulieferer, konkrete Wh/kg-Kennzahlen, Patente und Risiken...",
            height=110,
            max_chars=2000,
        )

        col_model, col_depth = st.columns([1, 1])
        with col_model:
            model_options = _get_available_gemini_models(settings.gemini_api_key)
            selected_model = st.selectbox(
                "🧠 KI-Modell",
                options=model_options,
                index=0,
                help="Wähle das Gemini-Modell für Recherche & Synthese. Standard: gemini-3.5-flash-lite.",
            )
        with col_depth:
            selected_depth = st.selectbox(
                "Recherche-Tiefe & Laufzeitprofil",
                options=depth_options,
                index=default_idx,
                format_func=lambda d: (
                    f"{DEPTH_PROFILES[d].label} ({DEPTH_PROFILES[d].estimated_duration_label})"
                ),
                help="Wähle das passende Recherche-Profil vom schnellen Turbo-Überblick (ca. 20–60 Sek.) bis zur intensiven Tiefenrecherche.",
            )

        col_lang, col_mail = st.columns([1, 2])
        with col_lang:
            selected_lang = st.selectbox(
                "Berichtssprache",
                options=["de", "en"],
                index=0 if settings.default_language == "de" else 1,
                format_func=lambda code: "Deutsch" if code == "de" else "English",
            )
        with col_mail:
            notify_email = st.text_input(
                "📧 E-Mail bei Fertigstellung",
                value=settings.email_to,
                placeholder="name@beispiel.de",
                help="Sobald das Ergebnis vorliegt, wird der aufbereitete Bericht automatisch an diese Adresse gesendet.",
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
    language: str,
    notify_email: str,
    model: str = DEFAULT_MODEL,
) -> None:
    try:
        validated = validate_research_request(
            topic=topic,
            focus_instructions=focus_instructions,
            depth=depth,
            language=language,
            notify_email=notify_email,
            model=model,
        )
        job = repository.create_job(validated)
        dispatcher = JobDispatcher(settings)
        dispatcher.dispatch(job.id, depth=validated.depth)
        st.session_state["_new_job_started"] = True
        st.rerun()
    except InputValidationError as exc:
        st.error(f"Eingabefehler: {exc}")
    except DispatchError as exc:
        st.warning(
            f"Auftrag wurde in der Warteschlange gespeichert, aber der Direktstart meldete: {exc}"
        )
    except ResearchBotError as exc:
        st.error(f"Auftrag konnte nicht angelegt werden: {exc}")
