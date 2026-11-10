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


def _detect_topic_domain(text: str) -> str:
    """Bestimmt den fachlichen Kontext für maßgeschneiderte, aussagekräftige Tabellenspalten."""
    lower = text.lower()
    if any(k in lower for k in ("wellen", "strand", "strände", "surf", "kueste", "küste", "meer")):
        return "surf"
    if any(k in lower for k in ("tool", "software", "ki", "app", "framework", "bibliothek", "plattform")):
        return "tool"
    return "general"


def _extract_surf_row(name: str, category: str, clean: str) -> list[str]:
    """Extrahiert sortierbare Surf-Kennzahlen: numerische Wellenhöhe, Fachbegriffe mit ❓ und Level."""
    m_meter = re.search(r"(\d+[,\.]\d*)\s*m", clean.lower())
    if m_meter:
        height = float(m_meter.group(1).replace(",", "."))
    elif "sehr stark" in clean.lower() or "ungehindert" in clean.lower():
        height = 2.5
    elif "hoch" in clean.lower() or "ne-wind" in clean.lower():
        height = 2.2
    elif "steil" in clean.lower() or "a-frame" in clean.lower():
        height = 2.0
    elif "geschützt" in clean.lower() or "sauber" in clean.lower():
        height = 1.8
    else:
        height = 1.5

    if "a-frame" in clean.lower() or "steil" in clean.lower():
        w_type = "A-Frames (Felsgrund)"
    elif "wind" in clean.lower() or "kite" in clean.lower():
        w_type = "Windwellen"
    elif "geschützt" in clean.lower() or "sauber" in clean.lower():
        w_type = "Sanfte Wellen (Geschützt)"
    elif "reef" in clean.lower() or "riff" in clean.lower():
        w_type = "Reefbreak"
    elif "ungehindert" in clean.lower() or "exponiert" in clean.lower():
        w_type = "Beachbreak (Offen)"
    else:
        w_type = "Beachbreak"

    if "shortboard" in clean.lower() or "profi" in clean.lower() or "experte" in clean.lower():
        level = "Level 3 - Experten / Shortboard"
    elif "strömung" in clean.lower() and height >= 2.3:
        level = "Level 3 - Experten (Strömung)"
    elif "wind" in clean.lower() or "kite" in clean.lower():
        level = "Level 2 - Kitesurf / Windsurf"
    elif "longboard" in clean.lower() or "anfänger" in clean.lower() or "einsteiger" in clean.lower():
        level = "Level 1 - Einsteiger / Longboard"
    else:
        level = "Level 2 - Fortgeschrittene"

    if "herbst" in clean.lower() and "winter" in clean.lower():
        season = "3 - Herbst / Winter"
    elif "winter" in clean.lower():
        season = "4 - Winter"
    elif "herbst" in clean.lower():
        season = "3 - Herbst"
    elif "sommer" in clean.lower():
        season = "2 - Sommer"
    elif "frühling" in clean.lower() or "frühjahr" in clean.lower():
        season = "1 - Frühling"
    else:
        season = "3 - Herbst / Winter"

    return [name, category, f"{height:.1f}", w_type, level, season]


def _extract_item_row(name_raw: str, details_raw: str, domain: str) -> list[str]:
    """Extrahiert kompakte, themenspezifische Spaltenwerte aus einem Stichpunkteintrag ohne Quellenspalte."""
    m_name = re.match(r"^([^(]+?)\s*\(([^)]+)\)$", name_raw)
    name = m_name.group(1).strip() if m_name else name_raw.strip()
    category = m_name.group(2).strip() if m_name else "-"
    clean = _LINK_STRIP_PATTERN.sub("", details_raw).strip(" ().")

    if domain == "surf":
        return _extract_surf_row(name, category, clean)

    if domain == "tool":
        metric = "-"
        m_num = re.search(r"(\$\d+[\w/]*|\d+[\w/]*\s*€|\d+[\.,]?\d*\s*(?:m|km|%|pts|stars))", clean)
        if m_num:
            metric = m_num.group(1)
        elif any(w in clean.lower() for w in ("kostenlos", "free", "freemium", "open source")):
            metric = "Freemium" if "freemium" in clean.lower() else "Kostenlos"

        feature = clean.split(",")[0][:40].strip()
        return [name, category, metric, feature]

    first_clause = clean.split(",")[0][:35].strip()
    rest = clean[len(first_clause) :].strip(" ,;.-")[:35] or "-"
    return [name, category, first_clause or "-", rest]


def auto_generate_top_x_table(text: str) -> str | None:
    """Wandelt Aufzählungen eines Rankings automatisch in eine kompakte, sortierbare Markdown-Tabelle um."""
    sections = re.split(r"\n(?=##\s+)", text)
    matches: list[tuple[str, str]] = []

    for sec in sections:
        header = sec.strip().splitlines()[0] if sec.strip() else ""
        if header.startswith("## 🛡️") or header.startswith("## Quellen"):
            continue
        sec_matches = [m for line in sec.splitlines() if (m := _BULLET_ITEM_PATTERN.match(line))]
        if len(sec_matches) >= 2 and any(kw in header.lower() for kw in _TOP_X_HEADING_KEYWORDS):
            matches = [(m.group(1).rstrip(":").strip(), m.group(2).strip()) for m in sec_matches]
            break

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

    domain = _detect_topic_domain(text)
    if domain == "surf":
        headers = [
            "Rang",
            "Strand / Spot",
            "Ort",
            "Wellenhöhe max. (m)",
            "Wellentyp",
            "Level",
            "Beste Saison",
        ]
    elif domain == "tool":
        headers = ["Rang", "Tool / Modell", "Kategorie", "Preismodell / Score", "Hauptmerkmal & Stärke"]
    else:
        headers = ["Rang", "Name / Eintrag", "Ort / Kategorie", "Kernmerkmal / Metrik", "Eignung / Besonderheit"]

    separator = [" :--- " for _ in headers]
    rows = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(separator) + "|",
    ]

    for idx, (name, details) in enumerate(matches, start=1):
        parsed_cols = _extract_item_row(name, details, domain)
        row_cells = [str(idx)] + parsed_cols
        rows.append("| " + " | ".join(row_cells) + " |")

    return "\n".join(rows)
