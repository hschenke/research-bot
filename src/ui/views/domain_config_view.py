"""Tab 4: Vollständige Einsicht und Live-Bearbeitung der gesamten KI- & Anwendungs-Fachlogik."""

from __future__ import annotations

import streamlit as st

from src.domain.domain_config import (
    DomainConfig,
    ReportFormatPreset,
    StyleRulesConfig,
)
from src.domain.enums import AuthRole
from src.storage.base_repository import ResearchRepository
from src.ui.views.domain_profiles_editor import (
    render_app_and_limits_tab,
    render_depth_profiles_tab,
)
from src.ui.views.domain_prompts_editor import (
    clear_domain_editor_state,
    persist_config_update,
    render_prompts_tab,
    render_yaml_tab,
)


def render_domain_config_view(
    repository: ResearchRepository,
    role: AuthRole,
    domain_config: DomainConfig,
) -> None:
    """Zeigt alle Stilregeln, Formate, Prompts, Tiefenprofile und App-Limits editierbar an."""
    is_admin = role == AuthRole.ADMIN
    flash = st.session_state.pop("_rules_flash_msg", None)
    if flash:
        st.success(str(flash))

    st.subheader("🎛️ KI-Regeln, Prompts & Anwendungslogik")
    has_custom = repository.has_custom_domain_config()

    col_info, col_reset = st.columns([3.5, 1.5], vertical_alignment="center")
    with col_info:
        if has_custom:
            st.info(
                "✏️ **Eigene Anpassungen aktiv** (persistent in der Datenbank gespeichert, wirksam für Web-App & Worker)."
            )
        else:
            st.caption(
                "🟢 **Standard-Konfiguration aktiv** (`config/settings.yaml`). Alle Regeln sind unten einseh- und anpassbar."
            )
    with col_reset:
        if is_admin and has_custom and st.button(
            "🔄 Auf Standard zurücksetzen",
            key="btn_reset_domain_cfg",
            use_container_width=True,
        ):
            repository.reset_domain_config()
            clear_domain_editor_state()
            st.session_state["_rules_flash_msg"] = "Auf Standardwerte aus config/settings.yaml zurückgesetzt."
            st.session_state["_force_nav_tab"] = "rules"
            st.rerun()

    if not is_admin:
        st.caption("🔒 Lese-Ansicht aktiv: Melde dich links als Admin an, um Regeln und Prompts zu speichern.")

    sub_style, sub_prompts, sub_depths, sub_app, sub_yaml = st.tabs(
        [
            "✂️ Stil, Kürze & Formate",
            "🤖 KI-Prompts & Vorlagen",
            "📊 Recherche-Tiefen",
            "⚙️ App- & Kontext-Limits",
            "📄 YAML-Gesamteditor",
        ]
    )

    with sub_style:
        _render_style_and_formats_tab(domain_config, repository, is_admin)
    with sub_prompts:
        render_prompts_tab(domain_config, repository, is_admin)
    with sub_depths:
        render_depth_profiles_tab(domain_config, repository, is_admin)
    with sub_app:
        render_app_and_limits_tab(domain_config, repository, is_admin)
    with sub_yaml:
        render_yaml_tab(domain_config, repository, is_admin)


def _render_style_and_formats_tab(
    cfg: DomainConfig,
    repository: ResearchRepository,
    is_admin: bool,
) -> None:
    with st.form("form_style_and_formats", clear_on_submit=False):
        with st.expander("📝 Globale Kürze- & Redaktionsregeln", expanded=False):
            global_rules = st.text_area(
                "Allgemeine Stilregeln (`global_style_rules`)",
                value=cfg.style.global_style_rules,
                height=135,
                disabled=not is_admin,
            )
            override_rules = st.text_area(
                "Prioritätsregeln für Nutzer-Formatwünsche (`format_override_rules`)",
                value=cfg.style.format_override_rules,
                height=125,
                disabled=not is_admin,
            )
            kw_raw = st.text_area(
                "Schlüsselwörter für automatischen Knappe-Liste-Modus (komma- oder zeilengetrennt)",
                value=", ".join(cfg.style.compact_list_keywords),
                height=75,
                disabled=not is_admin,
            )

        with st.expander("📚 Quellenverzeichnis & Teilergebnis-Texte", expanded=False):
            col_s1, col_s2, col_s3 = st.columns([1.2, 1.4, 1.4])
            with col_s1:
                inc_sources = st.checkbox(
                    "Quellenverzeichnis anhängen",
                    value=cfg.style.include_sources_section,
                    disabled=not is_admin,
                )
            with col_s2:
                src_de = st.text_input("Quellen-Überschrift (DE)", value=cfg.style.sources_heading_de, disabled=not is_admin)
            with col_s3:
                src_en = st.text_input("Quellen-Überschrift (EN)", value=cfg.style.sources_heading_en, disabled=not is_admin)

            part_de = st.text_input("Teilergebnis-Hinweis (DE)", value=cfg.style.partial_notice_de, disabled=not is_admin)
            part_en = st.text_input("Teilergebnis-Hinweis (EN)", value=cfg.style.partial_notice_en, disabled=not is_admin)

        st.markdown("**Auswählbare Berichtsformate (`report_formats`)**")
        updated_formats: dict[str, ReportFormatPreset] = {}
        for fmt_key, preset in cfg.report_formats.items():
            with st.expander(f"Format `{fmt_key}` – {preset.label}", expanded=False):
                c1, c2, c3 = st.columns([2.2, 1.0, 1.2])
                with c1:
                    lbl = st.text_input("Anzeigename", value=preset.label, disabled=not is_admin, key=f"dcfg_fmt_lbl_{fmt_key}")
                with c2:
                    cap = st.number_input(
                        "Max. Teilgebiete (0 = Profil)",
                        min_value=0,
                        max_value=20,
                        value=preset.max_subtopics_cap,
                        disabled=not is_admin,
                        key=f"dcfg_fmt_cap_{fmt_key}",
                    )
                with c3:
                    omit = st.checkbox(
                        "Reiner Direktmodus (ohne Überbau)",
                        value=preset.omit_wrapper_sections,
                        disabled=not is_admin,
                        key=f"dcfg_fmt_omit_{fmt_key}",
                    )
                inst = st.text_area(
                    "Format-Anweisung an die KI",
                    value=preset.instructions,
                    height=105,
                    disabled=not is_admin,
                    key=f"dcfg_fmt_inst_{fmt_key}",
                )
                updated_formats[fmt_key] = ReportFormatPreset(
                    label=lbl,
                    instructions=inst,
                    max_subtopics_cap=int(cap),
                    omit_wrapper_sections=omit,
                )

        submitted = st.form_submit_button(
            "💾 Stilregeln & Berichtsformate speichern",
            type="primary",
            use_container_width=True,
            disabled=not is_admin,
        )

    if submitted and is_admin:
        keywords = [
            item.strip()
            for line in kw_raw.splitlines()
            for item in line.split(",")
            if item.strip()
        ]
        new_style = StyleRulesConfig(
            global_style_rules=global_rules,
            format_override_rules=override_rules,
            compact_list_keywords=keywords,
            include_sources_section=inc_sources,
            sources_heading_de=src_de,
            sources_heading_en=src_en,
            partial_notice_de=part_de,
            partial_notice_en=part_en,
        )
        persist_config_update(
            repository,
            cfg.model_copy(update={"style": new_style, "report_formats": updated_formats}),
        )
