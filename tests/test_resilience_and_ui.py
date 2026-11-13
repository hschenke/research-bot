"""Tests für 3-Fehler-Limit, 429-Web-Fallback, Teilergebnisse, Live-Aktionsanzeige & Tab-Stabilität."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel
from src.config.constants import (
    DEPTH_PROFILES,
    LLM_DEFAULT_TIMEOUT_SECONDS,
    LLM_FAST_TIMEOUT_SECONDS,
    MIN_LLM_TIMEOUT_SECONDS,
)
from src.config.settings import AppSettings
from src.domain.enums import JobPhase, JobStatus, ResearchDepth
from src.domain.exceptions import LLMProviderError
from src.domain.models import ResearchReport, ResearchRequest, SourceCitation
from src.research.gemini_client import GeminiInteractionsGateway, GroundedGeneration
from src.research.llm_helpers import create_fast_genai_client
from src.research.orchestrator import ResearchOrchestrator
from src.research.web_fetcher import SafeWebFetcher
from src.research.web_search_fallback import _extract_ddg_target_url
from src.storage.executor import SqliteExecutor
from src.storage.repository import SqlResearchRepository
from streamlit.testing.v1 import AppTest

ModelT = TypeVar("ModelT", bound=BaseModel)


class _QuotaOnThirdSubtopicLLM:
    """Simuliert 2 erfolgreiche Teilgebiete und einen 429-Quota-Abbruch beim 3. Teilgebiet."""

    def __init__(self) -> None:
        self.grounded_calls = 0

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[ModelT],
        prefer_deep: bool = False,
    ) -> ModelT:
        _ = (prompt, system_instruction, prefer_deep)
        if "subtopics" in schema_cls.model_fields:
            return schema_cls.model_validate(
                {
                    "normalized_title": "Beste Badegebiete Europas",
                    "objective_summary": "Wasserqualität, Infrastruktur und Klima.",
                    "subtopics": [
                        {"title": "Wasserqualität", "guiding_question": "Q1", "search_queries": ["s1"]},
                        {"title": "Infrastruktur", "guiding_question": "Q2", "search_queries": ["s2"]},
                        {"title": "Risikoanalyse", "guiding_question": "Q3", "search_queries": ["s3"]},
                    ],
                }
            )
        raise LLMProviderError("Error code: 429 - You exceeded your current quota (too_many_requests)")

    def generate_grounded(
        self,
        prompt: str,
        system_instruction: str,
        use_url_context: bool = True,
        prefer_deep: bool = False,
    ) -> GroundedGeneration:
        _ = (system_instruction, use_url_context, prefer_deep)
        self.grounded_calls += 1
        if "Teilgebiet 3" in prompt:
            raise LLMProviderError(
                "Error code: 429 - {'error': {'message': 'You exceeded your current quota', 'code': 'too_many_requests'}}"
            )
        return GroundedGeneration(
            text="Verifizierte Fakten zum Badegebiet.\n- Exzellente Wasserqualität nach EU-Norm.",
            citations=[SourceCitation(url="https://example.org/badesee", title="EU Badegewässerbericht")],
            model_used="gemini-3.5-flash-lite",
        )

    def run_deep_research_agent(
        self,
        prompt: str,
        agent_name: str,
        on_poll_tick: Callable[[], None] | None = None,
    ) -> GroundedGeneration:
        _ = (prompt, agent_name, on_poll_tick)
        raise NotImplementedError


def test_fast_genai_client_disables_hidden_sdk_retry_backoff() -> None:
    client = create_fast_genai_client("dummy_test_api_key_without_network")
    retry_cfg = client.interactions.sdk_configuration.retry_config
    assert retry_cfg.strategy == "none"


def test_gateway_caps_429_at_three_attempts_and_switches_to_external_web_fallback() -> None:
    settings = AppSettings(gemini_api_key="valid_test_key")
    mock_client = MagicMock()
    quota_err = RuntimeError(
        "Error code: 429 - {'error': {'message': 'You exceeded your current quota', 'code': 'too_many_requests'}}"
    )
    mock_client.interactions.create.side_effect = quota_err

    recorded_actions: list[str] = []
    fake_citations = [
        SourceCitation(
            url="https://de.wikipedia.org/wiki/Badesee",
            title="Wikipedia: Badesee",
            snippet="Badeseen mit ausgezeichneter Wasserqualität in Alpennähe.",
        )
    ]

    gw = GeminiInteractionsGateway(
        settings,
        shared_client=mock_client,
        on_action=recorded_actions.append,
        fast_mode=True,
        web_search_fn=lambda _q, _lang: fake_citations,
    )
    res = gw.generate_grounded(
        prompt="Gesamtthema: bestes gebiet zum baden\nSuchfokus: Badeseen Wasserqualität",
        system_instruction="Antworte präzise.",
        use_url_context=False,
        prefer_deep=False,
    )

    # 1 schneller Grounded-Versuch (sofortiger Abbruch bei 429 statt 3x Sleep-Schleifen) + max. 2 schnelle Untooled-Versuche = 3 Calls
    assert mock_client.interactions.create.call_count == 3
    assert gw._quota_state.is_grounding_exhausted() is True
    assert len(gw.get_warnings()) >= 1
    assert "Google Search Tool" in gw.get_warnings()[0]
    assert res.model_used == "web-fallback-extractive"
    assert len(res.citations) == 1
    assert "Wikipedia: Badesee" in res.text
    assert any("Alternative Methode" in a for a in recorded_actions)


def test_ddg_redirect_unpacking_sanitizes_urls() -> None:
    raw = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.umweltbundesamt.de%2Fbadegewaesser&rut=abc"
    assert _extract_ddg_target_url(raw) == "https://www.umweltbundesamt.de/badegewaesser"
    evil = "//duckduckgo.com/l/?uddg=javascript%3Aalert(1)"
    assert _extract_ddg_target_url(evil) == "#"


def test_live_action_updates_status_without_polluting_progress_log() -> None:
    repo = SqlResearchRepository(SqliteExecutor(":memory:"))
    repo.initialize_schema()
    job = repo.create_job(ResearchRequest(topic="Live-Action Test"))
    repo.claim_job(job.id, worker_id="w1")

    repo.update_progress(
        job.id,
        phase=JobPhase.INVESTIGATING,
        progress_percent=50,
        message="Teilgebiet abgeschlossen (1/3): Wasserqualität",
        live_status_override="[Teilgebiet 2/3] 🌐 KI-Websuche via gemini-3.5-flash-lite...",
    )
    mid_job = repo.get_job(job.id)
    assert mid_job is not None
    assert mid_job.status_message == "[Teilgebiet 2/3] 🌐 KI-Websuche via gemini-3.5-flash-lite..."
    assert mid_job.progress_log[-1].message == "Teilgebiet abgeschlossen (1/3): Wasserqualität"
    log_count_before = len(mid_job.progress_log)

    repo.update_live_action(job.id, "[Teilgebiet 2/3] ⚡ API-Limit (429) – wechsle Modell (2/3)...")
    after_job = repo.get_job(job.id)
    assert after_job is not None
    assert after_job.status_message == "[Teilgebiet 2/3] ⚡ API-Limit (429) – wechsle Modell (2/3)..."
    assert len(after_job.progress_log) == log_count_before
    repo.close()


def test_orchestrator_completes_with_partial_result_on_429_instead_of_failing() -> None:
    repo = SqlResearchRepository(SqliteExecutor(":memory:"))
    repo.initialize_schema()
    settings = AppSettings(gemini_api_key="test_key")
    fetcher = SafeWebFetcher(dns_resolver=lambda _h, _p: ["93.184.216.34"])
    fetcher.fetch_many_tolerant = MagicMock(return_value=[])  # type: ignore[method-assign]

    job = repo.create_job(ResearchRequest(topic="bestes gebiet zum baden", depth=ResearchDepth.QUICK))
    orchestrator = ResearchOrchestrator(settings, repo, _QuotaOnThirdSubtopicLLM(), fetcher)
    report = orchestrator.execute_job(job.id, worker_id="quick-worker")

    assert report is not None
    assert "Hinweis (Teilergebnis)" in report.content_markdown
    assert "## 1. Wasserqualität" in report.content_markdown
    assert "## 2. Infrastruktur" in report.content_markdown

    final_job = repo.get_job(job.id)
    assert final_job is not None
    assert final_job.status == JobStatus.COMPLETED
    assert "Teilergebnis" in final_job.status_message
    assert "(2/3 Teilgebiete)" in final_job.status_message
    fetcher.close()
    repo.close()


def test_stop_and_resume_buttons_stay_on_active_jobs_tab(tmp_path: Path) -> None:
    db_file = tmp_path / "ui_nav_test.db"
    settings = AppSettings(
        gemini_api_key="valid_test_key",
        sqlite_path=str(db_file),
        app_password="",
    )
    repo = SqlResearchRepository(SqliteExecutor(str(db_file)))
    repo.initialize_schema()
    job = repo.create_job(ResearchRequest(topic="Tab-Bleibe-Test", depth=ResearchDepth.QUICK))
    repo.claim_job(job.id, worker_id="local-w1")

    def _entry() -> None:
        from src.ui.app import run_streamlit_app as _run

        _run()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.ui.app.load_app_settings", lambda: settings)
        mp.setattr("src.ui.views.new_research_view.fetch_available_gemini_models", lambda _k: ["gemini-3.5-flash-lite"])
        mp.setattr("src.ui.views.active_jobs_view.JobDispatcher.dispatch", lambda *_a, **_kw: "Turbo")
        mp.setattr("src.ui.views.active_jobs_view.JobDispatcher.stop_job_execution", lambda *_a, **_kw: None)
        mp.setattr("src.ui.views.active_jobs_view.JobDispatcher.auto_recover_stale_jobs", lambda *_a, **_kw: 0)

        at = AppTest.from_function(_entry).run()

        # Klick auf "⏹️ Stop" (Auftrag wird abgebrochen)
        at.button(key=f"cancel_{job.id}").click().run()
        cancelled_job = repo.get_job(job.id)
        assert cancelled_job is not None
        assert cancelled_job.status == JobStatus.CANCELLED
        assert at.session_state["main_navigation_tab"] == "⏳ Jobs"

        # Klick auf "🔁 Fortsetzen" (Auftrag wird erneut eingereiht)
        at.button(key=f"retry_{job.id}").click().run()
        resumed_job = repo.get_job(job.id)
        assert resumed_job is not None
        assert resumed_job.status == JobStatus.QUEUED
        assert at.session_state["main_navigation_tab"] == "⏳ Jobs"

    repo.close()


def test_structured_call_retries_transient_timeouts_on_primary_model() -> None:
    settings = AppSettings(gemini_api_key="valid_test_key")
    mock_client = MagicMock()
    ok_interaction = MagicMock()
    ok_interaction.output_text = '{"normalized_title": "T", "objective_summary": "O", "subtopics": []}'
    ok_interaction.steps = []

    timeout_err = RuntimeError("Request timed out. This is a client-side timeout.")
    mock_client.interactions.create.side_effect = [
        timeout_err,
        timeout_err,
        ok_interaction,
    ]

    class _DummySchema(BaseModel):
        normalized_title: str
        objective_summary: str
        subtopics: list[str]

    recorded_actions: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.research.gemini_client.compute_backoff_seconds", lambda _a: 0.001)
        gw = GeminiInteractionsGateway(
            settings,
            shared_client=mock_client,
            on_action=recorded_actions.append,
        )
        res = gw.generate_structured(
            prompt="Erstelle Struktur",
            system_instruction="System",
            schema_cls=_DummySchema,
        )

    assert res.normalized_title == "T"
    assert mock_client.interactions.create.call_count == 3
    # Alle 3 Versuche liefen auf dem Primärmodell gemini-3.5-flash-lite
    for call in mock_client.interactions.create.call_args_list:
        assert call.kwargs["model"] == "gemini-3.5-flash-lite"


def test_timeout_for_guarantees_minimum_seconds_and_clearing_deadline() -> None:
    settings = AppSettings(gemini_api_key="valid_test_key")
    mock_client = MagicMock()
    # Fast abgelaufene Deadline (nur noch 2 Sekunden übrig)
    tight_deadline = time.monotonic() + 2.0
    gw = GeminiInteractionsGateway(
        settings,
        shared_client=mock_client,
        deadline_monotonic=tight_deadline,
    )
    # Muss mindestens MIN_LLM_TIMEOUT_SECONDS garantieren und nicht auf 2s erdrosseln
    t = gw._timeout_for(prefer_deep=False)
    assert t >= MIN_LLM_TIMEOUT_SECONDS

    # Synthese-Reset: Explizites deadline_monotonic=None muss Deadline aufheben
    synth_gw = gw.with_preferred_model("gemini-3.5-flash-lite", deadline_monotonic=None)
    assert synth_gw._deadline_monotonic is None


def test_restored_workload_and_timeout_headroom() -> None:
    settings = AppSettings(gemini_api_key="valid_test_key")
    mock_client = MagicMock()
    gw = GeminiInteractionsGateway(settings, shared_client=mock_client)

    # Standard / Fast Timeout muss mindestens 60 Sekunden Headroom bieten
    assert gw._timeout_for(prefer_deep=False) == LLM_FAST_TIMEOUT_SECONDS
    assert gw._timeout_for(prefer_deep=False) >= 60.0

    # Deep / Synthese Timeout muss 120 Sekunden Headroom bieten
    assert gw._timeout_for(prefer_deep=True) == LLM_DEFAULT_TIMEOUT_SECONDS
    assert gw._timeout_for(prefer_deep=True) >= 120.0

    # Workload Profile auf bewährtem Standard
    assert DEPTH_PROFILES[ResearchDepth.STANDARD].subtopic_count == 6
    assert DEPTH_PROFILES[ResearchDepth.QUICK].subtopic_count == 3


def test_job_submission_switches_to_active_jobs_tab_and_shows_flash(tmp_path: Path) -> None:
    db_file = tmp_path / "ui_submit_test.db"
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
        mp.setattr("src.ui.views.new_research_view.fetch_available_gemini_models", lambda _k, **_kw: ["gemini-3.5-flash-lite"])
        mp.setattr("src.ui.views.new_research_view.JobDispatcher.dispatch", lambda *_a, **_kw: "Turbo")
        mp.setattr("src.ui.views.active_jobs_view.JobDispatcher.auto_recover_stale_jobs", lambda *_a, **_kw: 0)

        at = AppTest.from_function(_entry).run()
        assert not at.exception
        assert at.session_state["main_navigation_tab"] == "🚀 Neue Recherche"

        # Formular ausfüllen und absenden
        topic_input = [inp for inp in at.text_input if "Recherche-Thema" in inp.label][0]
        topic_input.set_value("Test Thema für Job-Navigation")

        submit_btn = [btn for btn in at.button if "Recherche starten" in btn.label][0]
        submit_btn.click().run()
        assert not at.exception

        # App muss sofort zum Tab '⏳ Jobs' gewechselt sein
        assert at.session_state["main_navigation_tab"] == "⏳ Jobs"
        assert at.query_params.get("tab") == "jobs"

        # Flash-Meldung muss im Jobs-Tab sichtbar sein
        success_boxes = [s.value for s in at.success]
        assert any("erfolgreich gestartet" in s for s in success_boxes)

    repo.close()


def test_jump_to_jobs_fallback_button_has_no_broken_markdown_link(tmp_path: Path) -> None:
    db_file = tmp_path / "ui_jump_btn_test.db"
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
        mp.setattr("src.ui.views.new_research_view.fetch_available_gemini_models", lambda _k, **_kw: ["gemini-3.5-flash-lite"])
        mp.setattr("src.ui.views.active_jobs_view.JobDispatcher.auto_recover_stale_jobs", lambda *_a, **_kw: 0)

        at = AppTest.from_function(_entry)
        at.session_state["_new_job_started"] = True
        at.run()
        assert not at.exception

        # Sicherstellen, dass kein kaputter relativer Markdown-Link mehr im Success-Text steckt
        for s in at.success:
            assert "[👉 Zum laufenden Job]" not in s.value
            assert "?tab=jobs" not in s.value

        # Klick auf den Button wechselt zuverlässig den Tab
        jump_btn = at.button(key="btn_jump_to_jobs")
        assert jump_btn is not None
        jump_btn.click().run()
        assert not at.exception
        assert at.session_state["main_navigation_tab"] == "⏳ Jobs"

    repo.close()


def test_completed_job_report_button_switches_to_reports_tab(tmp_path: Path) -> None:
    db_file = tmp_path / "ui_report_btn_test.db"
    settings = AppSettings(
        gemini_api_key="valid_test_key",
        sqlite_path=str(db_file),
        app_password="",
    )
    repo = SqlResearchRepository(SqliteExecutor(str(db_file)))
    repo.initialize_schema()
    job = repo.create_job(ResearchRequest(topic="Fertiger Test-Bericht", depth=ResearchDepth.QUICK))
    repo.claim_job(job.id, worker_id="w1")
    repo.mark_job_completed(
        job.id,
        report=ResearchReport(
            job_id=job.id,
            topic="Fertiger Test-Bericht",
            title="Fertiger Test-Bericht",
            executive_summary="Alles super.",
            content_markdown="## Zusammenfassung\nAlles super.",
            citations=[],
            duration_seconds=12,
            depth=ResearchDepth.QUICK,
            language="de",
            model_used="gemini-3.5-flash-lite",
        ),
    )

    def _entry() -> None:
        from src.ui.app import run_streamlit_app as _run

        _run()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.ui.app.load_app_settings", lambda: settings)
        mp.setattr("src.ui.views.new_research_view.fetch_available_gemini_models", lambda _k, **_kw: ["gemini-3.5-flash-lite"])
        mp.setattr("src.ui.views.active_jobs_view.JobDispatcher.auto_recover_stale_jobs", lambda *_a, **_kw: 0)

        at = AppTest.from_function(_entry).run()
        assert not at.exception

        # Klick auf "📄 Report" beim fertigen Job in der Liste
        rep_btn = at.button(key=f"view_rep_{job.id}")
        assert rep_btn is not None
        rep_btn.click().run()
        assert not at.exception

        # Tab muss zu '📚 Reports' gewechselt sein und job_id in den Query-Params gesetzt sein
        assert at.session_state["main_navigation_tab"] == "📚 Reports"
        assert at.query_params.get("tab") == "reports"
        assert at.query_params.get("job_id") == str(job.id)

    repo.close()

