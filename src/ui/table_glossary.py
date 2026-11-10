"""Kompaktes Fachglossar für Surf- & Sport-Begriffe in Tabellenansichten."""

from __future__ import annotations

import html
import re

TERM_DEFINITIONS: dict[str, str] = {
    "beachbreak": "Wellen brechen über einer Sandbank. Veränderlich durch Gezeiten und Strömungen; meist fehlerverzeihend.",
    "a-frame": "Symmetrische Welle in Dachform (A-Form), die an einer Spitze gleichzeitig nach links und rechts bricht (für zwei Surfer gleichzeitig).",
    "reefbreak": "Welle bricht über festem Fels- oder Korallenriff. Erzeugt extrem kraftvolle, steile und hohle Wellen (Barrels).",
    "pointbreak": "Welle bricht entlang einer Landspitze oder Bucht; ermöglicht besonders lange und geordnete Ritte.",
    "shorebreak": "Wellen brechen mit großer Wucht direkt am Ufersand auf die Brandungszone; erfordert erhöhte Aufmerksamkeit.",
    "windwelle": "Kabbelige, unruhige Wellen, die durch lokalen Wind entstehen; optimal für Wind- und Kitesurfen.",
    "sanfte welle": "Gutmütige, geschützte Wellen ohne steile Brecherkante; ideal für Longboarder und Einsteiger.",
    "shortboard": "Kurzes, wendiges Surfbrett für radikale Drehungen und steile Brecherkanten.",
    "longboard": "Langes, stabiles Surfbrett mit hohem Auftrieb für klassisches Gleiten und sanfte Wellen.",
    "swell": "Geordnete Dünung aus dem offenen Meer; bringt saubere Wellensets an die Küste.",
    "levante": "Feuchter, kräftiger Ostwind im westlichen Mittelmeer; Hauptantrieb für Wellen an der Costa Blanca.",
    "lebeche": "Heißer, böiger Südwestwind aus Nordafrika; oft ablandig.",
}

GLOSSARY_HELP_TEXT = (
    "❓ Fachbegriffe kurz erklärt:\n"
    "• Beachbreak: Brecher über Sandbank (gutmütig, fehlerverzeihend)\n"
    "• A-Frames: Dachförmige Welle, bricht nach links & rechts\n"
    "• Reefbreak: Brecher über Fels-/Korallenriff (steil, kraftvoll)\n"
    "• Pointbreak: Wellen entlang Landzunge für lange Ritte\n"
    "• Windwellen: Durch lokalen Wind erzeugte Kabbelwellen\n"
    "• Dünung (Swell): Saubere Meereswellen aus fernen Tiefdruckgebieten"
)


def decorate_cell_with_tooltips(cell_text: str) -> str:
    """Dekoriert Fachbegriffe direkt in der Tabellenzelle mit dem eingekreisten (?) und Hover/Touch-Tooltip."""
    if not cell_text or "surf-tip-icon" in cell_text:
        return cell_text

    # Längste Begriffe zuerst prüfen (z. B. "sanfte welle" vor "welle")
    for term_key in sorted(TERM_DEFINITIONS.keys(), key=len, reverse=True):
        pattern = re.compile(rf"\b{re.escape(term_key)}(?:s|n)?\b", re.IGNORECASE)
        m = pattern.search(cell_text)
        if m:
            definition = TERM_DEFINITIONS[term_key]
            safe_def = html.escape(definition)
            badge = (
                f'<span class="surf-tip-icon" tabindex="0" role="button" '
                f'aria-label="Erklärung anzeigen" title="{safe_def}">'
                f'?<span class="surf-tip-pop">{safe_def}</span></span>'
            )
            return cell_text[: m.end()] + " " + badge + cell_text[m.end() :]
    return cell_text
