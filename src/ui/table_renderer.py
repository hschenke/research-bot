"""Interaktive Tabellen-Aufbereitung & Sortierung für Recherche-Berichte in Streamlit."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
import streamlit as st

_SEPARATOR_CELL_PATTERN = re.compile(r"^:?-+:?$")
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_PLAIN_URL_PATTERN = re.compile(r"^https?://[^\s]+$")


def is_markdown_table_row(line: str) -> bool:
    """Prüft, ob eine Zeile eine typische Markdown-Tabellenzeile darstellt."""
    s = line.strip()
    return bool(s.startswith("|") and s.endswith("|") and "|" in s[1:-1])


def is_markdown_separator_row(line: str) -> bool:
    """Prüft, ob eine Zeile die Markdown-Tabellen-Trennlinie (|---|---|) darstellt."""
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return False
    cells = [c.strip() for c in s[1:-1].split("|")]
    return len(cells) >= 1 and all(bool(_SEPARATOR_CELL_PATTERN.match(c)) for c in cells if c)


def _clean_cell_content(cell: str) -> tuple[str, str | None]:
    """Bereinigt Markdown-Formatierungen in einer Zelle und extrahiert ggf. Hyperlinks."""
    s = cell.strip()
    link_match = _MARKDOWN_LINK_PATTERN.search(s)
    if link_match:
        return link_match.group(2), link_match.group(1)
    if _PLAIN_URL_PATTERN.match(s):
        return s, "URL"

    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
    cleaned = cleaned.replace("`", "")
    return cleaned, None


def _deduplicate_headers(raw_headers: list[str]) -> list[str]:
    """Erzeugt eindeutige Spaltenbezeichnungen aus den Roh-Tabellenköpfen."""
    headers = [re.sub(r"\*\*([^*]+)\*\*", r"\1", h).strip() for h in raw_headers]
    seen_headers: dict[str, int] = {}
    clean_headers: list[str] = []
    for h in headers:
        name = h or "Spalte"
        if name in seen_headers:
            seen_headers[name] += 1
            clean_headers.append(f"{name} ({seen_headers[name]})")
        else:
            seen_headers[name] = 1
            clean_headers.append(name)
    return clean_headers


def parse_markdown_table(lines: list[str]) -> tuple[pd.DataFrame, dict[str, Any]] | None:
    """Wandelt Markdown-Tabellenzeilen in einen typisierten DataFrame mit Sortier- & Link-Konfiguration um."""
    if len(lines) < 3 or not is_markdown_separator_row(lines[1]):
        return None

    raw_headers = [c.strip() for c in lines[0].strip()[1:-1].split("|")]
    clean_headers = _deduplicate_headers(raw_headers)
    if not clean_headers or all(not h for h in clean_headers):
        return None

    rows: list[list[str]] = []
    url_columns: set[str] = set()

    for line in lines[2:]:
        if not is_markdown_table_row(line):
            continue
        cells = [c.strip() for c in line.strip()[1:-1].split("|")]
        if len(cells) < len(clean_headers):
            cells += [""] * (len(clean_headers) - len(cells))
        elif len(cells) > len(clean_headers):
            cells = cells[: len(clean_headers)]

        cleaned_row: list[str] = []
        for idx, cell in enumerate(cells):
            col_name = clean_headers[idx]
            cleaned_val, link_label = _clean_cell_content(cell)
            if link_label is not None:
                url_columns.add(col_name)
            cleaned_row.append(cleaned_val)
        rows.append(cleaned_row)

    if not rows:
        return None

    df = pd.DataFrame(rows, columns=clean_headers)
    column_configs = _build_column_configs(df, url_columns)
    return df, column_configs


def _build_column_configs(df: pd.DataFrame, url_columns: set[str]) -> dict[str, Any]:
    """Konfiguriert Streamlit-Spaltentypen (Links, Ganzzahlen, Dezimalwerte) für korrektes Sortieren."""
    column_configs: dict[str, Any] = {}
    for col in df.columns:
        if col in url_columns:
            column_configs[col] = st.column_config.LinkColumn(col, display_text="🔗 Link")
            continue

        try:
            numeric_series = pd.to_numeric(df[col], errors="raise")
            df[col] = numeric_series
            if pd.api.types.is_integer_dtype(df[col]):
                column_configs[col] = st.column_config.NumberColumn(col, format="%d")
            else:
                column_configs[col] = st.column_config.NumberColumn(col, format="%.2f")
        except (ValueError, TypeError):
            pass
    return column_configs


def split_markdown_by_tables(markdown_text: str) -> list[tuple[str, Any]]:
    """Zerlegt Markdown-Text in alternierende Abschnitte: ('text', str) und ('table', list[str])."""
    lines = markdown_text.splitlines()
    blocks: list[tuple[str, Any]] = []
    current_text: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        if is_markdown_table_row(line) and i + 1 < len(lines) and is_markdown_separator_row(lines[i + 1]):
            if current_text:
                blocks.append(("text", "\n".join(current_text)))
                current_text = []

            table_lines = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and is_markdown_table_row(lines[i]):
                table_lines.append(lines[i])
                i += 1
            blocks.append(("table", table_lines))
            continue

        current_text.append(line)
        i += 1

    if current_text:
        blocks.append(("text", "\n".join(current_text)))

    return blocks


def render_markdown_with_interactive_tables(content_markdown: str) -> None:
    """Rendert Markdown mit interaktiven, sortierbaren Streamlit-Dataframes für alle Tabellen."""
    blocks = split_markdown_by_tables(content_markdown)
    has_any_table = any(b_type == "table" for b_type, _ in blocks)

    if not has_any_table:
        st.markdown(content_markdown)
        return

    for block_type, content in blocks:
        if block_type == "text":
            if str(content).strip():
                st.markdown(content)
        elif block_type == "table":
            table_lines = content
            result = parse_markdown_table(table_lines)
            if result is None:
                st.markdown("\n".join(table_lines))
                continue

            df, col_configs = result
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                column_config=col_configs,
            )
            st.caption("💡 *Spaltenköpfe anklicken zum Sortieren (Rang, Name, Werte) · Vollbild & Suche via Tabelle oben rechts.*")
            raw_table_md = "\n".join(table_lines)
            with st.expander("📋 Reine Markdown-Tabelle anzeigen", expanded=False):
                st.markdown(raw_table_md)
