"""Tests für Top-X Erkennung, interaktive Tabellen-Aufbereitung & Browserbase-Fallback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
from src.config.domain_loader import load_default_domain_config
from src.config.settings import AppSettings
from src.domain.enums import ResearchDepth
from src.domain.models import ResearchJob, ResearchPlan, SubTopicPlan
from src.research.browser_tools import (
    BrowserbaseIntegration,
    fetch_via_browserbase,
)
from src.research.report_formatter import build_research_briefing_block
from src.research.synthesizer import _build_synthesis_prompts
from src.research.table_extractor import auto_generate_top_x_table
from src.research.web_fetcher import FetchedWebPage, SafeWebFetcher
from src.ui.table_renderer import (
    _clean_cell_content,
    is_markdown_separator_row,
    is_markdown_table_row,
    parse_markdown_table,
    split_markdown_by_tables,
)


def test_is_top_x_query_detection() -> None:
    cfg = load_default_domain_config()
    assert cfg.is_top_x_query("Top 10 KI Tools 2026")
    assert cfg.is_top_x_query("top 5 Python Frameworks")
    assert cfg.is_top_x_query("Top-10 Cloud Provider")
    assert cfg.is_top_x_query("die besten 10 Grafikkarten")
    assert cfg.is_top_x_query("best 3 LLMs für Entwickler")
    assert cfg.is_top_x_query("Ranking der schnellsten Programmiersprachen")
    assert cfg.is_top_x_query("Vergleichstabelle SQL vs NoSQL")
    assert cfg.is_top_x_query("Allgemeines Thema", focus_instructions="Bitte Top 5 auflisten")

    assert not cfg.is_top_x_query("Einführung in Quantencomputing")
    assert not cfg.is_top_x_query("Wie funktioniert Photosynthese?")


def test_synthesizer_prompts_inject_top_x_table_mandate() -> None:
    cfg = load_default_domain_config()
    job = ResearchJob(
        id="job-top-x-1",
        topic="Top 10 Vektor-Datenbanken",
        depth=ResearchDepth.STANDARD,
        report_format="compact",
    )
    plan = ResearchPlan(
        normalized_title="Top 10 Vektordatenbanken",
        objective_summary="Vergleich der besten Vektor-DBs.",
        subtopics=[
            SubTopicPlan(
                index=1,
                title="Vektor-Datenbanken Vergleich",
                guiding_question="Welche Vektor-DBs sind führend?",
                search_queries=["Vektor DBs Vergleich"],
            )
        ],
    )
    profile = cfg.get_depth_profile(ResearchDepth.STANDARD)
    _sys, user, _omit = _build_synthesis_prompts(
        job=job,
        plan=plan,
        findings=[],
        baseline_markdown="",
        active_profile=profile,
        cfg=cfg,
    )

    assert "TOP-X / RANKING-VORGABE" in user
    assert "Markdown-Tabelle" in user


def test_markdown_table_detection_helpers() -> None:
    assert is_markdown_table_row("| Rang | Tool | Score |")
    assert not is_markdown_table_row("Hier ist nur Text mit | Pipe.")
    assert not is_markdown_table_row("# Überschrift")

    assert is_markdown_separator_row("|---|---|---|")
    assert is_markdown_separator_row("| :--- | :---: | ---: |")
    assert not is_markdown_separator_row("| Rang | Tool | Score |")


def test_clean_cell_content() -> None:
    text, link = _clean_cell_content("**FastAPI**")
    assert text == "FastAPI"
    assert link is None

    url, label = _clean_cell_content("[Offizielle Docs](https://fastapi.tiangolo.com)")
    assert url == "https://fastapi.tiangolo.com"
    assert label == "Offizielle Docs"


def test_split_markdown_by_tables() -> None:
    doc = (
        "# Einleitung\n\n"
        "Hier steht ein Einführungstext.\n\n"
        "| Rang | Tool | Score |\n"
        "|---|---|---|\n"
        "| 1 | Tool A | 9.8 |\n"
        "| 2 | Tool B | 9.1 |\n\n"
        "## Fazit\n"
        "Hier steht das Fazit."
    )
    blocks = split_markdown_by_tables(doc)
    assert len(blocks) == 3
    assert blocks[0][0] == "text"
    assert "Einführungstext" in blocks[0][1]
    assert blocks[1][0] == "table"
    assert len(blocks[1][1]) == 4
    assert blocks[2][0] == "text"
    assert "Fazit" in blocks[2][1]


def test_parse_markdown_table_to_dataframe() -> None:
    table_lines = [
        "| Rang | Tool | Score | Link |",
        "|---|---|---|---|",
        "| 1 | **Tool A** | 9.8 | [Site](https://example.com/a) |",
        "| 10 | Tool B | 8.2 | https://example.com/b |",
        "| 2 | Tool C | 9.1 | |",
    ]
    res = parse_markdown_table(table_lines)
    assert res is not None
    df, configs = res

    assert list(df.columns) == ["Rang", "Tool", "Score", "Link"]
    assert len(df) == 3
    # Prüfung der korrekten numerischen Typisierung für numerisches Sortieren
    assert pd.api.types.is_integer_dtype(df["Rang"])
    assert pd.api.types.is_float_dtype(df["Score"])
    # Prüfung der Link-Spalte
    assert "Link" in configs
    # Prüfe bereinigten Text
    assert df["Tool"].iloc[0] == "Tool A"


def test_browserbase_fetch_and_fallback() -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"content": "Extrahierter Browserbase Volltext"}

    with patch("requests.post", return_value=mock_resp):
        text = fetch_via_browserbase("https://example.com/spa", api_key="bb_live_key")
        assert text == "Extrahierter Browserbase Volltext"

    # Integrationstest
    settings = AppSettings(browserbase_api_key="bb_live_key", browserbase_project_id="proj_1")
    bb = BrowserbaseIntegration(settings)
    assert bb.is_available
    with patch("requests.post", return_value=mock_resp):
        res_text = bb.fetch_page_content("https://example.com/spa")
        assert res_text == "Extrahierter Browserbase Volltext"


def test_safe_web_fetcher_fallback_on_http_error() -> None:
    settings = AppSettings(browserbase_api_key="bb_key")
    fetcher = SafeWebFetcher(
        dns_resolver=lambda _h, _p: ["93.184.216.34"],
        settings=settings,
        enable_browserbase=True,
        enable_jina_reader=True,
    )

    # 1. Simuliere 403 Forbidden beim direkten Abruf, gefolgt von erfolgreichem Browserbase-Fallback
    with (
        patch.object(fetcher._session, "get") as mock_get,
        patch("src.research.web_fetcher.fetch_fallback_web_content") as mock_fallback,
    ):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_get.return_value.__enter__.return_value = mock_resp
        mock_fallback.return_value = ("Fallback Inhalt von gesperrter Seite", "Browserbase Cloud")

        page = fetcher.fetch_page("https://example.com/protected")
        assert isinstance(page, FetchedWebPage)
        assert page.text_content == "Fallback Inhalt von gesperrter Seite"
        assert "[Browserbase Cloud]" in page.title


def test_auto_generate_top_x_table_from_bullets() -> None:
    bullet_markdown = (
        "## 1. Top-Auswahl der Strände\n\n"
        "* **Playa de San Juan (Alicante):** Beliebter Sandstrand ([Surfline](https://surfline.com)).\n"
        "* **Playa de Guardamar:** Exponierte Wellen ([Windfinder](https://windfinder.com)).\n\n"
        "## 2. Wetterbedingungen\n"
        "- Starker Wind im Winter."
    )
    table = auto_generate_top_x_table(bullet_markdown)
    assert table is not None
    assert "| Rang | Name / Eintrag |" in table
    assert "| 1 | Playa de San Juan (Alicante) |" in table
    assert "| 2 | Playa de Guardamar |" in table
    assert "[Surfline](https://surfline.com)" in table


def test_briefing_block_includes_active_tools() -> None:
    cfg = load_default_domain_config()
    routing = cfg.model_routing
    routing_with_bb = routing.model_copy(update={"enable_browserbase": True})
    briefing = build_research_briefing_block(
        topic="Top 10 Tools",
        focus_instructions="",
        plan=None,
        language="de",
        routing=routing_with_bb,
    )
    assert "Eingesetzte Recherche-Tools:" in briefing
    assert "🌐 Browserbase (Cloud-Headless aktiv)" in briefing
