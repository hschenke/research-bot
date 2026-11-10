"""Parser- und Normalisierungslogik für Markdown-Tabellen in Recherche-Berichten."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

_SEPARATOR_CELL_PATTERN = re.compile(r"^:?-+:?$")
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_PLAIN_URL_PATTERN = re.compile(r"^https?://[^\s]+$")
_NUMERIC_STRIP_PATTERN = re.compile(
    r"\s*(?:m|km|€|\$|%|pts|punkte|sterne|min|h|std)$",
    re.IGNORECASE,
)


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


def clean_cell_content(cell: str) -> tuple[str, str | None]:
    """Bereinigt Markdown-Formatierungen in einer Zelle und extrahiert Hyperlinks."""
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


def deduplicate_headers(raw_headers: list[str]) -> list[str]:
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


def try_convert_numeric_series(series: pd.Series) -> pd.Series | None:
    """Versucht, eine Spalte in numerische Werte (Int/Float) für korrektes Sortieren zu konvertieren."""
    try:
        return pd.to_numeric(series, errors="raise")
    except (ValueError, TypeError):
        pass

    cleaned = series.astype(str).str.strip().str.replace(",", ".", regex=False)
    cleaned = cleaned.str.replace(_NUMERIC_STRIP_PATTERN, "", regex=True).str.strip()
    try:
        return pd.to_numeric(cleaned, errors="raise")
    except (ValueError, TypeError):
        return None


def parse_markdown_table_raw(lines: list[str]) -> tuple[pd.DataFrame, set[str]] | None:
    """Wandelt Markdown-Tabellenzeilen in einen DataFrame und identifiziert Link-Spalten."""
    if len(lines) < 3 or not is_markdown_separator_row(lines[1]):
        return None

    raw_headers = [c.strip() for c in lines[0].strip()[1:-1].split("|")]
    clean_headers = deduplicate_headers(raw_headers)
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
            cleaned_val, link_label = clean_cell_content(cell)
            if link_label is not None:
                url_columns.add(col_name)
            cleaned_row.append(cleaned_val)
        rows.append(cleaned_row)

    if not rows:
        return None

    df = pd.DataFrame(rows, columns=clean_headers)
    for col in df.columns:
        if col in url_columns:
            continue
        converted = try_convert_numeric_series(df[col])
        if converted is not None:
            df[col] = converted

    return df, url_columns


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
