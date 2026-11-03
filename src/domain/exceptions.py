"""Anwendungsspezifische Exception-Hierarchie für präzise Fehlerbehandlung."""


class ResearchBotError(Exception):
    """Basisklasse für alle fachlichen und technischen Fehler des Research-Bots."""


class InputValidationError(ResearchBotError):
    """Ungültige oder unsichere Benutzereingabe an einer Systemgrenze."""


class SecurityViolationError(ResearchBotError):
    """Verstoß gegen Sicherheitsrichtlinien (z. B. Path Traversal oder unzulässige URL)."""


class SSRFBlockedError(SecurityViolationError):
    """Blockierter Netzwerkzugriff auf interne, private oder nicht erlaubte Zieladressen."""


class StorageError(ResearchBotError):
    """Fehler bei einer Datenbankabfrage oder Persistenzoperation."""


class StorageConnectionError(StorageError):
    """Verbindungsfehler zum Speicher-Backend (Turso Cloud oder SQLite)."""


class WebFetchError(ResearchBotError):
    """Fehler beim Abrufen oder Extrahieren einer externen Webseite."""


class LLMProviderError(ResearchBotError):
    """Fehler bei der Kommunikation mit der KI-Schnittstelle (Gemini API)."""


class DispatchError(ResearchBotError):
    """Fehler beim Starten eines Hintergrund-Jobs (z. B. GitHub Actions API)."""


class NotificationError(ResearchBotError):
    """Fehler beim Versenden einer E-Mail-Benachrichtigung."""


class JobCancelledError(ResearchBotError):
    """Signalisiert den regulären Abbruch eines laufenden Recherche-Auftrags durch den Nutzer."""
