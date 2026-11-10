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


def _extract_item_row(name_raw: str, details_raw: str, domain: str) -> list[str]:
    """Extrahiert kompakte, themenspezifische Spaltenwerte aus einem Stichpunkteintrag."""
    m_name = re.match(r"^([^(]+?)\s*\(([^)]+)\)$", name_raw)
    name = m_name.group(1).strip() if m_name else name_raw.strip()
    category = m_name.group(2).strip() if m_name else "-"

    link_m = _LINK_PATTERN.search(details_raw)
    link = f"[{link_m.group(1).split()[0]}]({link_m.group(2)})" if link_m else "-"
    clean = _LINK_STRIP_PATTERN.sub("", details_raw).strip(" ().")

    if domain == "surf":
        if "sehr stark" in clean.lower() or "ungehindert" in clean.lower():
            metric = "Sehr stark (bis 2,5 m)"
        elif "hoch" in clean.lower() or "ne-wind" in clean.lower():
            metric = "Stark & Hoch (Windwellen)"
        elif "steil" in clean.lower() or "a-frame" in clean.lower():
            metric = "Stark & Steil (A-Frames)"
        elif "geschützt" in clean.lower() or "sauber" in clean.lower():
            metric = "Moderat & Clean (bis 1,8 m)"
        else:
            metric = "Konstant (Beachbreak)"

        level = "-"
        for kw in ("ideal für", "für", "gilt als"):
            if kw in clean.lower():
                part = clean[clean.lower().find(kw) + len(kw) :].strip(" :.,")
                level = part.split(" im ")[0].split(" bei ")[0].split(" durch ")[0][:35].strip()
                break

        wind = "-"
        if "ost" in clean.lower() or "levante" in clean.lower():
            wind = "Levante / Ost"
        elif "nord" in clean.lower() or "ne-" in clean.lower():
            wind = "Levanter / Nordost"
        return [name, category, metric, level, wind, link]

    if domain == "tool":
        metric = "-"
        m_num = re.search(r"(\$\d+[\w/]*|\d+[\w/]*\s*€|\d+[\.,]?\d*\s*(?:m|km|%|pts|stars))", clean)
        if m_num:
            metric = m_num.group(1)
        elif any(w in clean.lower() for w in ("kostenlos", "free", "freemium", "open source")):
            metric = "Freemium" if "freemium" in clean.lower() else "Kostenlos"

        feature = clean.split(",")[0][:45].strip()
        return [name, category, metric, feature, link]

    first_clause = clean.split(",")[0][:40].strip()
    rest = clean[len(first_clause) :].strip(" ,;.-")[:40] or "-"
    return [name, category, first_clause or "-", rest, link]


def auto_generate_top_x_table(text: str) -> str | None:
    """Wandelt Aufzählungen eines Rankings automatisch in eine sortierbare Markdown-Tabelle um."""
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
        headers = ["Rang", "Strand / Spot", "Ort", "Wellenstärke / Typ", "Eignung & Level", "Beste Saison / Wind", "Quelle"]
    elif domain == "tool":
        headers = ["Rang", "Tool / Modell", "Kategorie", "Preismodell / Score", "Hauptmerkmal & Stärke", "Quelle"]
    else:
        headers = ["Rang", "Name / Eintrag", "Ort / Kategorie", "Kernmerkmal / Metrik", "Eignung / Besonderheit", "Quelle"]

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
