"""Teil-Editor für Prompts, Fallback-Templates und Raw-YAML in der Web-App."""

from __future__ import annotations

import streamlit as st

from src.config.domain_loader import dump_domain_config_yaml
from src.domain.domain_config import (
    DomainConfig,
    FallbackSubtopicTemplate,
    FallbackTemplatesConfig,
    PromptTemplatesConfig,
)
from src.domain.exceptions import InputValidationError
from src.security.config_validator import (
    parse_and_validate_domain_yaml,
    validate_domain_config_payload,
)
from src.storage.base_repository import ResearchRepository


def render_prompts_tab(
    domain_config: DomainConfig,
    repository: ResearchRepository,
    is_admin: bool,
) -> None:
    """Rendert alle System- und User-Prompts sowie Fallback-Templates editierbar."""
    st.caption(
        "Platzhalter: `{topic}`, `{focus_block}`, `{style_rules}`, "
        "`{format_override_rules}`, `{max_subtopics}`, `{target_word_range}`."
    )
    p = domain_config.prompts

    with st.form("form_domain_prompts", clear_on_submit=False):
        with st.expander("1️⃣ Planner Prompts", expanded=False):
            planner_sys = st.text_area(
                "Planner System (`planner_system_prompt`)",
                value=p.planner_system_prompt,
                height=110,
                disabled=not is_admin,
            )
            planner_usr = st.text_area(
                "Planner User (`planner_user_prompt`)",
                value=p.planner_user_prompt,
                height=175,
                disabled=not is_admin,
            )

        with st.expander("2️⃣ Deep Baseline Prompts", expanded=False):
            base_usr = st.text_area(
                "Deep Agent (`deep_baseline_prompt`)",
                value=p.deep_baseline_prompt,
                height=100,
                disabled=not is_admin,
            )
            base_fb = st.text_area(
                "Fallback System (`deep_baseline_fallback_system_prompt`)",
                value=p.deep_baseline_fallback_system_prompt,
                height=75,
                disabled=not is_admin,
            )

        with st.expander("3️⃣ Investigator & Consolidation Prompts", expanded=False):
            q_sys = st.text_area(
                "Quick System (`investigator_quick_system_prompt`)",
                value=p.investigator_quick_system_prompt,
                height=90,
                disabled=not is_admin,
            )
            q_usr = st.text_area(
                "Quick User (`investigator_quick_user_prompt`)",
                value=p.investigator_quick_user_prompt,
                height=150,
                disabled=not is_admin,
            )
            d_sys = st.text_area(
                "Deep System (`investigator_deep_system_prompt`)",
                value=p.investigator_deep_system_prompt,
                height=85,
                disabled=not is_admin,
            )
            d_usr = st.text_area(
                "Deep User (`investigator_deep_user_prompt`)",
                value=p.investigator_deep_user_prompt,
                height=150,
                disabled=not is_admin,
            )
            c_sys = st.text_area(
                "Consolidation System (`investigator_consolidation_system_prompt`)",
                value=p.investigator_consolidation_system_prompt,
                height=85,
                disabled=not is_admin,
            )
            c_usr = st.text_area(
                "Consolidation User (`investigator_consolidation_user_prompt`)",
                value=p.investigator_consolidation_user_prompt,
                height=185,
                disabled=not is_admin,
            )
            f_usr = st.text_area(
                "Follow-up User (`investigator_followup_user_prompt`)",
                value=p.investigator_followup_user_prompt,
                height=110,
                disabled=not is_admin,
            )

        with st.expander("4️⃣ Synthesizer Prompts", expanded=False):
            s_sys = st.text_area(
                "Synthesizer System (`synthesizer_system_prompt`)",
                value=p.synthesizer_system_prompt,
                height=120,
                disabled=not is_admin,
            )
            s_usr = st.text_area(
                "Synthesizer User (`synthesizer_user_prompt`)",
                value=p.synthesizer_user_prompt,
                height=240,
                disabled=not is_admin,
            )

        fb_de_vals, fb_en_vals = _render_fallback_templates_inputs(domain_config, is_admin)

        saved = st.form_submit_button(
            "💾 Prompts speichern",
            type="primary",
            width="stretch",
            disabled=not is_admin,
        )

    if saved and is_admin:
        updated_prompts = PromptTemplatesConfig(
            planner_system_prompt=planner_sys,
            planner_user_prompt=planner_usr,
            deep_baseline_prompt=base_usr,
            deep_baseline_fallback_system_prompt=base_fb,
            investigator_quick_system_prompt=q_sys,
            investigator_quick_user_prompt=q_usr,
            investigator_deep_system_prompt=d_sys,
            investigator_deep_user_prompt=d_usr,
            investigator_consolidation_system_prompt=c_sys,
            investigator_consolidation_user_prompt=c_usr,
            investigator_followup_user_prompt=f_usr,
            synthesizer_system_prompt=s_sys,
            synthesizer_user_prompt=s_usr,
        )
        updated_fb = FallbackTemplatesConfig(de=fb_de_vals, en=fb_en_vals)
        persist_config_update(
            repository,
            domain_config.model_copy(
                update={"prompts": updated_prompts, "fallback_plan_templates": updated_fb}
            ),
        )


def _render_fallback_templates_inputs(
    domain_config: DomainConfig,
    is_admin: bool,
) -> tuple[list[FallbackSubtopicTemplate], list[FallbackSubtopicTemplate]]:
    with st.expander("5️⃣ Fallback Templates (Offline / 429)", expanded=False):
        de_out = _render_lang_fallback_list("de", "Deutsch (`de`)", domain_config.fallback_plan_templates.de, is_admin)
        en_out = _render_lang_fallback_list("en", "English (`en`)", domain_config.fallback_plan_templates.en, is_admin)
    return de_out, en_out


def _render_lang_fallback_list(
    lang: str,
    heading: str,
    items: list[FallbackSubtopicTemplate],
    is_admin: bool,
) -> list[FallbackSubtopicTemplate]:
    st.markdown(f"**{heading}**")
    prefix = lang.upper()
    out: list[FallbackSubtopicTemplate] = []
    for idx, item in enumerate(items):
        t = st.text_input(f"{prefix} Title #{idx + 1}", value=item.title, disabled=not is_admin, key=f"dcfg_fb_{lang}_t_{idx}")
        q = st.text_input(f"{prefix} Question #{idx + 1}", value=item.guiding_question, disabled=not is_admin, key=f"dcfg_fb_{lang}_q_{idx}")
        sq = st.text_input(
            f"{prefix} Queries #{idx + 1} (comma-separated)",
            value=", ".join(item.search_queries),
            disabled=not is_admin,
            key=f"dcfg_fb_{lang}_s_{idx}",
        )
        queries = [x.strip() for x in sq.split(",") if x.strip()] or ["{topic}"]
        out.append(FallbackSubtopicTemplate(title=t, guiding_question=q, search_queries=queries))
    return out


def render_yaml_tab(
    domain_config: DomainConfig,
    repository: ResearchRepository,
    is_admin: bool,
) -> None:
    """Rendert die vollständige Konfiguration als YAML."""
    yaml_str = dump_domain_config_yaml(domain_config)
    st.download_button(
        "⬇️ Export `settings.yaml`",
        data=yaml_str.encode("utf-8"),
        file_name="settings.yaml",
        mime="text/yaml",
    )

    with st.form("form_domain_raw_yaml", clear_on_submit=False):
        with st.expander("📄 YAML Editor", expanded=False):
            edited_yaml = st.text_area(
                "YAML Config",
                value=yaml_str,
                height=440,
                disabled=not is_admin,
            )
        submitted = st.form_submit_button(
            "💾 YAML validieren & speichern",
            type="primary",
            width="stretch",
            disabled=not is_admin,
        )

    if submitted and is_admin:
        try:
            parsed_cfg = parse_and_validate_domain_yaml(edited_yaml)
            repository.save_domain_config(parsed_cfg)
            clear_domain_editor_state()
            st.session_state["_rules_flash_msg"] = "✅ YAML gespeichert."
            st.session_state["_force_nav_tab"] = "rules"
            st.rerun()
        except InputValidationError as exc:
            st.error(f"Validierungsfehler: {exc}")


def persist_config_update(repository: ResearchRepository, candidate: DomainConfig) -> None:
    try:
        validated = validate_domain_config_payload(candidate.model_dump(mode="json"))
        repository.save_domain_config(validated)
        st.session_state["_rules_flash_msg"] = "✅ Einstellungen gespeichert."
        st.session_state["_force_nav_tab"] = "rules"
        st.rerun()
    except InputValidationError as exc:
        st.error(f"Ungültige Eingabe: {exc}")


def clear_domain_editor_state() -> None:
    """Entfernt gecachte Formular-Keys, damit Reset/YAML-Import sofort alle Felder aktualisiert."""
    for state_key in [k for k in st.session_state if str(k).startswith("dcfg_")]:
        st.session_state.pop(state_key, None)

