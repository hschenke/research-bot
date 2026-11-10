"""Kompaktes Fachglossar für Surf- & Sport-Begriffe in Tabellenansichten."""

from __future__ import annotations

import streamlit as st

GLOSSARY_HELP_TEXT = (
    "❓ Fachbegriffe kurz erklärt:\n"
    "• Beachbreak: Brecher über Sandbank (gutmütig, fehlerverzeihend)\n"
    "• A-Frames: Dachförmige Welle, bricht nach links & rechts\n"
    "• Reefbreak: Brecher über Fels-/Korallenriff (steil, kraftvoll)\n"
    "• Pointbreak: Wellen entlang Landzunge für lange Ritte\n"
    "• Windwellen: Durch lokalen Wind erzeugte Kabbelwellen\n"
    "• Dünung (Swell): Saubere Meereswellen aus fernen Tiefdruckgebieten"
)


def render_surf_glossary_popover() -> None:
    """Rendert ein kompaktes Popover mit Kurzerklärungen zu Fachbegriffen wie Beachbreak und A-Frames."""
    with st.popover("❓ Fachbegriffe erklärt", help="Kompakte Erläuterungen zu Wellentypen und Dünung"):
        st.markdown(
            """
            **🏄 Surf-Fachbegriffe kurz erklärt:**
            - 🌊 **Beachbreak:** Wellen brechen über einer Sandbank. Veränderlich durch Gezeiten und Strömungen; ideal für Einsteiger bis Fortgeschrittene.
            - 📐 **A-Frames:** Symmetrische Welle in Dreiecks- bzw. Dachform, die von der Spitze gleichzeitig nach links und rechts bricht. Perfekt für zwei Surfer gleichzeitig.
            - 🪨 **Reefbreak:** Welle bricht über festem Fels- oder Korallenriff. Erzeugt extrem kraftvolle, steile und oft hohle Wellen (Barrels).
            - 🏖️ **Pointbreak:** Welle bricht entlang einer Landspitze oder Bucht; erzeugt besonders lange und geordnete Ritte.
            - 💨 **Windwellen:** Unruhige, kurzperiodische Kabbelwellen, die durch lokalen Wind entstehen; ideal für Wind- und Kitesurfen.
            - 🌊 **Dünung (Swell):** Geordnete, energiereiche Meereswellen aus fernen Tiefdruckgebieten, die sauber an die Küste rollen.
            - 🌬️ **Levante & Lebeche:** Die dominierenden Winde an der Costa Blanca (Levante = feuchter Ostwind mit Wellen, Lebeche = heißer Südwestwind).
            """
        )
