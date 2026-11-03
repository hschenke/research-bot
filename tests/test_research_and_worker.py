"""Tests für Research-Orchestrator (inkl. Resume & Abbruch), WebFetcher, E-Mail & Worker-Dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel
from src.config.settings import AppSettings
from src.domain.enums import JobStatus, ResearchDepth, WorkerMode
from src.domain.exceptions import SSRFBlockedError
from src.domain.models import (
    ResearchJob,
    ResearchPlan,
    ResearchReport,
    ResearchRequest,
    SourceCitation,
    SubTopicFinding,
    SubTopicPlan,
)
from src.notifications.email_renderer import (
    build_report_app_url,
    format_duration_human,
    render_report_email_html,
)
from src.notifications.notifier import mask_email_for_log
from src.research.gemini_client import (
    GeminiInteractionsGateway,
    GroundedGeneration,
    fetch_available_gemini_models,
)
from src.research.orchestrator import ResearchOrchestrator
from src.research.web_fetcher import SafeWebFetcher, extract_clean_text_from_html
from src.storage.executor import SqliteExecutor
from src.storage.repository import SqlResearchRepository
from src.worker.dispatcher import resolve_effective_worker_mode

ModelT = TypeVar("ModelT", bound=BaseModel)


class FakeLLMGateway:
    """Deterministisches Fake-LLM ohne echte API-Calls für schnelle, isolierte Tests."""

    def __init__(self, on_step_hook: Callable[[], None] | None = None) -> None:
        self.grounded_calls = 0
        self.structured_calls = 0
        self._hook = on_step_hook

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[ModelT],
        prefer_deep: bool = False,
    ) -> ModelT:
        _ = (prompt, system_instruction, prefer_deep)
        self.structured_calls += 1
        if self._hook:
            self._hook()

        fields = schema_cls.model_fields
        if "subtopics" in fields:
            return schema_cls.model_validate(
                {
                    "normalized_title": "Analyse: Festkörperbatterien 2026",
                    "objective_summary": "Untersuchung von Energiedichte und Serienreife.",
                    "subtopics": [
                        {
                            "title": "Zellchemie",
                            "guiding_question": "Welche Elektrolyte dominieren?",
                            "search_queries": ["solid state electrolyte 2026"],
                        },
                        {
                            "title": "Serienproduktion",
                            "guiding_question": "Wann startet die Massenfertigung?",
                            "search_queries": ["solid state battery mass production"],
                        },
                        {
                            "title": "Kostenstruktur",
                            "guiding_question": "Wie hoch sind die EUR/kWh?",
                            "search_queries": ["solid state kwh cost"],
                        },
                    ],
                }
            )
        if "analysis_markdown" in fields:
            return schema_cls.model_validate(
                {
                    "analysis_markdown": "Sulfid-Elektrolyte erreichen 450 Wh/kg ([Studie](https://example.com/study)).",
                    "key_facts": ["Energiedichte 450 Wh/kg"],
                    "open_questions": [],
                }
            )
        return schema_cls.model_validate(
            {
                "report_title": "Gesamtdossier Festkörperbatterien",
                "executive_summary": "Festkörperzellen stehen vor dem industriellen Hochlauf.",
                "key_takeaways": ["450 Wh/kg erreicht", "Pilotlinien ab 2027"],
                "synthesis_conclusion": "Hohes Potenzial für Premium-EVs.",
            }
        )

    def generate_grounded(
        self,
        prompt: str,
        system_instruction: str,
        use_url_context: bool = True,
        prefer_deep: bool = False,
    ) -> GroundedGeneration:
        _ = (prompt, system_instruction, use_url_context, prefer_deep)
        self.grounded_calls += 1
        if self._hook:
            self._hook()
        return GroundedGeneration(
            text="Gefundene Fakten aus dem Web.",
            citations=[SourceCitation(url="https://example.com/study", title="Battery Paper")],
            model_used="gemini-3.8-flash",
        )

    def run_deep_research_agent(
        self,
        prompt: str,
        agent_name: str,
        on_poll_tick: Callable[[], None] | None = None,
    ) -> GroundedGeneration:
        _ = prompt
        if on_poll_tick:
            on_poll_tick()
        return GroundedGeneration(
            text="Deep Baseline Überblick.",
            citations=[SourceCitation(url="https://example.com/deep", title="Deep Source")],
            model_used=agent_name,
        )


class FakeNotifier:
    def __init__(self) -> None:
        self.notified_jobs: list[str] = []

    def notify_report_ready(self, job: ResearchJob, report: ResearchReport) -> bool:
        _ = report
        self.notified_jobs.append(job.id)
        return True


def _make_stub_fetcher() -> SafeWebFetcher:
    fetcher = SafeWebFetcher(dns_resolver=lambda _h, _p: ["93.184.216.34"])
    fetcher.fetch_many_tolerant = MagicMock(return_value=[])  # type: ignore[method-assign]
    return fetcher


class TestOrchestratorAndCheckpoints:
    def test_full_pipeline_persists_report_and_triggers_email(self) -> None:
        repo = SqlResearchRepository(SqliteExecutor(":memory:"))
        repo.initialize_schema()
        settings = AppSettings(gemini_api_key="test_key", email_to="empfaenger@domain.de")
        llm = FakeLLMGateway()
        notifier = FakeNotifier()
        fetcher = _make_stub_fetcher()

        job = repo.create_job(
            ResearchRequest(
                topic="Festkörperbatterien",
                depth=ResearchDepth.QUICK,
                notify_email="empfaenger@domain.de",
            )
        )
        orchestrator = ResearchOrchestrator(settings, repo, llm, fetcher, notifier)
        report = orchestrator.execute_job(job.id, worker_id="test-worker")

        assert report is not None
        assert "Gesamtdossier Festkörperbatterien" in report.title
        assert len(report.citations) == 1
        assert notifier.notified_jobs == [job.id]

        final_job = repo.get_job(job.id)
        assert final_job is not None
        assert final_job.status == JobStatus.COMPLETED
        assert final_job.email_sent is True
        fetcher.close()
        repo.close()

    def test_resumes_from_checkpoint_skipping_completed_subtopics(self) -> None:
        repo = SqlResearchRepository(SqliteExecutor(":memory:"))
        repo.initialize_schema()
        settings = AppSettings(gemini_api_key="test_key")
        llm = FakeLLMGateway()
        fetcher = _make_stub_fetcher()

        job = repo.create_job(
            ResearchRequest(topic="Marathon Thema", depth=ResearchDepth.QUICK)
        )
        # Simuliere einen nach Teilthema 2 von 3 abgebrochenen >1h Lauf
        plan = ResearchPlan(
            normalized_title="Marathon Plan",
            objective_summary="Drei Schritte",
            subtopics=[
                SubTopicPlan(index=1, title="T1", guiding_question="Q1"),
                SubTopicPlan(index=2, title="T2", guiding_question="Q2"),
                SubTopicPlan(index=3, title="T3", guiding_question="Q3"),
            ],
        )
        repo.save_plan_checkpoint(job.id, plan)
        repo.save_findings_checkpoint(
            job.id,
            [
                SubTopicFinding(subtopic_index=1, title="T1", analysis_markdown="A1"),
                SubTopicFinding(subtopic_index=2, title="T2", analysis_markdown="A2"),
            ],
        )

        orchestrator = ResearchOrchestrator(settings, repo, llm, fetcher)
        report = orchestrator.execute_job(job.id, worker_id="resume-worker")

        assert report is not None
        # Nur Teilthema 3 durfte einen neuen Grounded-Web-Search auslösen!
        assert llm.grounded_calls == 1
        assert "## 1. T1" in report.content_markdown
        assert "## 2. T2" in report.content_markdown
        assert "## 3. T3" in report.content_markdown
        fetcher.close()
        repo.close()

    def test_cooperative_cancellation_stops_running_job_cleanly(self) -> None:
        repo = SqlResearchRepository(SqliteExecutor(":memory:"))
        repo.initialize_schema()
        settings = AppSettings(gemini_api_key="test_key")
        job = repo.create_job(ResearchRequest(topic="Stopp Test", depth=ResearchDepth.QUICK))

        def _trigger_cancel() -> None:
            repo.request_cancellation(job.id)

        llm = FakeLLMGateway(on_step_hook=_trigger_cancel)
        fetcher = _make_stub_fetcher()
        orchestrator = ResearchOrchestrator(settings, repo, llm, fetcher)

        result = orchestrator.execute_job(job.id, worker_id="cancel-worker")
        assert result is None
        updated = repo.get_job(job.id)
        assert updated is not None
        assert updated.status == JobStatus.CANCELLED
        fetcher.close()
        repo.close()


class TestWebFetcherAndEmailSecurity:
    def test_web_fetcher_blocks_redirect_to_internal_metadata_ip(self) -> None:
        fetcher = SafeWebFetcher(dns_resolver=lambda _h, _p: ["93.184.216.34"])
        redirect_resp = MagicMock()
        redirect_resp.status_code = 302
        redirect_resp.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
        redirect_resp.__enter__.return_value = redirect_resp
        redirect_resp.__exit__.return_value = False
        fetcher._session.get = MagicMock(return_value=redirect_resp)  # type: ignore[method-assign]

        with pytest.raises(SSRFBlockedError):
            fetcher.fetch_page("https://example.com/start")
        fetcher.close()

    def test_html_extraction_strips_scripts_and_styles(self) -> None:
        raw_html = """
        <html>
          <head><title>Fachartikel</title><style>.x{color:red}</style></head>
          <body>
            <script>stealCookies();</script>
            <nav>Menü</nav>
            <article><p>Wichtiger Fakteninhalt zur Kernfusion.</p></article>
          </body>
        </html>
        """
        title, text = extract_clean_text_from_html(raw_html)
        assert title == "Fachartikel"
        assert "stealCookies" not in text
        assert "Wichtiger Fakteninhalt zur Kernfusion." in text

    def test_email_renderer_escapes_injected_script_tags_and_masks_pii(self) -> None:
        report = ResearchReport(
            job_id="job42",
            title="Test <script>alert(1)</script>",
            topic="Sicherheit",
            depth=ResearchDepth.DEEP,
            language="de",
            executive_summary="Kurzfassung",
            key_takeaways=["Fakt 1"],
            content_markdown="# Titel\n\n Böses <script>evil()</script> und [Link](javascript:alert(1)).",
            citations=[SourceCitation(url="https://example.com", title="Quelle")],
            duration_seconds=4500,
        )
        app_url = build_report_app_url("https://my-app.streamlit.app", "job42", "GeheimPW")
        html_mail = render_report_email_html(report, app_report_url=app_url)

        assert "<script>" not in html_mail
        assert "javascript:" not in html_mail
        assert "auth=ro%3A" in html_mail
        assert format_duration_human(4500) == "1 Std. 15 Min."
        assert mask_email_for_log("holger@beispiel.de") == "h***@beispiel.de"

    def test_worker_mode_auto_requires_both_github_and_turso(self) -> None:
        local_only = AppSettings(
            worker_mode=WorkerMode.AUTO,
            github_token="ghp_valid",
            github_repo="hschenke/research-bot",
            turso_url="",
            turso_auth_token="",
        )
        assert resolve_effective_worker_mode(local_only) == WorkerMode.LOCAL

        cloud_ready = AppSettings(
            worker_mode=WorkerMode.AUTO,
            github_token="ghp_valid",
            github_repo="hschenke/research-bot",
            turso_url="libsql://research-bot.turso.io",
            turso_auth_token="ey_valid_token",
        )
        assert resolve_effective_worker_mode(cloud_ready) == WorkerMode.GITHUB

    def test_fetch_available_gemini_models_defaults_and_filtering(self) -> None:
        # Leerer oder Placeholder-Key liefert Fallback-Liste
        fallback = fetch_available_gemini_models("")
        assert "gemini-3.5-flash-lite" in fallback
        assert fallback[0] == "gemini-3.5-flash-lite"

        # Mock für genai.Client.models.list
        mock_models = [
            MagicMock(name="m1", display_name="M1", supported_actions=["generateContent"]),
            MagicMock(name="m2", display_name="M2", supported_actions=["generateContent"]),
            MagicMock(name="m3", display_name="M3", supported_actions=["generateContent"]),
            MagicMock(name="m4", display_name="M4", supported_actions=["generateContent"]),
        ]
        mock_models[0].name = "models/gemini-3.8-flash"
        mock_models[1].name = "models/gemini-3.5-flash-lite"
        mock_models[2].name = "models/gemini-3.5-flash-image"  # Ausgeschlossen (Bild)
        mock_models[3].name = "models/gemini-3.6-flash"

        mock_client = MagicMock()
        mock_client.models.list.return_value = mock_models

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("google.genai.Client", lambda api_key: mock_client)
            res = fetch_available_gemini_models("valid_test_key_123")
            assert res[0] == "gemini-3.5-flash-lite"
            assert "gemini-3.8-flash" in res
            assert "gemini-3.6-flash" in res
            assert "gemini-3.5-flash-image" not in res

    def test_gateway_with_preferred_model_reorders_chain(self) -> None:
        settings = AppSettings(gemini_api_key="valid_test_key")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("google.genai.Client", lambda api_key: MagicMock())
            gw = GeminiInteractionsGateway(settings)
            assert gw._fast_chain[0] == "gemini-3.5-flash-lite"

            custom_gw = gw.with_preferred_model("gemini-3.6-flash")
            assert custom_gw._fast_chain[0] == "gemini-3.6-flash"
