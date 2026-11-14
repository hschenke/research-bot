"""Fachliche Enumerationen für Status, Phasen, Recherche-Tiefen und Rollen."""

from enum import StrEnum


class JobStatus(StrEnum):
    """Lebenszyklus-Status eines Recherche-Auftrags."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobPhase(StrEnum):
    """Aktueller Verarbeitungsschritt innerhalb eines Recherche-Auftrags."""

    QUEUED = "queued"
    PLANNING = "planning"
    DEEP_BASELINE = "deep_baseline"
    INVESTIGATING = "investigating"
    GAP_ANALYSIS = "gap_analysis"
    SYNTHESIZING = "synthesizing"
    NOTIFYING = "notifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResearchDepth(StrEnum):
    """Konfigurierbare Recherche-Tiefe nach gewünschtem Recherche-Aufwand."""

    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class WorkerMode(StrEnum):
    """Ausführungsmodus für Hintergrund-Jobs."""

    AUTO = "auto"
    GITHUB = "github"
    LOCAL = "local"


class EmailProvider(StrEnum):
    """Unterstützte E-Mail-Versanddienste."""

    RESEND = "resend"
    SMTP = "smtp"
    NONE = "none"


class AuthRole(StrEnum):
    """Berechtigungsrollen in der Web-Applikation."""

    ADMIN = "admin"
    READONLY = "readonly"
    GUEST = "guest"
