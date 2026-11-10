"""Interaktive Tabellen-Aufbereitung & Sortierung für Recherche-Berichte in Streamlit."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.research.table_extractor import auto_generate_top_x_table
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

_NON_SORTABLE_COLUMN_KEYWORDS = ("quelle", "link", "url", "ref", "referenz", "webseite")


def _build_column_configs(df: pd.DataFrame, url_columns: set[str]) -> dict[str, Any]:
    """Konfiguriert Streamlit-Spaltentypen (Links, Ganzzahlen, Dezimalwerte) für optimales Anzeigen."""
    column_configs: dict[str, Any] = {}
    for col in df.columns:
        if col in url_columns:
            column_configs[col] = st.column_config.LinkColumn(
                col,
                display_text="🔗 Link",
                help="Quellenlink zum Originalartikel",
            )
            continue

        if pd.api.types.is_integer_dtype(df[col]):
            column_configs[col] = st.column_config.NumberColumn(col, format="%d")
        elif pd.api.types.is_float_dtype(df[col]):
            col_lower = col.lower()
            if "(m)" in col_lower or "meter" in col_lower or "höhe" in col_lower:
                column_configs[col] = st.column_config.NumberColumn(col, format="%.1f m")
            elif "€" in col or "eur" in col_lower or "preis" in col_lower:
                column_configs[col] = st.column_config.NumberColumn(col, format="%.2f €")
            elif "%" in col:
                column_configs[col] = st.column_config.NumberColumn(col, format="%.1f %%")
            else:
                column_configs[col] = st.column_config.NumberColumn(col, format="%.2f")

    return column_configs


def parse_markdown_table(lines: list[str]) -> tuple[pd.DataFrame, dict[str, Any]] | None:
    """Wandelt Markdown-Tabellenzeilen in einen typisierten DataFrame mit Spaltenkonfigurationen um."""
    parsed = parse_markdown_table_raw(lines)
    if parsed is None:
        return None
    df, url_columns = parsed
    column_configs = _build_column_configs(df, url_columns)
    return df, column_configs


def _render_sorting_bar(df: pd.DataFrame, table_idx: int) -> pd.DataFrame:
    """Rendert eine übersichtliche Sortier-Leiste und schließt Quellenlinks strikt vom Sortieren aus."""
    sortable_cols = [c for c in df.columns if c.lower() not in _NON_SORTABLE_COLUMN_KEYWORDS]
    if not sortable_cols:
        return df

    default_idx = 0
    for idx, c in enumerate(sortable_cols):
        if any(kw in c.lower() for kw in ("höhe", "score", "metrik", "wert", "preis")):
            default_idx = idx
            break

    c_select, c_order = st.columns([3, 2])
    with c_select:
        selected_col = st.selectbox(
            "↕️ Sortieren nach:",
            options=sortable_cols,
            index=default_idx,
            key=f"tbl_sort_{table_idx}",
            help="Wähle eine Kennzahl oder Eigenschaft zum Sortieren (Quellen-Links sind bewusst ausgeschlossen).",
        )

    is_metric_descending = any(kw in selected_col.lower() for kw in ("höhe", "score", "wert", "meter", "stark"))
    with c_order:
        sort_order = st.radio(
            "Reihenfolge:",
            options=["Absteigend ⬇️", "Aufsteigend ⬆️"],
            index=0 if is_metric_descending else 1,
            horizontal=True,
            key=f"tbl_order_{table_idx}",
        )

    ascending = sort_order == "Aufsteigend ⬆️"
    try:
        return df.sort_values(by=selected_col, ascending=ascending)
    except Exception:
        return df


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

    table_counter = 0
    for block_type, content in blocks:
        if block_type == "text":
            if str(content).strip():
                st.markdown(content)
        elif block_type == "table":
            table_counter += 1
            table_lines = content
            result = parse_markdown_table_raw(table_lines)
            if result is None:
                st.markdown("\n".join(table_lines))
                continue

            df, url_columns = result
            col_configs = _build_column_configs(df, url_columns)
            sorted_df = _render_sorting_bar(df, table_counter)

            st.dataframe(
                sorted_df,
                use_container_width=True,
                hide_index=True,
                column_config=col_configs,
            )
            st.caption(
                "💡 *Gezielte Sortierung über die Leiste oben (Quelle ausgeschlossen) · "
                "Spaltenköpfe anklickbar · Vollbild & Suche oben rechts im Tabellenfenster.*"
            )
            raw_table_md = "\n".join(table_lines)
            with st.expander("📋 Reine Markdown-Tabelle anzeigen", expanded=False):
                st.markdown(raw_table_md)
