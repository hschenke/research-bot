"""Input-Validierung, Path-Traversal-Schutz und URL/HTML-Sanitization."""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import urlparse

from src.config.constants import (
    ALLOWED_URL_SCHEMES,
    MAX_EMAIL_LENGTH,
    MAX_FOCUS_LENGTH,
    MAX_TOPIC_LENGTH,
    MIN_TOPIC_LENGTH,
)
from src.domain.enums import ResearchDepth
from src.domain.exceptions import InputValidationError, SecurityViolationError
from src.domain.models import ResearchRequest

# Linearzeit-Ausdrücke ohne verschachtelte Quantifizierer (ReDoS-sicher)
_EMAIL_LOCAL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
_EMAIL_DOMAIN_PATTERN = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]{1,500})\]\(([^)\s]{1,2048})\)")


def strip_control_characters(value: str) -> str:
    """Entfernt unsichtbare Steuerzeichen aus Benutzereingaben (außer Zeilenumbruch/Tab)."""
    return _CONTROL_CHARS_PATTERN.sub("", value)


def validate_email_address(raw_email: str) -> str:
    """Validiert eine E-Mail-Adresse linear und ohne ReDoS-anfällige Regexes."""
    email = strip_control_characters(raw_email).strip()
    if not email:
        return ""
    if len(email) > MAX_EMAIL_LENGTH or " " in email or email.count("@") != 1:
        raise InputValidationError("Bitte eine gültige E-Mail-Adresse eingeben.")

    local_part, domain_part = email.split("@", 1)
    if not local_part or len(local_part) > 64 or not domain_part or len(domain_part) > 190:
        raise InputValidationError("Ungültiges Format der E-Mail-Adresse.")

    if not _EMAIL_LOCAL_PATTERN.match(local_part) or not _EMAIL_DOMAIN_PATTERN.match(domain_part):
        raise InputValidationError("Ungültige Zeichen in der E-Mail-Adresse.")

    tld = domain_part.rsplit(".", 1)[-1]
    if len(tld) < 2 or not tld.isalpha():
        raise InputValidationError("Ungültige Top-Level-Domain der E-Mail-Adresse.")

    return email


_MODEL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9\.\-_]+$")
_FORMAT_KEY_PATTERN = re.compile(r"^[a-z0-9_]{2,40}$")


def validate_model_identifier(raw_model: str, fallback: str = "gemini-3.5-flash-lite") -> str:
    """Prüft einen KI-Modellnamen strikt gegen eine Zeichen-Positivliste."""
    clean_model = strip_control_characters(raw_model).strip() or fallback
    if len(clean_model) > 100 or not _MODEL_NAME_PATTERN.match(clean_model):
        raise InputValidationError(f"Ungültiger Modellbezeichner: '{clean_model[:40]}'")
    return clean_model


def validate_format_key(raw_format: str, fallback: str = "compact") -> str:
    """Validiert den Schlüssel eines Berichtsformats per Whitelist."""
    clean_fmt = strip_control_characters(raw_format).strip().lower() or fallback
    if not _FORMAT_KEY_PATTERN.match(clean_fmt):
        raise InputValidationError("Ungültiger Berichtsformat-Schlüssel.")
    return clean_fmt


def validate_research_request(
    topic: str,
    focus_instructions: str = "",
    depth: ResearchDepth = ResearchDepth.STANDARD,
    language: str = "de",
    notify_email: str = "",
    model: str = "gemini-3.5-flash-lite",
    report_format: str = "compact",
) -> ResearchRequest:
    """Validiert und bereinigt alle Eingabefelder für einen neuen Recherche-Auftrag."""
    clean_topic = strip_control_characters(topic).strip()
    if len(clean_topic) < MIN_TOPIC_LENGTH:
        raise InputValidationError(
            f"Das Thema muss mindestens {MIN_TOPIC_LENGTH} Zeichen lang sein."
        )
    if len(clean_topic) > MAX_TOPIC_LENGTH:
        raise InputValidationError(
            f"Das Thema darf maximal {MAX_TOPIC_LENGTH} Zeichen lang sein."
        )

    clean_focus = strip_control_characters(focus_instructions).strip()
    if len(clean_focus) > MAX_FOCUS_LENGTH:
        raise InputValidationError(
            f"Zusätzliche Fokus-Hinweise dürfen maximal {MAX_FOCUS_LENGTH} Zeichen umfassen."
        )

    clean_lang = language.strip().lower()
    if clean_lang not in {"de", "en"}:
        raise InputValidationError("Unterstützte Sprachen sind 'de' und 'en'.")

    clean_email = validate_email_address(notify_email)
    clean_model = validate_model_identifier(model)
    clean_format = validate_format_key(report_format)

    return ResearchRequest(
        topic=clean_topic,
        focus_instructions=clean_focus,
        depth=depth,
        report_format=clean_format,
        language=clean_lang,
        notify_email=clean_email,
        model=clean_model,
    )


def resolve_safe_path(base_dir: Path, relative_target: str | Path) -> Path:
    """Löst einen Dateipfad sicher auf und verhindert jegliches Path Traversal."""
    resolved_base = base_dir.resolve()
    candidate = (resolved_base / relative_target).resolve()
    if not candidate.is_relative_to(resolved_base):
        raise SecurityViolationError(
            f"Sicherheitsverletzung: Pfad '{relative_target}' liegt außerhalb von '{resolved_base}'."
        )
    return candidate


def sanitize_display_url(raw_url: str) -> str:
    """
    Bereinigt eine URL für die Darstellung in HTML/Markdown (Schutz vor XSS wie `javascript:`).
    Gibt `#` zurück, falls das Schema nicht `http` oder `https` ist.
    """
    cleaned = strip_control_characters(raw_url).strip().rstrip(".,;)>\"'")
    if not cleaned:
        return "#"

    parsed = urlparse(cleaned)
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES or not parsed.netloc:
        return "#"

    # Schutz vor Attribut-Ausbruch in HTML href="..."
    return (
        cleaned.replace('"', "%22")
        .replace("'", "%27")
        .replace("<", "%3C")
        .replace(">", "%3E")
    )


def sanitize_markdown_links(markdown_text: str) -> str:
    """Bereinigt alle Markdown-Links `[Titel](URL)` gegen schädliche URL-Schemata."""

    def _replace_match(match: re.Match[str]) -> str:
        label = html.escape(match.group(1), quote=False)
        safe_url = sanitize_display_url(match.group(2))
        if safe_url == "#":
            return label
        return f"[{label}]({safe_url})"

    return _MARKDOWN_LINK_PATTERN.sub(_replace_match, markdown_text)
