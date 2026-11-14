"""Tests für knackige Berichte, Knappe-Liste-Modus, editierbare Fachlogik & Web-App-Editor."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel
from src.config.domain_loader import (
    compute_config_overrides,
    load_default_domain_config,
    render_prompt_template,
)
from src.config.settings import AppSettings
from src.domain.enums import ResearchDepth
from src.domain.exceptions import InputValidationError, LLMProviderError
from src.domain.models import ResearchRequest, SourceCitation
from src.research.gemini_client import GroundedGeneration
from src.research.orchestrator import ResearchOrchestrator
from src.research.planner import resolve_max_subtopics
from src.research.web_fetcher import SafeWebFetcher
from src.security.config_validator import (
    parse_and_validate_domain_yaml,
    validate_domain_config_payload,
)
from src.storage.executor import SqliteExecutor
from src.storage.repository import SqlResearchRepository
from streamlit.testing.v1 import AppTest

ModelT = TypeVar("ModelT", bound=BaseModel)


class _RecordingConciseLLM:
    """Zeichnet alle übergebenen Prompts auf und liefert eine knackige Synthese."""

    def __init__(self, fail_synthesis: bool = False) -> None:
        self.prompts: list[str] = []
        self.system_instructions: list[str] = []
        self.fail_synthesis = fail_synthesis

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[ModelT],
        prefer_deep: bool = False,
    ) -> ModelT:
        _ = prefer_deep
        self.prompts.append(prompt)
        self.system_instructions.append(system_instruction)
        fields = schema_cls.model_fields

        if "subtopics" in fields:
            return schema_cls.model_validate(
                {
                    "normalized_title": "Top 3 Badeseen in Bayern",
                    "objective_summary": "Knappe Liste der besten Seen.",
                    "subtopics": [
                        {"title": "Oberbayern", "guiding_question": "Q1", "search_queries": ["q1"]},
                        {"title": "Allgäu", "guiding_question": "Q2", "search_queries": ["q2"]},
                        {"title": "Franken", "guiding_question": "Q3", "search_queries": ["q3"]},
                        {"title": "Niederbayern", "guiding_question": "Q4", "search_queries": ["q4"]},
                    ],
                }
            )
        if "analysis_markdown" in fields:
            return schema_cls.model_validate(
                {
                    "analysis_markdown": "- **Walchensee**: Türkises Wasser, bis 21 °C ([Quelle](https://example.com/walchensee)).",
                    "key_facts": ["Walchensee: Türkises Bergwasser, bis 21 °C"],
                    "open_questions": [],
                }
            )
        if self.fail_synthesis:
            raise LLMProviderError("429 quota exceeded")
        return schema_cls.model_validate(
            {
                "report_title": "Top 3 Badeseen in Bayern",
                "executive_summary": "Drei kristallklare Seen ragen heraus.",
                "key_takeaways": ["Walchensee führend bei Sichttiefe"],
                "body_markdown": (
                    "# Doppelter Titel der entfernt wird\n"
                    "- **Walchensee**: Kristallklar, 20 °C ([BayernTourismus](https://example.com/walchensee))\n"
                    "- **Eibsee**: Zugspitz-Panorama, Top-Wasserqualität\n"
                    "- **Starnberger See**: S-Bahn-Anschluss ab München"
                ),
            }
        )

    def generate_grounded(
        self,
        prompt: str,
        system_instruction: str,
        use_url_context: bool = True,
        prefer_deep: bool = False,
    ) -> GroundedGeneration:
        _ = (use_url_context, prefer_deep)
        self.prompts.append(prompt)
        self.system_instructions.append(system_instruction)
        return GroundedGeneration(
            text="- **Walchensee**: Kristallklar ([BayernTourismus](https://example.com/walchensee))",
            citations=[SourceCitation(url="https://example.com/walchensee", title="BayernTourismus")],
            model_used="gemini-3.5-flash-lite",
        )

    def run_deep_research_agent(
        self,
        prompt: str,
        agent_name: str,
        on_poll_tick: Callable[[], None] | None = None,
        fallback_system_instruction: str = "",
    ) -> GroundedGeneration:
        _ = (prompt, agent_name, on_poll_tick, fallback_system_instruction)
        raise NotImplementedError


def _make_stub_fetcher() -> SafeWebFetcher:
    fetcher = SafeWebFetcher(dns_resolver=lambda _h, _p: ["93.184.216.34"])
    fetcher.fetch_many_tolerant = MagicMock(return_value=[])  # type: ignore[method-assign]
    return fetcher


class TestConciseReportsAndListMode:
    def test_natural_language_short_list_caps_subtopics_and_omits_verbose_wrappers(self) -> None:
        repo = SqlResearchRepository(SqliteExecutor(":memory:"))
        repo.initialize_schema()
        settings = AppSettings(gemini_api_key="test_key")
        llm = _RecordingConciseLLM()
        fetcher = _make_stub_fetcher()

        # Nutzer lässt Format auf "compact" und Tiefe auf "standard" (6 Teilthemen),
        # verlangt aber im Freitext explizit "nur eine knappe Liste"!
        job = repo.create_job(
            ResearchRequest(
                topic="Beste Badeseen in Bayern",
                focus_instructions="Bitte wirklich nur eine knappe Liste ohne langes Gelaber!",
                depth=ResearchDepth.STANDARD,
                report_format="compact",
            )
        )
        cfg = repo.get_domain_config()
        profile = cfg.get_depth_profile(ResearchDepth.STANDARD)
        assert resolve_max_subtopics(job, profile, cfg) == 2

        orchestrator = ResearchOrchestrator(settings, repo, llm, fetcher)
        report = orchestrator.execute_job(job.id)

        assert report is not None
        # Planer lieferte 4 Entwürfe, Listen-Modus begrenzt auf maximal 2 Teilgebiete!
        cp = repo.load_checkpoint(job.id)
        assert cp.plan is not None
        assert len(cp.plan.subtopics) == 2

        # Fokus-Vorgaben wurden an ALLE Stufen (Planer, Investigator, Synthesizer) übergeben
        assert all("nur eine knappe Liste" in p for p in llm.prompts if "Nachrecherche" not in p)

        # Kein aufgeblähter Dossier-Überbau, kein doppelter H1-Titel!
        assert "## Executive Summary" not in report.content_markdown
        assert "## Zentrale Erkenntnisse" not in report.content_markdown
        assert "## Übergreifendes Fazit" not in report.content_markdown
        assert "Doppelter Titel" not in report.content_markdown
        assert report.content_markdown.startswith("# Top 3 Badeseen in Bayern")
        assert "- **Walchensee**" in report.content_markdown
        fetcher.close()
        repo.close()

    def test_list_mode_fallback_on_429_still_produces_clean_bullet_list(self) -> None:
        repo = SqlResearchRepository(SqliteExecutor(":memory:"))
        repo.initialize_schema()
        settings = AppSettings(gemini_api_key="test_key")
        llm = _RecordingConciseLLM(fail_synthesis=True)
        fetcher = _make_stub_fetcher()

        job = repo.create_job(
            ResearchRequest(
                topic="Top 3 Badeseen – kurze Liste",
                depth=ResearchDepth.QUICK,
                report_format="list",
            )
        )
        report = ResearchOrchestrator(settings, repo, llm, fetcher).execute_job(job.id)
        assert report is not None
        assert "## 1." not in report.content_markdown
        assert "- **Walchensee**" in report.content_markdown
        fetcher.close()
        repo.close()


class TestEditableDomainConfigPersistenceAndSecurity:
    def test_persists_overrides_as_diff_drives_orchestrator_and_resets_cleanly(self) -> None:
        repo = SqlResearchRepository(SqliteExecutor(":memory:"))
        repo.initialize_schema()
        assert repo.has_custom_domain_config() is False

        default_cfg = repo.get_domain_config()
        custom_style = default_cfg.style.model_copy(
            update={
                "sources_heading_de": "## Meine Custom-Quellen",
                "include_sources_section": True,
            }
        )
        custom_prompts = default_cfg.prompts.model_copy(
            update={
                "synthesizer_system_prompt": default_cfg.prompts.synthesizer_system_prompt
                + "\nCUSTOM_MARKER_PROMPT_XYZ"
            }
        )
        edited = default_cfg.model_copy(
            update={
                "available_models": ["gemini-3.5-flash-lite", "gemini-3.5-flash"],
                "style": custom_style,
                "prompts": custom_prompts,
            }
        )
        diff = compute_config_overrides(default_cfg, edited)
        assert set(diff.keys()) == {"available_models", "style", "prompts"}

        repo.save_domain_config(edited)
        assert repo.has_custom_domain_config() is True

        reloaded = repo.get_domain_config()
        assert reloaded.style.sources_heading_de == "## Meine Custom-Quellen"
        assert "gemini-3.5-flash" in reloaded.available_models

        # Orchestrator nutzt sofort die in der DB bearbeiteten Prompts & Überschriften
        llm = _RecordingConciseLLM()
        fetcher = _make_stub_fetcher()
        job = repo.create_job(ResearchRequest(topic="Test Custom Fachlogik", depth=ResearchDepth.QUICK))
        report = ResearchOrchestrator(AppSettings(gemini_api_key="k"), repo, llm, fetcher).execute_job(job.id)
        assert report is not None
        assert "## Meine Custom-Quellen" in report.content_markdown
        assert any("CUSTOM_MARKER_PROMPT_XYZ" in s for s in llm.system_instructions)

        # Reset stellt Datei-Standardwerte wieder her
        reset_cfg = repo.reset_domain_config()
        assert repo.has_custom_domain_config() is False
        assert reset_cfg.style.sources_heading_de == "## Quellen"
        fetcher.close()
        repo.close()

    def test_safe_prompt_rendering_prevents_format_string_injection_and_preserves_json_braces(self) -> None:
        tpl = 'Thema: {topic} | Literal JSON: {"a": 1} | Attack: {topic.__class__} | Unknown: {missing_var}'
        rendered = render_prompt_template(tpl, {"topic": "Batterien"})
        assert rendered == 'Thema: Batterien | Literal JSON: {"a": 1} | Attack: {topic.__class__} | Unknown: {missing_var}'

    def test_validator_blocks_malicious_models_oversized_yaml_and_out_of_range_limits(self) -> None:
        base = load_default_domain_config().model_dump(mode="json")

        bad_model = dict(base, available_models=["gemini-3.5-flash-lite; rm -rf /"])
        with pytest.raises(InputValidationError):
            validate_domain_config_payload(bad_model)

        bad_limits = dict(base, limits={**base["limits"], "max_chars_per_scraped_page": 999_999})
        with pytest.raises(InputValidationError):
            validate_domain_config_payload(bad_limits)

        with pytest.raises(InputValidationError):
            parse_and_validate_domain_yaml("a: " + ("x" * 120_000))


def test_web_app_renders_and_saves_editable_domain_logic(tmp_path: Path) -> None:
    db_file = tmp_path / "ui_rules_test.db"
    settings = AppSettings(
        gemini_api_key="valid_test_key",
        sqlite_path=str(db_file),
        app_password="",
    )
    repo = SqlResearchRepository(SqliteExecutor(str(db_file)))
    repo.initialize_schema()

    def _entry() -> None:
        from src.ui.app import run_streamlit_app as _run

        _run()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.ui.app.load_app_settings", lambda: settings)
        mp.setattr("src.ui.views.active_jobs_view.JobDispatcher.auto_recover_stale_jobs", lambda *_a, **_kw: 0)

        at = AppTest.from_function(_entry).run()
        assert not at.exception

        # Prüfe, dass Tab "🎛️ Prompts & Rules" vorhanden ist und wechsle dorthin
        tab_labels = [t.label for t in at.tabs]
        assert "🎛️ Prompts & Rules" in tab_labels
        at.button(key="sidebar_nav_rules").click().run()
        assert not at.exception

        # Ändere die Quellen-Überschrift direkt über das Formular in der Web-App und speichere
        src_inputs = [inp for inp in at.text_input if inp.label == "Quellen-Überschrift (DE)"]
        assert len(src_inputs) == 1
        src_inputs[0].set_value("## Kurzquellen")

        save_btns = [b for b in at.button if "Stilregeln & Berichtsformate speichern" in b.label]
        assert len(save_btns) == 1
        save_btns[0].click().run()
        assert not at.exception

        assert repo.has_custom_domain_config() is True
        assert repo.get_domain_config().style.sources_heading_de == "## Kurzquellen"

        # Prüfe zusätzlich das Zurücksetzen auf Standardwerte aus der Web-App heraus
        reset_btns = [b for b in at.button if "Auf Standard zurücksetzen" in b.label]
        assert len(reset_btns) == 1
        reset_btns[0].click().run()
        assert not at.exception
        assert repo.has_custom_domain_config() is False

    repo.close()


def test_new_research_form_uses_configured_email_without_ui_input(tmp_path: Path) -> None:
    db_file = tmp_path / "ui_email_test.db"
    settings = AppSettings(
        gemini_api_key="valid_test_key",
        sqlite_path=str(db_file),
        app_password="",
        email_to="fest.definiert@example.com",
    )
    repo = SqlResearchRepository(SqliteExecutor(str(db_file)))
    repo.initialize_schema()

    def _entry() -> None:
        from src.ui.app import run_streamlit_app as _run

        _run()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.ui.app.load_app_settings", lambda: settings)
        mp.setattr("src.ui.views.new_research_view.fetch_available_gemini_models", lambda _k, **_kw: ["gemini-3.5-flash-lite"])
        mp.setattr("src.ui.views.new_research_view.JobDispatcher.dispatch", lambda *_a, **_kw: "Turbo")
        mp.setattr("src.ui.views.active_jobs_view.JobDispatcher.auto_recover_stale_jobs", lambda *_a, **_kw: 0)

        at = AppTest.from_function(_entry).run()
        assert not at.exception

        # Es darf kein manuelles E-Mail-Eingabefeld mehr im Formular existieren
        email_inputs = [i for i in at.text_input if "E-Mail" in i.label]
        assert len(email_inputs) == 0

        # Formular absenden
        topic_input = [inp for inp in at.text_input if "Recherche-Thema" in inp.label][0]
        topic_input.set_value("Recherche ohne Email-Inputfeld")
        submit_btn = [btn for btn in at.button if "Recherche starten" in btn.label][0]
        submit_btn.click().run()
        assert not at.exception

        # Erstellter Job muss automatisch die konfigurierte Zieladresse tragen
        jobs = repo.list_jobs(limit=1)
        assert len(jobs) == 1
        assert jobs[0].notify_email == "fest.definiert@example.com"

    repo.close()


def test_domain_config_all_expanders_default_collapsed(tmp_path: Path) -> None:
    db_file = tmp_path / "ui_expanders_test.db"
    settings = AppSettings(
        gemini_api_key="valid_test_key",
        sqlite_path=str(db_file),
        app_password="",
    )
    repo = SqlResearchRepository(SqliteExecutor(str(db_file)))
    repo.initialize_schema()

    def _entry() -> None:
        from src.ui.app import run_streamlit_app as _run

        _run()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.ui.app.load_app_settings", lambda: settings)
        mp.setattr("src.ui.views.active_jobs_view.JobDispatcher.auto_recover_stale_jobs", lambda *_a, **_kw: 0)

        at = AppTest.from_function(_entry).run()
        assert not at.exception

        at.session_state["main_navigation_tab"] = "🎛️ Prompts & Rules"
        at.run()
        assert not at.exception

        # Alle Expander im Tab müssen initial zugeklappt sein
        expanders = at.expander
        assert len(expanders) > 0
        for exp in expanders:
            assert exp.proto.expanded is False

    repo.close()
