"""Tab 1: Formular zum Starten neuer Web-Recherchen (inkl. >1h Hintergrund-Modus)."""

from __future__ import annotations

import streamlit as st

from src.config.constants import DEFAULT_MODEL, DEPTH_PROFILES
from src.config.settings import AppSettings
from src.domain.enums import AuthRole, ResearchDepth
from src.domain.exceptions import DispatchError, InputValidationError, ResearchBotError
from src.research.gemini_client import fetch_available_gemini_models
from src.security.sanitization import validate_research_request
from src.storage.base_repository import ResearchRepository
from src.worker.dispatcher import JobDispatcher, resolve_effective_worker_mode


@st.cache_data(ttl=3600, show_spinner=False)
def _get_available_gemini_models(api_key: str) -> list[str]:
    """Fragt die verfügbaren Modelle ab und cacht das Ergebnis für 1 Stunde."""
    return fetch_available_gemini_models(api_key)


def render_new_research_view(
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
) -> None:
    """Rendert das Eingabeformular zum Anlegen und sofortigen Hintergrund-Start eines Auftrags."""
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

    effective_worker = resolve_effective_worker_mode(settings)
    worker_badge = (
        "☁️ GitHub Actions Cloud-Worker aktiv (läuft bis zu 5 Std. im Hintergrund – Browser kann geschlossen werden)"
        if effective_worker.value == "github"
        else "💻 Lokaler Hintergrund-Worker aktiv (läuft asynchron im Server-Prozess)"
    )
    st.caption(worker_badge)

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
            default_m_idx = (
                model_options.index(DEFAULT_MODEL)
                if DEFAULT_MODEL in model_options
                else 0
            )
            selected_model = st.selectbox(
                "🧠 KI-Modell",
                options=model_options,
                index=default_m_idx,
                help="Wähle das Gemini-Modell für Recherche & Synthese (dynamisch vom API-Key abgefragt). Standard: gemini-3.5-flash-lite.",
            )
        with col_depth:
            selected_depth = st.selectbox(
                "Recherche-Tiefe & Laufzeitprofil",
                options=depth_options,
                index=default_idx,
                format_func=lambda d: (
                    f"{DEPTH_PROFILES[d].label} ({DEPTH_PROFILES[d].estimated_duration_label})"
                ),
            )

        col_lang, col_mail = st.columns([1, 2])
        with col_lang:
            selected_lang = st.selectbox(
                "Berichtssprache",
                options=["de", "en"],
                index=0 if settings.default_language == "de" else 1,
                format_func=lambda code: "🇩🇪 Deutsch" if code == "de" else "🇬🇧 English",
            )
        with col_mail:
            notify_email = st.text_input(
                "📧 E-Mail bei Fertigstellung",
                value=settings.email_to,
                placeholder="name@beispiel.de",
                help="Sobald das Ergebnis vorliegt, wird der aufbereitete Bericht automatisch an diese Adresse gesendet.",
            )

        selected_profile = DEPTH_PROFILES[selected_depth]
        st.caption(
            f"Geplanter Umfang: **{selected_profile.subtopic_count} Teilgebiete**, "
            f"bis zu **{selected_profile.max_urls_per_subtopic} Volltext-Seiten pro Teilgebiet**, "
            f"**{selected_profile.followup_rounds}** Nachschärfungsrunde(n)."
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
        worker_label = dispatcher.dispatch(job.id)
        st.success(
            f"✅ Auftrag **`{job.id}`** wurde mit Modell **`{job.model}`** gestartet ({worker_label})! "
            "Du kannst dieses Fenster jederzeit schließen – bei Fertigstellung wirst du per E-Mail informiert. "
            "Den Live-Fortschritt findest du im Tab **⏳ Laufende Jobs**."
        )
    except InputValidationError as exc:
        st.error(f"Eingabefehler: {exc}")
    except DispatchError as exc:
        st.warning(
            f"Auftrag wurde in der Warteschlange gespeichert, aber der Direktstart meldete: {exc}"
        )
    except ResearchBotError as exc:
        st.error(f"Auftrag konnte nicht angelegt werden: {exc}")
