"""Automatischer Tabellen-Generator für Top-X / Ranking-Listen in Recherche-Berichten."""

from __future__ import annotations

import re

_BULLET_ITEM_PATTERN = re.compile(r"^\s*(?:[\*\-]|\d+[\.\)])\s+\*\*([^*]+?)\*\*[:\s–\-]*\s*(.+)$")
_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_LINK_STRIP_PATTERN = re.compile(r"\(?\s*\[[^\]]+\]\(https?://[^\s)]+\)\s*\)?")
_TOP_X_HEADING_KEYWORDS = (
    "top",
    "auswahl",
    "ranking",
    "vergleich",
    "beste",
    "liste",
    "spots",
    "strände",
    "tools",
    "modelle",
    "kandidaten",
)


def auto_generate_top_x_table(text: str) -> str | None:
    """Wandelt Aufzählungen eines Rankings automatisch in eine formatierte Markdown-Tabelle um."""
    sections = re.split(r"\n(?=##\s+)", text)
    matches: list[tuple[str, str]] = []

    # 1. Bevorzuge Abschnitte mit passender Überschrift
    for sec in sections:
        header = sec.strip().splitlines()[0] if sec.strip() else ""
        if header.startswith("## 🛡️") or header.startswith("## Quellen"):
            continue
        sec_matches = [m for line in sec.splitlines() if (m := _BULLET_ITEM_PATTERN.match(line))]
        if len(sec_matches) >= 2 and any(kw in header.lower() for kw in _TOP_X_HEADING_KEYWORDS):
            matches = [(m.group(1).rstrip(":").strip(), m.group(2).strip()) for m in sec_matches]
            break

    # 2. Fallback: Erster passender Abschnitt mit mindestens 2 Stichpunkten
    if not matches:
        for sec in sections:
            header = sec.strip().splitlines()[0] if sec.strip() else ""
            if header.startswith("## 🛡️") or header.startswith("## Quellen"):
                continue
            sec_matches = [m for line in sec.splitlines() if (m := _BULLET_ITEM_PATTERN.match(line))]
            if len(sec_matches) >= 2:
                matches = [(m.group(1).rstrip(":").strip(), m.group(2).strip()) for m in sec_matches]
                break

    if len(matches) < 2:
        return None

    rows = [
        "| Rang | Name / Eintrag | Kernfakten & Details | Quelle |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for idx, (name, details) in enumerate(matches, start=1):
        link_m = _LINK_PATTERN.search(details)
        link_str = f"[{link_m.group(1)}]({link_m.group(2)})" if link_m else "-"
        clean_details = _LINK_STRIP_PATTERN.sub("", details).strip(" ().")
        rows.append(f"| {idx} | {name} | {clean_details} | {link_str} |")

    return "\n".join(rows)
