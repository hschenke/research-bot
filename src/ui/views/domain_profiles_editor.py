"""Teil-Editor für Recherche-Tiefen-Profile und App-/Kontext-Limits in der Web-App."""

from __future__ import annotations

import streamlit as st

from src.domain.domain_config import (
    DepthProfileConfig,
    DomainConfig,
    ProcessingLimitsConfig,
)
from src.domain.enums import ResearchDepth
from src.storage.base_repository import ResearchRepository
from src.ui.views.domain_prompts_editor import persist_config_update


def render_depth_profiles_tab(
    cfg: DomainConfig,
    repository: ResearchRepository,
    is_admin: bool,
) -> None:
    """Rendert die 4 Recherche-Tiefen-Profile vollständig editierbar."""
    with st.form("form_depth_profiles", clear_on_submit=False):
        updated_depths: dict[ResearchDepth, DepthProfileConfig] = {}
        for depth in ResearchDepth:
            prof = cfg.depth_profiles[depth]
            with st.expander(f"{prof.label} (`{depth.value}`)", expanded=False):
                updated_depths[depth] = _render_single_depth_inputs(depth, prof, is_admin)

        saved = st.form_submit_button(
            "💾 Recherche-Tiefen speichern",
            type="primary",
            width="stretch",
            disabled=not is_admin,
        )

    if saved and is_admin:
        persist_config_update(repository, cfg.model_copy(update={"depth_profiles": updated_depths}))


def _render_single_depth_inputs(
    depth: ResearchDepth,
    prof: DepthProfileConfig,
    is_admin: bool,
) -> DepthProfileConfig:
    k = depth.value
    r1c1, r1c2, r1c3 = st.columns(3)
    with r1c1:
        lbl = st.text_input("Bezeichnung", value=prof.label, disabled=not is_admin, key=f"dcfg_dp_lbl_{k}")
    with r1c2:
        dur = st.text_input("Zeitschätzung", value=prof.estimated_duration_label, disabled=not is_admin, key=f"dcfg_dp_dur_{k}")
    with r1c3:
        words = st.text_input("Ziel-Wortumfang", value=prof.target_word_range, disabled=not is_admin, key=f"dcfg_dp_w_{k}")

    r2c1, r2c2, r2c3, r2c4, r2c5 = st.columns(5)
    with r2c1:
        sub_cnt = st.number_input("Max. Teilgebiete", 1, 25, prof.subtopic_count, disabled=not is_admin, key=f"dcfg_dp_sc_{k}")
    with r2c2:
        bullets = st.number_input("Max. Bullets/Teil", 1, 15, prof.max_subtopic_bullets, disabled=not is_admin, key=f"dcfg_dp_bl_{k}")
    with r2c3:
        urls = st.number_input("Webseiten/Teil", 0, 10, prof.max_urls_per_subtopic, disabled=not is_admin, key=f"dcfg_dp_u_{k}")
    with r2c4:
        fup = st.number_input("Follow-up-Runden", 0, 4, prof.followup_rounds, disabled=not is_admin, key=f"dcfg_dp_f_{k}")
    with r2c5:
        par = st.number_input("Parallele Worker", 1, 6, prof.parallel_subtopics, disabled=not is_admin, key=f"dcfg_dp_p_{k}")

    r3c1, r3c2, r3c3, r3c4 = st.columns([1.2, 1.6, 1.2, 1.0])
    with r3c1:
        use_ag = st.checkbox("Deep-Agent aktiv", value=prof.use_deep_research_agent, disabled=not is_admin, key=f"dcfg_dp_ag_{k}")
    with r3c2:
        ag_name = st.text_input("Agent-Modell", value=prof.deep_agent_name, disabled=not is_admin, key=f"dcfg_dp_agn_{k}")
    with r3c3:
        pref_deep = st.checkbox("Deep-Modell bevorzugen", value=prof.prefer_deep_model, disabled=not is_admin, key=f"dcfg_dp_pd_{k}")
    with r3c4:
        pause = st.number_input("Pause (Sek.)", 0.0, 10.0, float(prof.inter_step_pause_seconds), step=0.1, disabled=not is_admin, key=f"dcfg_dp_ps_{k}")

    return DepthProfileConfig(
        label=lbl,
        estimated_duration_label=dur,
        subtopic_count=int(sub_cnt),
        target_word_range=words,
        max_subtopic_bullets=int(bullets),
        max_urls_per_subtopic=int(urls),
        followup_rounds=int(fup),
        parallel_subtopics=int(par),
        use_deep_research_agent=use_ag,
        deep_agent_name=ag_name,
        prefer_deep_model=pref_deep,
        inter_step_pause_seconds=float(pause),
    )


def render_app_and_limits_tab(
    cfg: DomainConfig,
    repository: ResearchRepository,
    is_admin: bool,
) -> None:
    """Rendert Anwendungs-Defaults, KI-Modellfreigaben sowie Kontext- und Worker-Limits."""
    fmt_keys = list(cfg.report_formats.keys())
    depth_opts = list(ResearchDepth)
    lim = cfg.limits

    with st.form("form_app_and_limits", clear_on_submit=False):
        with st.expander("🎯 Standard-Vorgaben (Sprache, Tiefe & Format)", expanded=False):
            st.caption(
                "💡 **KI-Modell- & Tool-Routing:** Die Zuweisung von KI-Modellen "
                "(Planung, Recherche, Fallback, Synthese) und Such-Tools erfolgt zentral im Tab „Neue Recherche“ "
                "über den Button **„🎛️ Routing & Tools“**."
            )
            c1, c2 = st.columns(2)
            with c1:
                def_lang = st.selectbox(
                    "Standard-Sprache",
                    options=["de", "en"],
                    index=0 if cfg.default_language == "de" else 1,
                    format_func=lambda code: "Deutsch" if code == "de" else "English",
                    disabled=not is_admin,
                )
                en_agent = st.checkbox(
                    "Autonomen Deep-Research-Agent global erlauben",
                    value=cfg.enable_deep_research_agent,
                    disabled=not is_admin,
                )
            with c2:
                def_depth = st.selectbox(
                    "Standard-Recherche-Tiefe",
                    options=depth_opts,
                    index=depth_opts.index(cfg.default_depth),
                    format_func=lambda d: cfg.depth_profiles[d].label,
                    disabled=not is_admin,
                )
                def_fmt = st.selectbox(
                    "Standard-Berichtsformat",
                    options=fmt_keys,
                    index=fmt_keys.index(cfg.default_report_format) if cfg.default_report_format in fmt_keys else 0,
                    format_func=lambda k: cfg.report_formats[k].label,
                    disabled=not is_admin,
                )

        with st.expander("⚙️ Kontext-, Scraping- & Worker-Grenzen", expanded=False):
            l1, l2, l3, l4 = st.columns(4)
            with l1:
                max_sq = st.number_input("Max. Suchanfragen/Teil", 1, 8, lim.max_search_queries_per_subtopic, disabled=not is_admin)
                max_tk = st.number_input("Max. Key-Takeaways", 1, 12, lim.max_takeaways_count, disabled=not is_admin)
            with l2:
                max_oq = st.number_input("Max. Follow-up-Fragen", 1, 5, lim.max_open_questions_per_followup, disabled=not is_admin)
                max_loc = st.number_input("Max. lokale Worker-Jobs", 1, 16, cfg.max_concurrent_local_jobs, disabled=not is_admin)
            with l3:
                max_pg = st.number_input("Zeichen/Webseite", 500, 15000, lim.max_chars_per_scraped_page, step=250, disabled=not is_admin)
                stale_m = st.number_input("Watchdog-Timeout (Min.)", 1, 180, cfg.stale_job_timeout_minutes, disabled=not is_admin)
            with l4:
                max_fc = st.number_input("Zeichen/Teil in Synthese", 300, 10000, lim.max_finding_chars_in_synthesis, step=100, disabled=not is_admin)
                max_bc = st.number_input("Zeichen Baseline-Kontext", 300, 10000, lim.max_baseline_chars_in_context, step=100, disabled=not is_admin)

        saved = st.form_submit_button(
            "💾 App-Einstellungen & Limits speichern",
            type="primary",
            width="stretch",
            disabled=not is_admin,
        )

    if saved and is_admin:
        new_limits = ProcessingLimitsConfig(
            max_search_queries_per_subtopic=int(max_sq),
            max_open_questions_per_followup=int(max_oq),
            max_chars_per_scraped_page=int(max_pg),
            max_finding_chars_in_synthesis=int(max_fc),
            max_baseline_chars_in_context=int(max_bc),
            max_takeaways_count=int(max_tk),
        )
        persist_config_update(
            repository,
            cfg.model_copy(
                update={
                    "default_language": def_lang,
                    "default_depth": def_depth,
                    "default_report_format": def_fmt,
                    "enable_deep_research_agent": en_agent,
                    "max_concurrent_local_jobs": int(max_loc),
                    "stale_job_timeout_minutes": int(stale_m),
                    "limits": new_limits,
                }
            ),
        )
