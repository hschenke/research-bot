"""Tab 4: Live-Editor für Prompts, Rules, Formats und Limits."""

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
    """Zeigt alle Stilregeln, Formate, Prompts, Depth-Profile und Limits editierbar an."""
    is_admin = role == AuthRole.ADMIN
    flash = st.session_state.pop("_rules_flash_msg", None)
    if flash:
        st.success(str(flash))

    has_custom = repository.has_custom_domain_config()
    col_info, col_reset = st.columns([3.5, 1.3], vertical_alignment="center")
    with col_info:
        if has_custom:
            st.caption("✏️ **Custom Config aktiv** (DB Override)")
        else:
            st.caption("🟢 **Default Config** (`config/settings.yaml`)")
    with col_reset:
        if is_admin and has_custom and st.button(
            "🔄 Auf Standard zurücksetzen",
            key="btn_reset_domain_cfg",
            width="stretch",
        ):
            repository.reset_domain_config()
            clear_domain_editor_state()
            st.session_state["_rules_flash_msg"] = "Auf Standardwerte zurückgesetzt."
            st.session_state["_force_nav_tab"] = "rules"
            st.rerun()

    if not is_admin:
        st.caption("🔒 Read-Only: Bitte in der Sidebar als Admin einloggen, um Änderungen zu speichern.")

    sub_style, sub_prompts, sub_depths, sub_app, sub_yaml = st.tabs(
        [
            "✂️ Style & Formats",
            "🤖 Prompts",
            "📊 Depth Profiles",
            "⚙️ Limits & Defaults",
            "📄 Raw YAML",
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
        with st.expander("📝 Global Style & Override Rules", expanded=False):
            global_rules = st.text_area(
                "Global Style Rules (`global_style_rules`)",
                value=cfg.style.global_style_rules,
                height=120,
                disabled=not is_admin,
            )
            override_rules = st.text_area(
                "Format Override Rules (`format_override_rules`)",
                value=cfg.style.format_override_rules,
                height=115,
                disabled=not is_admin,
            )
            kw_raw = st.text_area(
                "Compact-List Keywords (komma- oder zeilengetrennt)",
                value=", ".join(cfg.style.compact_list_keywords),
                height=70,
                disabled=not is_admin,
            )

        with st.expander("📚 Sources & Partial Notices", expanded=False):
            col_s1, col_s2, col_s3 = st.columns([1.2, 1.4, 1.4])
            with col_s1:
                inc_sources = st.checkbox(
                    "Sources anhängen",
                    value=cfg.style.include_sources_section,
                    disabled=not is_admin,
                )
            with col_s2:
                src_de = st.text_input("Quellen-Überschrift (DE)", value=cfg.style.sources_heading_de, disabled=not is_admin)
            with col_s3:
                src_en = st.text_input("Quellen-Überschrift (EN)", value=cfg.style.sources_heading_en, disabled=not is_admin)

            part_de = st.text_input("Partial Notice (DE)", value=cfg.style.partial_notice_de, disabled=not is_admin)
            part_en = st.text_input("Partial Notice (EN)", value=cfg.style.partial_notice_en, disabled=not is_admin)

        st.markdown("**Report Formats**")
        updated_formats: dict[str, ReportFormatPreset] = {}
        for fmt_key, preset in cfg.report_formats.items():
            with st.expander(f"`{fmt_key}` – {preset.label}", expanded=False):
                c1, c2, c3 = st.columns([2.2, 1.0, 1.2])
                with c1:
                    lbl = st.text_input("Label", value=preset.label, disabled=not is_admin, key=f"dcfg_fmt_lbl_{fmt_key}")
                with c2:
                    cap = st.number_input(
                        "Max. Subtopics (0 = Auto)",
                        min_value=0,
                        max_value=20,
                        value=preset.max_subtopics_cap,
                        disabled=not is_admin,
                        key=f"dcfg_fmt_cap_{fmt_key}",
                    )
                with c3:
                    omit = st.checkbox(
                        "Direct Mode (ohne Wrapper)",
                        value=preset.omit_wrapper_sections,
                        disabled=not is_admin,
                        key=f"dcfg_fmt_omit_{fmt_key}",
                    )
                inst = st.text_area(
                    "Prompt-Instructions",
                    value=preset.instructions,
                    height=95,
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
            width="stretch",
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

