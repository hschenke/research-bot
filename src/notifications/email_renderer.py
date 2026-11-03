"""Sicheres, responsives HTML-E-Mail-Rendering für fertiggestellte Recherche-Berichte."""

from __future__ import annotations

import html
import re
import urllib.parse

import markdown

from src.config.constants import DEPTH_PROFILES
from src.domain.models import ResearchReport
from src.security.auth import generate_readonly_token
from src.security.sanitization import sanitize_display_url, sanitize_markdown_links

_RAW_HTML_TAG_PATTERN = re.compile(r"<(/?[a-zA-Z][^>]*)>")


def format_duration_human(seconds: int) -> str:
    """Formatiert eine Sekundenanzahl lesbar (z. B. `1 Std. 14 Min.` oder `4 Min. 12 Sek.`)."""
    total = max(1, seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours} Std. {minutes} Min."
    if minutes > 0:
        return f"{minutes} Min. {secs} Sek."
    return f"{secs} Sek."


def build_report_app_url(base_url: str, job_id: str, app_password: str = "") -> str:
    """Erstellt einen Direktlink auf den Bericht in der Streamlit-App inkl. Lese-Token."""
    clean_base = sanitize_display_url(base_url.strip().rstrip("/"))
    if clean_base == "#":
        return ""
    params: dict[str, str] = {"tab": "reports", "job_id": job_id}
    if app_password.strip():
        params["auth"] = generate_readonly_token(app_password)
    return f"{clean_base}/?{urllib.parse.urlencode(params)}"


def _render_safe_markdown_html(md_content: str) -> str:
    """Konvertiert bereinigtes Markdown in HTML und maskiert zuvor rohe HTML-Tags (XSS-Schutz)."""
    sanitized_md = sanitize_markdown_links(md_content)
    escaped_raw_html = _RAW_HTML_TAG_PATTERN.sub(
        lambda m: html.escape(m.group(0)),
        sanitized_md,
    )
    rendered = markdown.markdown(escaped_raw_html, extensions=["extra", "tables", "sane_lists"])
    return _apply_inline_email_styles(rendered)


def _apply_inline_email_styles(body_html: str) -> str:
    """Fügt kompatible Inline-Styles für gängige Mail-Clients (Gmail, Outlook, Apple Mail) ein."""
    styled = body_html.replace(
        "<h1>",
        '<h1 style="color:#0f172a;font-size:22px;margin:0 0 16px 0;line-height:1.3;">',
    )
    styled = styled.replace(
        "<h2>",
        '<h2 style="color:#0f172a;font-size:18px;margin:26px 0 10px 0;padding-bottom:6px;border-bottom:2px solid #e2e8f0;">',
    )
    styled = styled.replace(
        "<h3>",
        '<h3 style="color:#1e293b;font-size:15px;margin:18px 0 8px 0;">',
    )
    styled = styled.replace(
        "<p>",
        '<p style="color:#334155;font-size:14.5px;line-height:1.65;margin:0 0 12px 0;">',
    )
    styled = styled.replace(
        "<ul>",
        '<ul style="color:#334155;font-size:14.5px;line-height:1.65;padding-left:20px;margin:10px 0 16px 0;">',
    )
    styled = styled.replace(
        "<ol>",
        '<ol style="color:#334155;font-size:14px;line-height:1.6;padding-left:20px;margin:10px 0 16px 0;">',
    )
    styled = styled.replace(
        "<li>",
        '<li style="margin-bottom:8px;">',
    )
    styled = styled.replace(
        "<a href=",
        '<a target="_blank" rel="noopener noreferrer" style="color:#2563eb;font-weight:600;text-decoration:none;" href=',
    )
    return styled


def render_report_email_html(
    report: ResearchReport,
    app_report_url: str = "",
) -> str:
    """Erstellt ein vollständiges, responsives HTML-Dokument für den Ergebnis-Versand."""
    safe_title = html.escape(report.title)
    profile_label = html.escape(DEPTH_PROFILES[report.depth].label)
    duration_str = html.escape(format_duration_human(report.duration_seconds))
    sources_count = len(report.citations)
    body_html = _render_safe_markdown_html(report.content_markdown)

    cta_block = ""
    if app_report_url:
        cta_block = (
            f'<div style="margin:0 0 24px 0;">'
            f'<a href="{app_report_url}" target="_blank" rel="noopener noreferrer" '
            f'style="display:inline-block;background-color:#2563eb;color:#ffffff;'
            f'font-weight:600;font-size:14px;padding:11px 20px;border-radius:8px;'
            f'text-decoration:none;">🔗 Bericht in der Web-App öffnen &amp; herunterladen</a>'
            f"</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="{html.escape(report.language)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title}</title>
</head>
<body style="margin:0;padding:24px 12px;background-color:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:700px;margin:0 auto;background-color:#ffffff;border-radius:14px;overflow:hidden;border:1px solid #e2e8f0;">
    <div style="background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 100%);padding:28px 32px;color:#ffffff;">
      <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.08em;color:#93c5fd;margin-bottom:6px;">
        🔬 Research-Bot Ergebnisbericht
      </div>
      <h1 style="margin:0 0 10px 0;font-size:22px;line-height:1.3;color:#ffffff;">{safe_title}</h1>
      <div style="font-size:13px;color:#cbd5e1;">
        {profile_label} &middot; Laufzeit: {duration_str} &middot; {sources_count} Web-Quellen ausgewertet
      </div>
    </div>
    <div style="padding:30px 32px;">
      {cta_block}
      {body_html}
    </div>
  </div>
</body>
</html>"""
