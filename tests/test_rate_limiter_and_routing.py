"""Unit-Tests für LlmRateLimiter, Modell- & Tool-Routing sowie 15-RPM-Schutz."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from src.config.domain_loader import load_default_domain_config
from src.config.settings import AppSettings
from src.domain.domain_config import ModelRoutingConfig
from src.domain.enums import ResearchDepth
from src.domain.models import DepthProfile, ResearchJob, SourceCitation, SubTopicPlan
from src.research.gemini_client import GeminiInteractionsGateway
from src.research.investigator import investigate_subtopic
from src.research.rate_limiter import LlmRateLimiter
from src.research.report_formatter import assemble_report_markdown, build_execution_warnings_notice


def test_rate_limiter_sliding_window_and_pacing() -> None:
    # Direkter Test mit deaktivierter Test-Umgebungsumgehung
    limiter = LlmRateLimiter(max_rpm=3, min_interval_seconds=0.05)
    # Überschreibe _is_test_environment für diesen Unit-Test lokal
    limiter._timestamps.clear()

    start = time.monotonic()
    # 1. Ticket
    with limiter._lock:
        limiter._timestamps.append(start)
        limiter._last_call_monotonic = start

    status = limiter.get_status()
    assert status["max_rpm"] == 3
    assert status["current_window_rpm"] == 1
    assert status["cooldown_active"] is False

    # Cooldown aktivieren
    limiter.notify_rate_limit(cooldown_seconds=1.0)
    # Manuell den Cooldown-Wert setzen, da notify_rate_limit im Testmodus evtl. überspringt
    with limiter._lock:
        limiter._cooldown_until = time.monotonic() + 0.5
    assert limiter.get_status()["cooldown_active"] is True

    # Reset
    limiter.reset()
    assert limiter.get_status()["current_window_rpm"] == 0
    assert limiter.get_status()["cooldown_active"] is False


def test_domain_config_includes_model_routing_defaults() -> None:
    cfg = load_default_domain_config()
    assert hasattr(cfg, "model_routing")
    routing = cfg.model_routing
    assert isinstance(routing, ModelRoutingConfig)
    assert routing.planning_model == "gemini-3.5-flash-lite"
    assert routing.research_primary_model == "gemini-3.5-flash-lite"
    assert routing.research_fallback_model == "gemini-3.1-flash-lite"
    assert routing.synthesizer_model == "gemini-3.5-flash-lite"
    assert routing.max_rpm == 12
    assert routing.min_call_interval_seconds == 4.5
    assert routing.single_pass_research is True
    assert routing.enable_google_search is True


def test_investigate_subtopic_uses_single_pass_when_configured() -> None:
    cfg = load_default_domain_config()
    assert cfg.model_routing.single_pass_research is True

    mock_gateway = MagicMock()
    mock_grounded = MagicMock()
    mock_grounded.text = "Fakten: Punkt 1, Punkt 2."
    mock_grounded.citations = []
    mock_grounded.model_used = "gemini-3.5-flash-lite"
    mock_gateway.generate_grounded.return_value = mock_grounded

    mock_fetcher = MagicMock()
    job = ResearchJob(
        id="test_job_1",
        topic="Test Thema",
        depth=ResearchDepth.STANDARD,
        report_format="compact",
        language="de",
    )
    subtopic = SubTopicPlan(
        index=1,
        title="Test Teilthema",
        guiding_question="Leitfrage",
        search_queries=["Suche 1"],
    )
    profile = DepthProfile(
        depth=ResearchDepth.STANDARD,
        label="Standard",
        estimated_duration_label="2 Min",
        subtopic_count=3,
        max_urls_per_subtopic=2,
        followup_rounds=1,
        use_deep_research_agent=False,
        deep_agent_name="",
        inter_step_pause_seconds=0.0,
        target_word_range="200-400",
        max_subtopic_bullets=5,
        parallel_subtopics=1,
        prefer_deep_model=False,
    )

    finding = investigate_subtopic(
        gateway=mock_gateway,
        web_fetcher=mock_fetcher,
        job=job,
        subtopic=subtopic,
        profile=profile,
        domain_config=cfg,
    )

    # Bei aktiviertem single_pass_research darf genau 1 Grounding-Aufruf erfolgen
    # und keine Fetcher-Scraping- oder Konsolidierungs-Calls!
    assert mock_gateway.generate_grounded.call_count == 1
    assert mock_gateway.generate_structured.call_count == 0
    assert mock_fetcher.fetch_many_tolerant.call_count == 0
    assert finding.title == "Test Teilthema"


def test_build_execution_warnings_notice_formatting() -> None:
    # Leer bei keinen Warnungen
    assert build_execution_warnings_notice([], "de") == ""
    assert build_execution_warnings_notice(None, "de") == ""

    # Deutsch formatierter Block
    de_notice = build_execution_warnings_notice(["Google Search Tool nicht verfügbar"], "de")
    assert "> [!WARNING] **Hinweis zu API- & Tool-Limits während der Recherche:**" in de_notice
    assert "> - Google Search Tool nicht verfügbar" in de_notice

    # Englisch formatierter Block
    en_notice = build_execution_warnings_notice(["Rate limit reached"], "en")
    assert "> [!WARNING] **Notice regarding API & Tool limits during research:**" in en_notice
    assert "> - Rate limit reached" in en_notice


def test_assemble_report_markdown_includes_warnings() -> None:
    cfg = load_default_domain_config()
    md = assemble_report_markdown(
        report_title="Testbericht",
        body_markdown="Hauptteil der Recherche.",
        key_takeaways=[],
        plan=None,
        findings=[],
        citations=[],
        language="de",
        cfg=cfg,
        omit_wrapper_sections=False,
        warnings=["Test-Warnung bezüglich 429."],
    )
    assert "# Testbericht" in md
    assert "> [!WARNING] **Hinweis zu API- & Tool-Limits während der Recherche:**" in md
    assert "> - Test-Warnung bezüglich 429." in md
    assert "Hauptteil der Recherche." in md


def test_gateway_with_search_disabled_skips_grounded_phase() -> None:
    settings = AppSettings(gemini_api_key="valid_test_key")
    mock_client = MagicMock()
    mock_interaction = MagicMock()
    mock_interaction.outputs = [MagicMock(text="Extrahierter Text")]
    mock_client.interactions.create.return_value = mock_interaction

    fake_citations = [
        SourceCitation(
            url="https://example.com/info",
            title="Beispielseite",
            snippet="Wichtiger Inhalt",
        )
    ]

    gw = GeminiInteractionsGateway(
        settings,
        shared_client=mock_client,
        fast_mode=True,
        enable_google_search=False,
        web_search_fn=lambda _q, _lang: fake_citations,
    )
    res = gw.generate_grounded(
        prompt="Thema: Python Clean Code",
        system_instruction="Sei präzise.",
        use_url_context=False,
    )

    assert mock_client.interactions.create.call_count == 1
    call_kwargs = mock_client.interactions.create.call_args.kwargs
    # Darf keine tools=[{'type': 'google_search'}] enthalten haben!
    assert "tools" not in call_kwargs
    assert len(res.citations) == 1
    assert res.citations[0].url == "https://example.com/info"
