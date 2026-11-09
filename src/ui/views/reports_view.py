"""Tab 3: Ergebnis-Archiv mit Volltextsuche, Quellen-Explorer, Export und E-Mail-Nachversand."""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

from src.config.domain_loader import load_default_domain_config
from src.config.settings import AppSettings
from src.domain.domain_config import DomainConfig
from src.domain.enums import AuthRole
from src.domain.models import ResearchReport
from src.notifications.email_renderer import (
    build_report_app_url,
    format_duration_human,
    render_report_email_html,
)
from src.notifications.notifier import EmailNotifier
from src.research.llm_helpers import normalize_preferred_model
from src.storage.base_repository import ResearchRepository
from src.ui.date_formatter import format_datetime_german
from src.ui.table_renderer import render_markdown_with_interactive_tables


def render_reports_view(
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
    domain_config: DomainConfig | None = None,
) -> None:
    """Rendert die Übersicht und Detailansicht aller fertiggestellten Recherche-Berichte."""
    cfg = domain_config or repository.get_domain_config() or load_default_domain_config()
    search_query = st.text_input(
        "🔍 Berichte durchsuchen",
        placeholder="Thema, Stichwort oder Inhalt filtern...",
    )
    reports = repository.list_reports(search_query=search_query, limit=50)

    if not reports:
        st.info("Noch keine fertiggestellten Recherche-Berichte gefunden.")
        return

    preselected_id = str(st.query_params.get("job_id", "") or "").strip()
    report_ids = [r.job_id for r in reports]
    default_index = report_ids.index(preselected_id) if preselected_id in report_ids else 0

    selected_report = st.selectbox(
        f"📚 Verfügbare Berichte ({len(reports)})",
        options=reports,
        index=default_index,
        format_func=lambda r: (
            f"{r.title}  ·  [{format_datetime_german(r.created_at)} | {cfg.get_depth_profile(r.depth).label}]"
        ),
    )

    if selected_report is not None:
        _render_single_report(
            report=selected_report,
            settings=settings,
            repository=repository,
            role=role,
            cfg=cfg,
        )


def _render_kpi_card(column: Any, label: str, value: str) -> None:
    """Rendert eine robuste KPI-Karte via Border-Container ohne anfällige dynamische Vite-Chunks."""
    safe_val = html.escape(value)
    safe_lbl = html.escape(label)
    with column.container(border=True):
        st.caption(safe_lbl)
        st.markdown(
            f"<div style='font-size: 1.25rem; font-weight: 600; line-height: 1.2;'>{safe_val}</div>",
            unsafe_allow_html=True,
        )


def _render_single_report(
    report: ResearchReport,
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
    cfg: DomainConfig,
) -> None:
    st.divider()
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    _render_kpi_card(col_m1, "Tiefe", cfg.get_depth_profile(report.depth).label)
    _render_kpi_card(col_m2, "Laufzeit", format_duration_human(report.duration_seconds))
    _render_kpi_card(col_m3, "Verifizierte Quellen", str(len(report.citations)))
    _render_kpi_card(col_m4, "Erstellt", format_datetime_german(report.created_at))
    safe_model = normalize_preferred_model(
        report.model_used or cfg.default_model,
        allowed_models=cfg.available_models,
    )
    st.caption(f"🤖 Erstellt mit Modell: `{safe_model}`")

    app_url = build_report_app_url(
        base_url=settings.streamlit_app_url,
        job_id=report.job_id,
        app_password=settings.app_password,
    )
    html_export = render_report_email_html(
        report=report, app_report_url=app_url, domain_config=cfg
    )

    dl_col1, dl_col2, mail_col, del_col = st.columns([1.2, 1.2, 1.6, 1])
    with dl_col1:
        st.download_button(
            "⬇️ Markdown (.md)",
            data=report.content_markdown.encode("utf-8"),
            file_name=f"research_{report.job_id}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    with dl_col2:
        st.download_button(
            "⬇️ HTML-Dossier (.html)",
            data=html_export.encode("utf-8"),
            file_name=f"research_{report.job_id}.html",
            mime="text/html",
            use_container_width=True,
        )
    with mail_col:
        can_send_mail = role == AuthRole.ADMIN and settings.has_email_configured
        if can_send_mail and st.button(
            "📧 Per E-Mail senden",
            key=f"send_mail_{report.job_id}",
            use_container_width=True,
        ):
            _resend_report_email(report=report, settings=settings, repository=repository)
    with del_col:
        if role == AuthRole.ADMIN and st.button(
            "🗑️ Löschen",
            key=f"del_rep_{report.job_id}",
            use_container_width=True,
        ):
            repository.delete_job_and_report(report.job_id)
            st.rerun()

    job_record = repository.get_job(report.job_id)
    checkpoint = repository.load_checkpoint(report.job_id)
    with st.expander("📋 Recherche-Auftrag, Anforderungen & Teilthemen einsehen", expanded=False):
        st.markdown(f"**Recherche-Thema:** {report.topic}")
        if job_record and job_record.focus_instructions.strip():
            st.markdown(f"**Eingegebene Anforderungen / Schwerpunkte:**\n> {job_record.focus_instructions.strip()}")
        else:
            st.caption("Keine zusätzlichen Einschränkungen oder Vorgaben eingegeben.")
        if checkpoint and checkpoint.plan and checkpoint.plan.subtopics:
            st.markdown("**Untersuchte Teilthemen & Leitfragen:**")
            for sub in checkpoint.plan.subtopics:
                st.markdown(f"{sub.index}. **{sub.title}** – _{sub.guiding_question}_")

    if report.citations:
        with st.expander(f"🔗 Alle {len(report.citations)} ausgewerteten Web-Quellen anzeigen"):
            for idx, cite in enumerate(report.citations, start=1):
                label = cite.title or cite.url
                st.markdown(f"{idx}. [{label}]({cite.url})")

    with st.container(border=True):
        render_markdown_with_interactive_tables(report.content_markdown)


def _resend_report_email(
    report: ResearchReport,
    settings: AppSettings,
    repository: ResearchRepository,
) -> None:
    job = repository.get_job(report.job_id)
    if job is None:
        st.error("Zugehöriger Auftragsdatensatz nicht gefunden.")
        return
    notifier = EmailNotifier(settings)
    if notifier.notify_report_ready(job=job, report=report):
        repository.mark_job_email_sent(job.id)
        st.success("✅ Bericht wurde erfolgreich per E-Mail versendet.")
    else:
        st.error("E-Mail-Versand fehlgeschlagen. Bitte E-Mail-Einstellungen und Empfänger prüfen.")
