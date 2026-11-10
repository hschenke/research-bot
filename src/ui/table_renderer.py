"""Interaktive Tabellen-Aufbereitung für Recherche-Berichte in Streamlit."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.research.table_extractor import auto_generate_top_x_table
from src.ui.table_glossary import GLOSSARY_HELP_TEXT
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


def _build_column_configs(df: pd.DataFrame, url_columns: set[str]) -> dict[str, Any]:
    """Konfiguriert Spaltenbreiten, Einheiten und das eingekreiste Fragezeichen (?) mit Tooltip."""
    column_configs: dict[str, Any] = {}
    for col in df.columns:
        if col in url_columns:
            column_configs[col] = st.column_config.LinkColumn(
                col,
                width="small",
                display_text="🔗 Link",
                help="Quellenlink zum Originalartikel",
            )
            continue

        col_lower = col.lower()
        if pd.api.types.is_integer_dtype(df[col]):
            width = "small" if len(col) <= 6 else "medium"
            column_configs[col] = st.column_config.NumberColumn(col, width=width, format="%d")
        elif pd.api.types.is_float_dtype(df[col]):
            if "(m)" in col_lower or "meter" in col_lower or "höhe" in col_lower:
                column_configs[col] = st.column_config.NumberColumn(
                    col, width="small", format="%.1f m", help="Maximale Wellenhöhe in Metern"
                )
            elif "€" in col or "eur" in col_lower or "preis" in col_lower:
                column_configs[col] = st.column_config.NumberColumn(col, width="small", format="%.2f €")
            elif "%" in col:
                column_configs[col] = st.column_config.NumberColumn(col, width="small", format="%.1f %%")
            else:
                column_configs[col] = st.column_config.NumberColumn(col, width="small", format="%.2f")
        else:
            if any(k in col_lower for k in ("typ", "art", "wellentyp")):
                # Zeigt das kleine eingekreiste (?) an, das bei Mouse-Hover und Mobile-Touch den Tooltip öffnet
                column_configs[col] = st.column_config.TextColumn(
                    col,
                    width="medium",
                    help=GLOSSARY_HELP_TEXT,
                )
            elif any(k in col_lower for k in ("ort", "saison", "level")):
                column_configs[col] = st.column_config.TextColumn(col, width="small")
            else:
                column_configs[col] = st.column_config.TextColumn(col, width="medium")

    return column_configs


def parse_markdown_table(lines: list[str]) -> tuple[pd.DataFrame, dict[str, Any]] | None:
    """Wandelt Markdown-Tabellenzeilen in einen typisierten DataFrame mit Spaltenkonfigurationen um."""
    parsed = parse_markdown_table_raw(lines)
    if parsed is None:
        return None
    df, url_columns = parsed
    column_configs = _build_column_configs(df, url_columns)
    return df, column_configs


def _filter_compact_columns(df: pd.DataFrame, table_idx: int = 1) -> pd.DataFrame:
    """Entfernt Quellenspalten und sekundäre Spalten für eine kompakte, scrollfreie Ansicht."""
    _ = table_idx
    # 1. Quelle / Link / URL strikt aus der Tabelle entfernen
    clean_cols = [c for c in df.columns if c.lower() not in _EXCLUDED_COLUMNS]
    if clean_cols:
        df = df[clean_cols]

    # 2. Sekundäre Spalten (z. B. Wind) weglassen, wenn Tabelle sonst horizontal scrollen würde
    core_cols = [c for c in df.columns if c.lower() not in _SECONDARY_COLUMNS]
    if len(df.columns) > 6 and len(core_cols) >= 4:
        return df[core_cols]

    return df


_apply_compact_column_filter = _filter_compact_columns


def render_markdown_with_interactive_tables(content_markdown: str) -> None:
    """Rendert Markdown mit interaktiven, sortierbaren Streamlit-Dataframes für alle Tabellen."""
    blocks = split_markdown_by_tables(content_markdown)
    has_any_table = any(b_type == "table" for b_type, _ in blocks)

    if not has_any_table:
        fallback_table = auto_generate_top_x_table(content_markdown)
        if fallback_table:
            header = "\n\n## 🏆 Top-Übersicht & Vergleich\n\n" + fallback_table + "\n\n"
            first_h2 = content_markdown.find("## ")
            if first_h2 != -1:
                content_markdown = content_markdown[:first_h2] + header + content_markdown[first_h2:]
            else:
                content_markdown = content_markdown + header
            blocks = split_markdown_by_tables(content_markdown)
        else:
            st.markdown(content_markdown)
            return

    for block_type, content in blocks:
        if block_type == "text":
            if str(content).strip():
                st.markdown(content)
        elif block_type == "table":
            table_lines = content
            result = parse_markdown_table_raw(table_lines)
            if result is None:
                st.markdown("\n".join(table_lines))
                continue

            df, url_columns = result
            df = _filter_compact_columns(df)
            col_configs = _build_column_configs(df, url_columns)

            st.dataframe(
                df,
                width="stretch",
                hide_index=True,
                column_config=col_configs,
            )
            st.caption(
                "💡 *Spaltenköpfe zum Sortieren anklicken · Eingekreistes (?) bei Wellentyp für Begriffserklärungen (Hover/Touch).*"
            )
            raw_table_md = "\n".join(table_lines)
            with st.expander("📋 Reine Markdown-Tabelle anzeigen", expanded=False):
                st.markdown(raw_table_md)
