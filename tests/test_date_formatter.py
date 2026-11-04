"""Unit-Tests für deutsche Datums- und Zeitformatierung."""

from __future__ import annotations

from src.ui.date_formatter import format_datetime_german


def test_format_datetime_german_iso_variants() -> None:
    # Standard ISO mit UTC-Offset
    assert format_datetime_german("2026-11-04T15:38:12+00:00") == "04.11.2026 15:38"

    # Standard ISO mit Z
    assert format_datetime_german("2026-11-04T17:30:00Z") == "04.11.2026 17:30"

    # Mit Mikrosekunden
    assert format_datetime_german("2026-05-12T09:05:43.123456") == "12.05.2026 09:05"

    # Leerzeichen-getrennt
    assert format_datetime_german("2026-01-01 08:00:00") == "01.01.2026 08:00"


def test_format_datetime_german_empty_and_invalid() -> None:
    assert format_datetime_german(None) == "-"
    assert format_datetime_german("") == "-"
    assert format_datetime_german("   ") == "-"
