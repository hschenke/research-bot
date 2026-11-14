"""Kompakter Modal-Dialog zur Konfiguration der Model- und Tool-Routing-Strategie."""

from __future__ import annotations

import streamlit as st

from src.domain.domain_config import DomainConfig, ModelRoutingConfig
from src.domain.enums import AuthRole
from src.domain.exceptions import ResearchBotError
from src.storage.base_repository import ResearchRepository


@st.dialog("🎛️ Model & Tool Routing", width="large")
def render_model_routing_dialog(
    repository: ResearchRepository,
    cfg: DomainConfig,
    role: AuthRole,
) -> None:
    """Rendert die kompakte, modular getabte Einstellungs-Ansicht für Modelle und Tools."""
    routing = cfg.model_routing
    available = cfg.available_models
    is_admin = role == AuthRole.ADMIN

    tab_models, tab_tools, tab_pacing = st.tabs(
        [
            "🤖 Models",
            "🛠️ Search Tools",
            "⏱️ Rate Limits & Check",
        ]
    )

    with tab_models:
        c1, c2 = st.columns(2)
        with c1:
            p_idx = available.index(routing.planning_model) if routing.planning_model in available else 0
            new_plan = st.selectbox("Planner Model", available, index=p_idx, disabled=not is_admin, key="r_plan")
            prim_idx = available.index(routing.research_primary_model) if routing.research_primary_model in available else 0
            new_prim = st.selectbox("Primary Research Model", available, index=prim_idx, disabled=not is_admin, key="r_prim")
        with c2:
            fall_idx = available.index(routing.research_fallback_model) if routing.research_fallback_model in available else 0
            new_fall = st.selectbox("Fallback Model (429)", available, index=fall_idx, disabled=not is_admin, key="r_fall")
            syn_idx = available.index(routing.synthesizer_model) if routing.synthesizer_model in available else 0
            new_synth = st.selectbox("Synthesizer Model", available, index=syn_idx, disabled=not is_admin, key="r_syn")

    with tab_tools:
        t_col1, t_col2 = st.columns(2)
        with t_col1:
            new_google = st.checkbox(
                "🌐 Google Search (Grounding)",
                value=routing.enable_google_search,
                disabled=not is_admin,
                key="t_google",
                help="Gemini Grounding (bei Free-Tier 429 deaktivieren)",
            )
            new_ddg = st.checkbox(
                "🦆 DuckDuckGo Search",
                value=routing.enable_duckduckgo_search,
                disabled=not is_admin,
                key="t_ddg",
                help="Key-freie Websuche via DuckDuckGo Lite",
            )
            new_reddit = st.checkbox(
                "💬 Reddit Search",
                value=routing.enable_reddit_search,
                disabled=not is_admin,
                key="t_reddit",
                help="Community-Erfahrungen & Diskussionen",
            )
            new_jina = st.checkbox(
                "⚡ Jina AI Reader",
                value=routing.enable_jina_reader,
                disabled=not is_admin,
                key="t_jina",
                help="Saubere Markdown-Extraktion ohne JS-Bloat",
            )
        with t_col2:
            new_hn = st.checkbox(
                "🔶 Hacker News",
                value=routing.enable_hackernews_search,
                disabled=not is_admin,
                key="t_hn",
                help="Algolia API für Tech- & Software-Quellen",
            )
            new_wiki = st.checkbox(
                "📚 Wikipedia API",
                value=routing.enable_wikipedia_search,
                disabled=not is_admin,
                key="t_wiki",
                help="MediaWiki-API für Basis-Fakten",
            )
            new_arxiv = st.checkbox(
                "🔬 arXiv Preprints",
                value=routing.enable_arxiv_search,
                disabled=not is_admin,
                key="t_arxiv",
                help="Wissenschaftliche Papers & Preprints",
            )
            new_bb = st.checkbox(
                "🌐 Browserbase (Cloud Browser)",
                value=routing.enable_browserbase,
                disabled=not is_admin,
                key="t_bb",
                help="Erfordert BROWSERBASE_API_KEY",
            )

    with tab_pacing:
        p_c1, p_c2 = st.columns(2)
        with p_c1:
            new_rpm = st.number_input("Max RPM", 1, 60, routing.max_rpm, disabled=not is_admin, key="p_rpm")
            new_single = st.checkbox(
                "⚡ Global Single-Pass Override",
                routing.single_pass_research,
                disabled=not is_admin,
                key="p_sp",
                help="Erzwingt 1 Call pro Subtopic über ALLE Tiefen (inkl. Standard & Deep); deaktivieren für echtes Multi-Pass",
            )
        with p_c2:
            new_pace = st.number_input("Min. Interval (s)", 0.5, 30.0, float(routing.min_call_interval_seconds), 0.5, disabled=not is_admin, key="p_pace")
            new_verif = st.checkbox(
                "🔍 Cross-Source Verification",
                routing.enable_cross_verification,
                disabled=not is_admin,
                key="p_verif",
                help="Gleicht Fakten über Web, Reddit, HN und Wiki ab",
            )

    if not is_admin:
        st.info("🔒 Read-Only: Nur Admins können das Routing ändern.")
        return

    if st.button("💾 Speichern", type="primary", width="stretch", key="btn_save_routing"):
        _save_routing(
            repository=repository,
            cfg=cfg,
            planning_model=new_plan,
            primary_model=new_prim,
            fallback_model=new_fall,
            synth_model=new_synth,
            rpm=int(new_rpm),
            pace=float(new_pace),
            single_pass=new_single,
            enable_google=new_google,
            enable_ddg=new_ddg,
            enable_reddit=new_reddit,
            enable_hn=new_hn,
            enable_wiki=new_wiki,
            enable_arxiv=new_arxiv,
            enable_jina=new_jina,
            enable_bb=new_bb,
            enable_verif=new_verif,
        )


def _save_routing(
    repository: ResearchRepository,
    cfg: DomainConfig,
    planning_model: str,
    primary_model: str,
    fallback_model: str,
    synth_model: str,
    rpm: int,
    pace: float,
    single_pass: bool,
    enable_google: bool,
    enable_ddg: bool,
    enable_reddit: bool,
    enable_hn: bool,
    enable_wiki: bool,
    enable_arxiv: bool,
    enable_jina: bool,
    enable_bb: bool,
    enable_verif: bool,
) -> None:
    updated = ModelRoutingConfig(
        planning_model=planning_model,
        research_primary_model=primary_model,
        research_fallback_model=fallback_model,
        synthesizer_model=synth_model,
        max_rpm=rpm,
        min_call_interval_seconds=pace,
        single_pass_research=single_pass,
        enable_google_search=enable_google,
        enable_duckduckgo_search=enable_ddg,
        enable_reddit_search=enable_reddit,
        enable_hackernews_search=enable_hn,
        enable_wikipedia_search=enable_wiki,
        enable_arxiv_search=enable_arxiv,
        enable_jina_reader=enable_jina,
        enable_browserbase=enable_bb,
        enable_cross_verification=enable_verif,
    )
    updated_cfg = cfg.model_copy(update={"model_routing": updated})
    try:
        repository.save_domain_config(updated_cfg)
        st.session_state["_new_job_flash"] = "✅ Routing gespeichert."
        st.rerun()
    except ResearchBotError as exc:
        st.error(f"Fehler beim Speichern: {exc}")

