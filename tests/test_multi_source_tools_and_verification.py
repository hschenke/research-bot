"""Tests für die erweiterten kostenfreien Recherche-Tools, Bandbreiten-Prüfung & Berichts-Briefing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.config.domain_loader import load_default_domain_config
from src.config.settings import AppSettings
from src.domain.domain_config import ModelRoutingConfig
from src.domain.models import (
    ResearchJob,
    ResearchPlan,
    SourceCitation,
    SubTopicFinding,
    SubTopicPlan,
)
from src.research.arxiv_search import search_arxiv
from src.research.browser_tools import BrowserbaseIntegration, fetch_via_jina_reader
from src.research.hackernews_search import search_hackernews
from src.research.multi_source_search import search_multi_source_tools
from src.research.reddit_search import search_reddit_community
from src.research.report_formatter import assemble_report_markdown, build_research_briefing_block
from src.research.result_verifier import (
    analyze_source_diversity,
    build_verification_markdown_section,
)


def test_hackernews_search_parses_hits() -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "hits": [
            {
                "title": "Asyncio Architecture",
                "url": "https://example.com/asyncio",
                "points": 142,
                "num_comments": 48,
                "author": "dev_expert",
                "objectID": "12345",
            }
        ]
    }
    with patch("requests.get", return_value=mock_resp):
        res = search_hackernews("asyncio", max_results=2)
        assert len(res) == 1
        assert "Hacker News: Asyncio Architecture" in res[0].title
        assert "142 Punkte" in res[0].snippet
        assert res[0].url == "https://example.com/asyncio"


def test_reddit_search_via_ddg_parses_html() -> None:
    fake_html = """
    <html><body>
    <a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.reddit.com%2Fr%2FPython%2Fcomments%2F123">Reddit Discussion</a>
    <td class="result-snippet">Python asyncio guide and pitfalls.</td>
    </body></html>
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = fake_html
    with patch("requests.post", return_value=mock_resp):
        res = search_reddit_community("asyncio", max_results=2)
        assert len(res) == 1
        assert "Reddit" in res[0].title
        assert "https://www.reddit.com/r/Python/comments/123" in res[0].url


def test_arxiv_search_parses_atom_xml() -> None:
    xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Quantum Neural Networks</title>
        <id>https://arxiv.org/abs/2301.0001</id>
        <summary>A detailed survey of quantum algorithms.</summary>
        <author><name>Alice Smith</name></author>
      </entry>
    </feed>
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = xml_content
    with patch("requests.get", return_value=mock_resp):
        res = search_arxiv("quantum computing", max_results=2)
        assert len(res) == 1
        assert "arXiv: Quantum Neural Networks" in res[0].title
        assert "Alice Smith" in res[0].snippet


def test_browser_tools_jina_and_browserbase() -> None:
    # Jina Reader
    mock_jina_resp = MagicMock()
    mock_jina_resp.status_code = 200
    mock_jina_resp.text = "# Extrahierter Markdown-Artikel"
    with patch("requests.get", return_value=mock_jina_resp):
        text = fetch_via_jina_reader("https://example.com/blog")
        assert "Extrahierter Markdown-Artikel" in text

    # Browserbase
    empty_settings = AppSettings()
    bb = BrowserbaseIntegration(empty_settings)
    assert bb.is_available is False
    summary = bb.get_status_summary()
    assert summary["active"] is False


def test_result_verifier_and_source_diversity() -> None:
    citations = [
        SourceCitation(url="https://docs.python.org/3/library/asyncio.html", title="Python Docs", snippet="Doc"),
        SourceCitation(url="https://www.reddit.com/r/Python/123", title="Reddit Discussion", snippet="Praxis"),
        SourceCitation(url="https://news.ycombinator.com/item?id=456", title="Hacker News Post", snippet="Tech"),
        SourceCitation(url="https://de.wikipedia.org/wiki/Python", title="Wikipedia: Python", snippet="Enzyklopädie"),
    ]
    summary = analyze_source_diversity(citations)
    assert summary.total_sources == 4
    assert summary.distinct_domains_count == 4
    assert summary.community_sources_count == 2
    assert summary.academic_sources_count == 1

    md_section = build_verification_markdown_section(citations, [], language="de")
    assert "## 🛡️ Quellenprüfung & Faktenabgleich (Cross-Source-Verification)" in md_section
    assert "Praxis-Validierung" in md_section


def test_research_briefing_block_included_in_report() -> None:
    cfg = load_default_domain_config()
    job = ResearchJob(
        id="job_briefing_test",
        topic="Top 7 Badeseen in Bayern",
        focus_instructions="Nur sauberes Wasser & gute Erreichbarkeit mit der Bahn.",
    )
    plan = ResearchPlan(
        normalized_title="Badeseen in Bayern",
        objective_summary="Recherche zu bayerischen Badeseen.",
        subtopics=[
            SubTopicPlan(
                index=1,
                title="Wasserqualität",
                guiding_question="Welche Seen haben beste Bewertungen?",
                search_queries=["Badeseen Bayern Wasserqualität"],
            ),
            SubTopicPlan(
                index=2,
                title="Bahn-Erreichbarkeit",
                guiding_question="Welche Seen sind gut mit dem Zug erreichbar?",
                search_queries=["Badeseen Bayern Bahn"],
            ),
        ],
    )
    findings = [
        SubTopicFinding(
            subtopic_index=1,
            title="Wasserqualität",
            analysis_markdown="Sehr hohe Wasserqualität.",
            key_facts=["Königssee hat Trinkwasserqualität."],
        )
    ]
    citations = [
        SourceCitation(url="https://example.com/seen", title="Seen-Portal", snippet="Infos zu Seen"),
        SourceCitation(url="https://www.reddit.com/r/munich/seen", title="Reddit: Beste Seen", snippet="Erfahrungen"),
    ]

    briefing = build_research_briefing_block(job.topic, job.focus_instructions, plan, language="de")
    assert "Top 7 Badeseen in Bayern" in briefing
    assert "Nur sauberes Wasser & gute Erreichbarkeit mit der Bahn." in briefing
    assert "1. **Wasserqualität** – _Welche Seen haben beste Bewertungen?_" in briefing

    full_md = assemble_report_markdown(
        report_title="Badeseen in Bayern",
        body_markdown="Hauptbericht über Seen.",
        key_takeaways=["Königssee hat Trinkwasserqualität."],
        plan=plan,
        findings=findings,
        citations=citations,
        language="de",
        cfg=cfg,
        omit_wrapper_sections=False,
        job=job,
    )
    assert "# Badeseen in Bayern" in full_md
    assert "> [!NOTE] **Recherche-Auftrag & Suchkriterien:**" in full_md
    assert "Top 7 Badeseen in Bayern" in full_md
    assert "## 🛡️ Quellenprüfung & Faktenabgleich (Cross-Source-Verification)" in full_md


def test_multi_source_tools_aggregator_honors_routing() -> None:
    routing = ModelRoutingConfig(
        enable_duckduckgo_search=False,
        enable_reddit_search=False,
        enable_hackernews_search=True,
        enable_wikipedia_search=False,
        enable_arxiv_search=False,
    )
    fake_hn = [SourceCitation(url="https://example.com/hn", title="HN Link", snippet="Tech")]
    with patch("src.research.multi_source_search.search_hackernews", return_value=fake_hn):
        citations = search_multi_source_tools("python", routing=routing)
        assert len(citations) == 1
        assert citations[0].url == "https://example.com/hn"
