"""Hilfsfunktionen für eingedeutschte Datums-, Zeit- und Laufzeitformatierungen."""

from __future__ import annotations

from datetime import datetime


def format_datetime_german(iso_str: str | None) -> str:
    """Formatiert einen ISO-Datums-/Zeitstempel ins deutsche Format `dd.mm.yyyy hh:mm`.

    Robust gegenüber leeren Werten, Zeitzonenangaben (Z/+00:00) oder Mikrosekunden.
    """
    if not iso_str:
        return "-"
    cleaned = iso_str.strip()
    if not cleaned:
        return "-"

    try:
        normalized = cleaned.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        # Defensiver Fallback für abweichende String-Formate
        parts = cleaned.replace("T", " ").split()
        if len(parts) >= 2:
            date_parts = parts[0].split("-")
            time_parts = parts[1].split(":")
            if len(date_parts) == 3 and len(time_parts) >= 2:
                year, month, day = date_parts[0], date_parts[1], date_parts[2][:2]
                hour, minute = time_parts[0], time_parts[1]
                return f"{day}.{month}.{year} {hour}:{minute}"
        return cleaned[:16]
