"""Modal-Dialog zur Konfiguration der KI-Modell- und Tool-Routing-Strategie."""

from __future__ import annotations

import streamlit as st

from src.domain.domain_config import DomainConfig, ModelRoutingConfig
from src.domain.enums import AuthRole
from src.domain.exceptions import ResearchBotError
from src.storage.base_repository import ResearchRepository


@st.dialog("🎛️ KI-Modell- & Tool-Routing (Was, wann, wie)", width="large")
def render_model_routing_dialog(
    repository: ResearchRepository,
    cfg: DomainConfig,
    role: AuthRole,
) -> None:
    """Rendert die editierbare Options-Liste für Modelle, Tools und Rate-Limits."""
    routing = cfg.model_routing
    available = cfg.available_models
    is_admin = role == AuthRole.ADMIN

    st.markdown(
        "Hier definierst du transparent, **welches Modell** und **welche Tools** in welcher "
        "Phase eingesetzt werden, um innerhalb deines API-Limits (z. B. **15 RPM**) optimal zu arbeiten."
    )

    st.markdown("### 🧠 1. Phase: Planung & Zerlegung")
    st.caption("Erstellt den strukturierten Recherche-Plan mit Leitfragen (JSON-Schema, kein Web-Tool).")
    plan_idx = available.index(routing.planning_model) if routing.planning_model in available else 0
    new_plan_model = st.selectbox(
        "Planungs-Modell",
        options=available,
        index=plan_idx,
        disabled=not is_admin,
        key="dlg_planning_model",
    )

    st.markdown("---")
    st.markdown("### 🌐 2. Phase: Web-Recherche & Grounding")
    st.caption("Führt Live-Websuche und Fakten-Extraktion für jedes Teilthema durch.")
    col_p, col_f = st.columns(2)
    with col_p:
        prim_idx = available.index(routing.research_primary_model) if routing.research_primary_model in available else 0
        new_prim_model = st.selectbox(
            "Primäres Recherche-Modell",
            options=available,
            index=prim_idx,
            disabled=not is_admin,
            key="dlg_res_prim_model",
            help="Hauptmodell für Google Search Grounding.",
        )
    with col_f:
        fall_idx = available.index(routing.research_fallback_model) if routing.research_fallback_model in available else 0
        new_fall_model = st.selectbox(
            "Fallback-Modell (bei 429 / Last)",
            options=available,
            index=fall_idx,
            disabled=not is_admin,
            key="dlg_res_fall_model",
            help="Springt sofort ein, wenn das Hauptmodell an ein Quota-Limit stößt.",
        )

    col_t1, col_t2 = st.columns(2)
    with col_t1:
        new_search_enabled = st.checkbox(
            "Google Search Tool aktiv",
            value=routing.enable_google_search,
            disabled=not is_admin,
            key="dlg_enable_search",
            help="Ermöglicht verifiziertes Grounding über Google Search.",
        )
    with col_t2:
        new_single_pass = st.checkbox(
            "⚡ Single-Pass Modus (1 Call pro Teilthema)",
            value=routing.single_pass_research,
            disabled=not is_admin,
            key="dlg_single_pass",
            help="Spart 75% API-Calls und verhindert 429-Limits zuverlässig.",
        )

    st.markdown("---")
    st.markdown("### ✍️ 3. Phase: Berichts-Synthese")
    st.caption("Führt Teilbefunde zusammen und formatiert den finalen Markdown-Bericht.")
    synth_idx = available.index(routing.synthesizer_model) if routing.synthesizer_model in available else 0
    new_synth_model = st.selectbox(
        "Synthese-Modell",
        options=available,
        index=synth_idx,
        disabled=not is_admin,
        key="dlg_synth_model",
    )

    st.markdown("---")
    st.markdown("### 🛡️ 4. Rate-Limiting & Quota-Pacing")
    st.caption("Puffert Aufrufe, sodass die Obergrenze von 15 Requests/Minute strikt geschützt bleibt.")
    col_rpm, col_pace = st.columns(2)
    with col_rpm:
        new_rpm = st.number_input(
            "Max. Requests pro Minute (RPM-Cap)",
            min_value=1,
            max_value=30,
            value=routing.max_rpm,
            disabled=not is_admin,
            key="dlg_max_rpm",
            help="Sicherheits-Obergrenze (empfohlen: 12 für ein 15-RPM-Limit).",
        )
    with col_pace:
        new_pace = st.number_input(
            "Mindestabstand zwischen Aufrufen (Sek.)",
            min_value=0.5,
            max_value=15.0,
            value=float(routing.min_call_interval_seconds),
            step=0.5,
            disabled=not is_admin,
            key="dlg_pace_sec",
            help="Mindestpause zwischen zwei aufeinanderfolgenden API-Calls.",
        )

    st.markdown("---")
    if not is_admin:
        st.info("🔒 Nur Administratoren können die Routing-Strategie dauerhaft ändern.")
        return

    if st.button("💾 Routing-Strategie speichern", type="primary", use_container_width=True, key="btn_save_routing"):
        _save_routing(
            repository=repository,
            cfg=cfg,
            planning_model=new_plan_model,
            primary_model=new_prim_model,
            fallback_model=new_fall_model,
            search_enabled=new_search_enabled,
            single_pass=new_single_pass,
            synth_model=new_synth_model,
            rpm=int(new_rpm),
            pace=float(new_pace),
        )


def _save_routing(
    repository: ResearchRepository,
    cfg: DomainConfig,
    planning_model: str,
    primary_model: str,
    fallback_model: str,
    search_enabled: bool,
    single_pass: bool,
    synth_model: str,
    rpm: int,
    pace: float,
) -> None:
    updated_routing = ModelRoutingConfig(
        planning_model=planning_model,
        research_primary_model=primary_model,
        research_fallback_model=fallback_model,
        synthesizer_model=synth_model,
        max_rpm=rpm,
        min_call_interval_seconds=pace,
        single_pass_research=single_pass,
        enable_google_search=search_enabled,
    )
    updated_cfg = cfg.model_copy(update={"model_routing": updated_routing})
    try:
        repository.save_domain_config(updated_cfg)
        st.session_state["_new_job_flash"] = "✅ KI-Modell- & Tool-Routing erfolgreich gespeichert!"
        st.rerun()
    except ResearchBotError as exc:
        st.error(f"Fehler beim Speichern der Routing-Strategie: {exc}")
