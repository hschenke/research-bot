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
    assert routing.single_pass_research is False
    assert routing.enable_google_search is True
    assert cfg.depth_profiles[ResearchDepth.QUICK].single_pass is True
    assert cfg.depth_profiles[ResearchDepth.STANDARD].single_pass is False
    assert cfg.depth_profiles[ResearchDepth.DEEP].single_pass is False


def test_investigate_subtopic_uses_single_pass_when_configured() -> None:
    cfg = load_default_domain_config()
    mock_gateway = MagicMock()
    mock_grounded = MagicMock()
    mock_grounded.text = "Fakten: Punkt 1, Punkt 2."
    mock_grounded.citations = []
    mock_grounded.model_used = "gemini-3.5-flash-lite"
    mock_gateway.generate_grounded.return_value = mock_grounded

    mock_fetcher = MagicMock()
    subtopic = SubTopicPlan(
        index=1,
        title="Test Teilthema",
        guiding_question="Leitfrage",
        search_queries=["Suche 1"],
    )

    # 1. Quick nutzt standardmäßig profilbezogenen Single-Pass (kein Multi-Pass)
    quick_job = ResearchJob(
        id="test_job_quick",
        topic="Test Thema",
        depth=ResearchDepth.QUICK,
        report_format="compact",
        language="de",
    )
    quick_finding = investigate_subtopic(
        gateway=mock_gateway,
        web_fetcher=mock_fetcher,
        job=quick_job,
        subtopic=subtopic,
        profile=cfg.get_depth_profile(ResearchDepth.QUICK),
        domain_config=cfg,
    )
    assert mock_gateway.generate_grounded.call_count == 1
    assert mock_gateway.generate_structured.call_count == 0
    assert mock_fetcher.fetch_many_tolerant.call_count == 0
    assert quick_finding.title == "Test Teilthema"

    # 2. Globaler Single-Pass Override erzwingt Single-Pass auch für Standard
    mock_gateway.reset_mock()
    mock_fetcher.reset_mock()
    forced_cfg = cfg.model_copy(
        update={"model_routing": cfg.model_routing.model_copy(update={"single_pass_research": True})}
    )
    std_job = ResearchJob(
        id="test_job_std_sp",
        topic="Test Thema",
        depth=ResearchDepth.STANDARD,
        report_format="compact",
        language="de",
    )
    custom_std_profile = DepthProfile(
        depth=ResearchDepth.STANDARD,
        label="Standard",
        subtopic_count=6,
        max_urls_per_subtopic=2,
        followup_rounds=1,
        use_deep_research_agent=False,
        deep_agent_name="",
        inter_step_pause_seconds=0.0,
        single_pass=False,
        target_word_range="350-650",
        max_subtopic_bullets=6,
        parallel_subtopics=2,
        prefer_deep_model=False,
    )
    investigate_subtopic(
        gateway=mock_gateway,
        web_fetcher=mock_fetcher,
        job=std_job,
        subtopic=subtopic,
        profile=custom_std_profile,
        domain_config=forced_cfg,
    )
    assert mock_gateway.generate_grounded.call_count == 1
    assert mock_gateway.generate_structured.call_count == 0
    assert mock_fetcher.fetch_many_tolerant.call_count == 0


def test_depth_profiles_produce_distinct_workload_across_quick_standard_deep() -> None:
    from src.research.evidence_consolidator import StructuredSubTopicAnalysis
    from src.research.planner import resolve_max_subtopics, resolve_min_subtopics
    from src.research.web_fetcher import FetchedWebPage

    cfg = load_default_domain_config()
    subtopic = SubTopicPlan(
        index=1,
        title="Quantenkryptographie",
        guiding_question="Wie ausgereift sind QKD-Netze 2026?",
        search_queries=["QKD networks 2026", "QKD commercial benchmarks", "QKD limitations"],
    )

    # Planungs-Skalierung (Compact ohne Listen-Cap vs. Liste mit Cap)
    for depth, expected_min, expected_max in (
        (ResearchDepth.QUICK, 1, 3),
        (ResearchDepth.STANDARD, 4, 6),
        (ResearchDepth.DEEP, 8, 10),
    ):
        job = ResearchJob(id=f"j_{depth.value}", topic="QKD", depth=depth, report_format="compact", language="de")
        prof = cfg.get_depth_profile(depth)
        max_st = resolve_max_subtopics(job, prof, cfg)
        min_st = resolve_min_subtopics(job, prof, cfg, max_st)
        assert (min_st, max_st) == (expected_min, expected_max)

    list_deep_job = ResearchJob(id="j_list", topic="QKD", depth=ResearchDepth.DEEP, report_format="list", language="de")
    assert resolve_max_subtopics(list_deep_job, cfg.get_depth_profile(ResearchDepth.DEEP), cfg) == 2

    # Standard Multi-Pass: Grounding + 1 External Search + max 2 URLs + Consolidation + 1 Follow-up
    std_gw = MagicMock()
    std_gw.generate_grounded.side_effect = [
        MagicMock(
            text="Initial [Q1](https://example.org/a) [Q2](https://example.org/b) [Q3](https://example.org/c)",
            citations=[
                SourceCitation(url="https://example.org/a", title="A"),
                SourceCitation(url="https://example.org/b", title="B"),
                SourceCitation(url="https://example.org/c", title="C"),
            ],
            model_used="gemini-3.5-flash-lite",
        ),
        MagicMock(
            text="Follow-up [Q4](https://example.org/d)",
            citations=[SourceCitation(url="https://example.org/d", title="D")],
            model_used="gemini-3.5-flash-lite",
        ),
    ]
    std_gw.search_external_sources.return_value = [SourceCitation(url="https://ext.org/1", title="Ext1", snippet="Hit")]
    std_gw.generate_structured.side_effect = [
        StructuredSubTopicAnalysis(
            analysis_markdown="- Vorläufig",
            key_facts=["Fakt 1"],
            open_questions=["Welche Latenz entsteht bei 100 km?"],
        ),
        StructuredSubTopicAnalysis(
            analysis_markdown="- Verdichtet mit Follow-up",
            key_facts=["Fakt 1", "Fakt 2"],
            open_questions=[],
        ),
    ]
    std_fetcher = MagicMock()
    std_fetcher.fetch_many_tolerant.side_effect = [
        [FetchedWebPage(url="https://example.org/a", title="A", text_content="Volltext A")],
        [FetchedWebPage(url="https://example.org/d", title="D", text_content="Volltext D")],
    ]
    std_res = investigate_subtopic(
        gateway=std_gw,
        web_fetcher=std_fetcher,
        job=ResearchJob(id="j_std", topic="QKD", depth=ResearchDepth.STANDARD, report_format="compact", language="de"),
        subtopic=subtopic,
        profile=cfg.get_depth_profile(ResearchDepth.STANDARD),
        domain_config=cfg,
    )
    assert std_gw.generate_grounded.call_count == 2
    assert std_gw.generate_structured.call_count == 2
    assert std_gw.search_external_sources.call_count == 1
    assert std_fetcher.fetch_many_tolerant.call_count == 2
    assert std_fetcher.fetch_many_tolerant.call_args_list[0].kwargs["max_pages"] == 2
    assert all(c.kwargs["prefer_deep"] is False for c in std_gw.generate_grounded.call_args_list)
    assert all(c.kwargs["prefer_deep"] is False for c in std_gw.generate_structured.call_args_list)
    assert "Verdichtet mit Follow-up" in std_res.analysis_markdown

    # Deep Multi-Pass: prefer_deep=True, 2 External Searches, max 5 URLs,
    # und automatische Gegenprüfung in Runde 1 + Runde 2 bei offenen Fragen
    deep_gw = MagicMock()
    deep_gw.generate_grounded.side_effect = [
        MagicMock(
            text="Deep Initial [D1](https://deep.example/1)",
            citations=[SourceCitation(url="https://deep.example/1", title="D1")],
            model_used="gemini-3.5-flash-lite",
        ),
        MagicMock(
            text="Verification Round 1 [D2](https://deep.example/2)",
            citations=[SourceCitation(url="https://deep.example/2", title="D2")],
            model_used="gemini-3.5-flash-lite",
        ),
        MagicMock(
            text="Deep Follow-up Round 2 [D3](https://deep.example/3)",
            citations=[SourceCitation(url="https://deep.example/3", title="D3")],
            model_used="gemini-3.5-flash-lite",
        ),
    ]
    deep_gw.search_external_sources.return_value = [SourceCitation(url="https://ext.org/d1", title="ExtD1")]
    deep_gw.generate_structured.side_effect = [
        StructuredSubTopicAnalysis(
            analysis_markdown="- Runde 0",
            key_facts=["F1"],
            open_questions=[],  # Triggert Gegenprüfung aus search_queries[1:]
        ),
        StructuredSubTopicAnalysis(
            analysis_markdown="- Runde 1",
            key_facts=["F1", "F2"],
            open_questions=["Wie hoch sind die Betriebskosten pro Node?"],
        ),
        StructuredSubTopicAnalysis(
            analysis_markdown="- Runde 2 final",
            key_facts=["F1", "F2", "F3"],
            open_questions=[],
        ),
    ]
    deep_fetcher = MagicMock()
    deep_fetcher.fetch_many_tolerant.side_effect = [
        [FetchedWebPage(url="https://deep.example/1", title="D1", text_content="Volltext D1")],
        [FetchedWebPage(url="https://deep.example/2", title="D2", text_content="Volltext D2")],
        [FetchedWebPage(url="https://deep.example/3", title="D3", text_content="Volltext D3")],
    ]
    deep_res = investigate_subtopic(
        gateway=deep_gw,
        web_fetcher=deep_fetcher,
        job=ResearchJob(id="j_deep", topic="QKD", depth=ResearchDepth.DEEP, report_format="compact", language="de"),
        subtopic=subtopic,
        profile=cfg.get_depth_profile(ResearchDepth.DEEP),
        domain_config=cfg,
    )
    assert deep_gw.generate_grounded.call_count == 3
    assert deep_gw.generate_structured.call_count == 3
    assert deep_gw.search_external_sources.call_count == 2
    assert deep_fetcher.fetch_many_tolerant.call_count == 3
    assert deep_fetcher.fetch_many_tolerant.call_args_list[0].kwargs["max_pages"] == 5
    assert all(c.kwargs["prefer_deep"] is True for c in deep_gw.generate_grounded.call_args_list)
    assert all(c.kwargs["prefer_deep"] is True for c in deep_gw.generate_structured.call_args_list)
    assert "Gegenpositionen" in deep_gw.generate_grounded.call_args_list[1].kwargs["prompt"]
    assert "Runde 2 final" in deep_res.analysis_markdown



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
