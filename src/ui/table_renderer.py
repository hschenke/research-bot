"""Interaktive HTML-Tabellen-Aufbereitung für Recherche-Berichte in Streamlit."""

from __future__ import annotations

import html
import urllib.parse
from typing import Any

import pandas as pd
import streamlit as st

from src.research.table_extractor import auto_generate_top_x_table
from src.ui.table_glossary import GLOSSARY_HELP_TEXT, decorate_cell_with_tooltips
from src.ui.table_parser import (
    clean_cell_content as _clean_cell_content,
)
from src.ui.table_parser import (
    is_markdown_separator_row,
    is_markdown_table_row,
    parse_markdown_table_raw,
    split_markdown_by_tables,
)

__all__ = [
    "_clean_cell_content",
    "is_markdown_separator_row",
    "is_markdown_table_row",
    "parse_markdown_table",
    "render_markdown_with_interactive_tables",
    "split_markdown_by_tables",
]

_EXCLUDED_COLUMNS = ("quelle", "link", "url", "ref", "referenz", "webseite")
_SECONDARY_COLUMNS = ("wind", "details", "beschreibung", "kommentar")


def _sort_dataframe(df: pd.DataFrame, sort_col: str, sort_dir: str) -> pd.DataFrame:
    """Sortiert das DataFrame nach der gewählten Spalte (numerisch oder textuell)."""
    if sort_col not in df.columns:
        return df
    ascending = sort_dir.lower() == "asc"
    series = df[sort_col]
    if pd.api.types.is_numeric_dtype(series):
        return df.sort_values(by=sort_col, ascending=ascending)
    try:
        numeric_s = pd.to_numeric(
            series.astype(str).str.extract(r"([\d\.,]+)")[0].str.replace(",", "."),
            errors="coerce",
        )
        if numeric_s.notna().sum() >= len(df) * 0.7:
            df_copy = df.copy()
            df_copy["_sort_key"] = numeric_s
            return df_copy.sort_values(by="_sort_key", ascending=ascending).drop(columns=["_sort_key"])
    except (ValueError, TypeError, KeyError):
        return df.sort_values(by=sort_col, ascending=ascending)
    return df.sort_values(by=sort_col, ascending=ascending)


def _format_cell_value(val: Any, col: str, is_url: bool) -> str:
    """Formatiert einen Zellwert als HTML mit Zahlenformatierung oder Begriffserklärungs-Badge."""
    val_str = "" if pd.isna(val) else str(val).strip()
    col_lower = col.lower()
    if is_url and (val_str.startswith("http://") or val_str.startswith("https://")):
        return f'<a href="{html.escape(val_str)}" target="_blank" rel="noopener noreferrer">🔗 Link</a>'
    if isinstance(val, (int, float)):
        if "(m)" in col_lower or "höhe" in col_lower or "meter" in col_lower:
            return f"<b>{float(val):.2f} m</b>"
        if "€" in col or "preis" in col_lower:
            return f"{float(val):.2f} €"
        if "%" in col:
            return f"{float(val):.1f} %"
        if isinstance(val, int) and col_lower == "rang":
            return f"<b>{val}</b>"
        return f"{val}"
    safe_text = html.escape(val_str)
    return decorate_cell_with_tooltips(safe_text)


def _build_html_headers(
    df: pd.DataFrame, sort_col: str | None, sort_dir: str, job_id: str | None
) -> list[str]:
    """Erzeugt anklickbare Sortier-Header für jede Tabellenspalte."""
    th_elements: list[str] = []
    for col in df.columns:
        is_cur = sort_col == col
        next_d = "desc" if (is_cur and sort_dir == "asc") else "asc"
        arrow = ("▲" if sort_dir == "asc" else "▼") if is_cur else "↕"
        qp = {**st.query_params, "tab": "reports", "tbl_sort": col, "tbl_dir": next_d}
        if job_id:
            qp["job_id"] = job_id
        th_elements.append(
            f'<th><a href="?{urllib.parse.urlencode(qp)}" target="_self" title="Nach {html.escape(col)} sortieren">'
            f'{html.escape(col)} <span class="surf-sort-arrow">{arrow}</span></a></th>'
        )
    return th_elements


def _build_html_table(
    df: pd.DataFrame,
    url_columns: set[str],
    sort_col: str | None,
    sort_dir: str,
    job_id: str | None,
) -> str:
    """Baut eine vollständige HTML-Tabelle mit interaktiven Sortier-Headern und Begriff-Tooltips."""
    headers = "".join(_build_html_headers(df, sort_col, sort_dir, job_id))
    tr_elements: list[str] = []
    for _, row in df.iterrows():
        tds = [f"<td>{_format_cell_value(row[c], c, c in url_columns)}</td>" for c in df.columns]
        tr_elements.append(f"<tr>{''.join(tds)}</tr>")
    tbody = "".join(tr_elements)
    return (
        f'<div class="surf-tbl-wrap">\n'
        f'  <table class="surf-tbl">\n'
        f'    <thead><tr>{headers}</tr></thead>\n'
        f'    <tbody>{tbody}</tbody>\n'
        f'  </table>\n'
        f'</div>'
    )


def _build_column_configs(df: pd.DataFrame, url_columns: set[str]) -> dict[str, Any]:
    """Legacy-Fallback für DataFrame-Spaltenkonfigurationen (u. a. für Tests)."""
    configs: dict[str, Any] = {}
    for col in df.columns:
        if col in url_columns:
            configs[col] = st.column_config.LinkColumn(col, width="small", display_text="🔗 Link")
        elif pd.api.types.is_integer_dtype(df[col]):
            configs[col] = st.column_config.NumberColumn(col, width="small", format="%d")
        elif pd.api.types.is_float_dtype(df[col]):
            configs[col] = st.column_config.NumberColumn(col, width="small", format="%.2f")
        elif any(k in col.lower() for k in ("typ", "art", "wellentyp")):
            configs[col] = st.column_config.TextColumn(col, width="medium", help=GLOSSARY_HELP_TEXT)
        else:
            configs[col] = st.column_config.TextColumn(col, width="small")
    return configs


def parse_markdown_table(lines: list[str]) -> tuple[pd.DataFrame, dict[str, Any]] | None:
    """Wandelt Markdown-Tabellenzeilen in einen typisierten DataFrame mit Spaltenkonfigurationen um."""
    parsed = parse_markdown_table_raw(lines)
    if parsed is None:
        return None
    df, url_columns = parsed
    return df, _build_column_configs(df, url_columns)


def _filter_compact_columns(df: pd.DataFrame, table_idx: int = 1) -> pd.DataFrame:
    """Entfernt Quellenspalten und sekundäre Spalten für eine kompakte, scrollfreie Ansicht."""
    _ = table_idx
    clean_cols = [c for c in df.columns if c.lower() not in _EXCLUDED_COLUMNS]
    if clean_cols:
        df = df[clean_cols]
    core = [c for c in df.columns if c.lower() not in _SECONDARY_COLUMNS]
    return df[core] if len(df.columns) > 6 and len(core) >= 4 else df


_apply_compact_column_filter = _filter_compact_columns


def render_markdown_with_interactive_tables(
    content_markdown: str, job_id: str | None = None
) -> None:
    """Rendert Markdown mit responsiver HTML-Tabelle, Kopf-Sortierung und Begriff-Tooltips."""
    blocks = split_markdown_by_tables(content_markdown)
    if not any(b_type == "table" for b_type, _ in blocks):
        fallback = auto_generate_top_x_table(content_markdown)
        if not fallback:
            st.markdown(content_markdown)
            return
        hdr = "\n\n## 🏆 Top-Übersicht & Vergleich\n\n" + fallback + "\n\n"
        pos = content_markdown.find("## ")
        content_markdown = (
            content_markdown[:pos] + hdr + content_markdown[pos:] if pos != -1 else content_markdown + hdr
        )
        blocks = split_markdown_by_tables(content_markdown)

    for b_type, content in blocks:
        if b_type == "text" and str(content).strip():
            st.markdown(content)
        elif b_type == "table":
            table_lines = content
            result = parse_markdown_table_raw(table_lines)
            if result is None:
                st.markdown("\n".join(table_lines))
                continue
            df, url_columns = result
            df = _filter_compact_columns(df)
            s_col = st.query_params.get("tbl_sort")
            s_dir = str(st.query_params.get("tbl_dir", "asc") or "asc").lower()
            if s_col and s_col in df.columns:
                df = _sort_dataframe(df, s_col, s_dir)
            st.markdown(_build_html_table(df, url_columns, s_col, s_dir, job_id), unsafe_allow_html=True)
            st.caption("💡 *Spaltenköpfe zum Sortieren anklicken · Eingekreistes (?) bei Fachbegriffen.*")
            with st.expander("📋 Reine Markdown-Tabelle anzeigen", expanded=False):
                st.markdown("\n".join(table_lines))
