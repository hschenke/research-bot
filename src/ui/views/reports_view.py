"""Tab 3: Reports-Archiv mit Suche, Export und E-Mail-Versand."""

from __future__ import annotations

import html

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
from src.storage.base_repository import ResearchRepository
from src.ui.date_formatter import format_datetime_german
from src.ui.table_renderer import render_markdown_with_interactive_tables


def render_reports_view(
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
    domain_config: DomainConfig | None = None,
) -> None:
    """Rendert die Übersicht und Detailansicht aller fertiggestellten Reports."""
    cfg = domain_config or repository.get_domain_config() or load_default_domain_config()

    col_sel, col_search = st.columns([2.2, 1.1], vertical_alignment="bottom")
    with col_search:
        search_query = st.text_input(
            "🔍 Filter",
            placeholder="🔍 Stichwort oder Thema filtern...",
            label_visibility="collapsed",
        )

    reports = repository.list_reports(search_query=search_query, limit=50)
    if not reports:
        st.info("Keine Reports gefunden.")
        return

    preselected_id = str(st.query_params.get("job_id", "") or "").strip()
    report_ids = [r.job_id for r in reports]
    default_index = report_ids.index(preselected_id) if preselected_id in report_ids else 0

    with col_sel:
        selected_report = st.selectbox(
            f"📚 Reports ({len(reports)})",
            options=reports,
            index=default_index,
            format_func=lambda r: (
                f"{r.title}  ·  {format_datetime_german(r.created_at)} · {cfg.get_depth_profile(r.depth).label}"
            ),
        )

    if selected_report is not None:
        if st.query_params.get("job_id") != selected_report.job_id:
            st.query_params["job_id"] = selected_report.job_id
        _render_single_report(
            report=selected_report,
            settings=settings,
            repository=repository,
            role=role,
            cfg=cfg,
        )


def _render_metadata_badge_bar(report: ResearchReport, cfg: DomainConfig) -> None:
    """Rendert eine kompakte Pill-Metadaten-Leiste."""
    depth_label = html.escape(cfg.get_depth_profile(report.depth).label)
    duration = html.escape(format_duration_human(report.duration_seconds))
    sources_count = len(report.citations)
    created_at = html.escape(format_datetime_german(report.created_at))

    st.markdown(
        f'<div class="rb-pill-bar">'
        f'<span class="rb-pill">{depth_label}</span>'
        f'<span class="rb-pill">⏱️ {duration}</span>'
        f'<span class="rb-pill">🔗 {sources_count} Sources</span>'
        f'<span class="rb-pill">🗓️ {created_at}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_single_report(
    report: ResearchReport,
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
    cfg: DomainConfig,
) -> None:
    app_url = build_report_app_url(
        base_url=settings.streamlit_app_url,
        job_id=report.job_id,
        app_password=settings.app_password,
    )
    html_export = render_report_email_html(
        report=report, app_report_url=app_url, domain_config=cfg
    )

    _render_metadata_badge_bar(report, cfg)

    can_send_mail = role == AuthRole.ADMIN and settings.has_email_configured
    is_admin = role == AuthRole.ADMIN
    btn_count = 2 + (1 if can_send_mail else 0) + (1 if is_admin else 0)
    btn_cols = st.columns(btn_count, gap="small")

    with btn_cols[0]:
        st.download_button(
            "⬇️ .md",
            data=report.content_markdown.encode("utf-8"),
            file_name=f"research_{report.job_id}.md",
            mime="text/markdown",
            help="Markdown herunterladen",
        )
    with btn_cols[1]:
        st.download_button(
            "⬇️ .html",
            data=html_export.encode("utf-8"),
            file_name=f"research_{report.job_id}.html",
            mime="text/html",
            help="HTML-Report herunterladen",
        )
    col_idx = 2
    if can_send_mail:
        with btn_cols[col_idx]:
            if st.button("📧 E-Mail", key=f"send_mail_{report.job_id}"):
                _resend_report_email(report=report, settings=settings, repository=repository)
        col_idx += 1
    if is_admin:
        with btn_cols[col_idx]:
            if st.button("🗑️", key=f"del_rep_{report.job_id}", help="Report löschen"):
                repository.delete_job_and_report(report.job_id)
                st.rerun()

    with st.container(border=True):
        render_markdown_with_interactive_tables(report.content_markdown, job_id=report.job_id)



def _resend_report_email(
    report: ResearchReport,
    settings: AppSettings,
    repository: ResearchRepository,
) -> None:
    job = repository.get_job(report.job_id)
    if job is None:
        st.error("Job nicht gefunden.")
        return
    notifier = EmailNotifier(settings)
    if notifier.notify_report_ready(job=job, report=report):
        repository.mark_job_email_sent(job.id)
        st.success("✅ E-Mail gesendet.")
    else:
        st.error("E-Mail-Versand fehlgeschlagen.")

