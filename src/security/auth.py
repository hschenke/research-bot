"""HMAC-basierte Token-Authentifizierung und Brute-Force-Schutz für die Web-App."""

from __future__ import annotations

import hashlib
import hmac
import threading
import time

from src.config.constants import (
    ADMIN_TOKEN_MAX_AGE_SECONDS,
    CLOCK_SKEW_TOLERANCE_SECONDS,
    LOGIN_LOCKOUT_SECONDS,
    MAX_FAILED_LOGIN_ATTEMPTS,
)
from src.domain.enums import AuthRole

_ADMIN_SCHEME = "adm"
_READONLY_SCHEME = "ro"
_HMAC_DOMAIN_ADMIN = "research_bot_admin_v1"
_HMAC_DOMAIN_READONLY = "research_bot_readonly_v1"


def _compute_hmac_hex(secret: str, message: str) -> str:
    """Berechnet einen kryptographischen HMAC-SHA256 Hex-Digest."""
    return hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_admin_token(password: str, issued_at: int | None = None) -> str:
    """Erstellt ein signiertes, 24h gültiges Admin-Session-Token."""
    clean_pw = password.strip()
    if not clean_pw:
        return ""
    timestamp = str(issued_at if issued_at is not None else int(time.time()))
    signature = _compute_hmac_hex(clean_pw, f"{_HMAC_DOMAIN_ADMIN}:{timestamp}")
    return f"{_ADMIN_SCHEME}:{timestamp}:{signature}"


def verify_admin_token(
    token: str,
    password: str,
    max_age_seconds: int = ADMIN_TOKEN_MAX_AGE_SECONDS,
) -> bool:
    """Prüft Signatur und Gültigkeitsdauer eines Admin-Tokens in konstanter Zeit."""
    clean_pw = password.strip()
    if not token or not clean_pw or not token.startswith(f"{_ADMIN_SCHEME}:"):
        return False

    parts = token.split(":", 2)
    if len(parts) != 3:
        return False

    _, ts_str, provided_sig = parts
    if not ts_str.isdigit():
        return False

    issued_ts = int(ts_str)
    now_ts = int(time.time())
    if (now_ts - issued_ts) > max_age_seconds:
        return False
    if issued_ts > (now_ts + CLOCK_SKEW_TOLERANCE_SECONDS):
        return False

    expected_sig = _compute_hmac_hex(clean_pw, f"{_HMAC_DOMAIN_ADMIN}:{ts_str}")
    return hmac.compare_digest(provided_sig, expected_sig)


def generate_readonly_token(password: str, scope: str = "reports") -> str:
    """
    Erzeugt ein Lese-Token für Direktlinks in Benachrichtigungs-E-Mails.
    Erlaubt ausschließlich das Lesen fertiger Berichte, niemals Konfigurationsänderungen.
    """
    clean_pw = password.strip()
    if not clean_pw:
        return ""
    signature = _compute_hmac_hex(clean_pw, f"{_HMAC_DOMAIN_READONLY}:{scope}")
    return f"{_READONLY_SCHEME}:{scope}:{signature}"


def verify_readonly_token(token: str, password: str) -> bool:
    """Verifiziert ein Lese-Token in konstanter Zeit."""
    clean_pw = password.strip()
    if not token or not clean_pw or not token.startswith(f"{_READONLY_SCHEME}:"):
        return False

    parts = token.split(":", 2)
    if len(parts) != 3:
        return False

    _, scope, provided_sig = parts
    expected_sig = _compute_hmac_hex(clean_pw, f"{_HMAC_DOMAIN_READONLY}:{scope}")
    return hmac.compare_digest(provided_sig, expected_sig)


def resolve_credential_role(credential: str, expected_password: str) -> AuthRole:
    """Ermittelt die Rolle anhand eines Passworts oder signierten Tokens."""
    clean_pw = expected_password.strip()
    if not clean_pw:
        return AuthRole.ADMIN

    candidate = credential.strip()
    if not candidate:
        return AuthRole.GUEST

    if hmac.compare_digest(candidate, clean_pw):
        return AuthRole.ADMIN
    if verify_admin_token(candidate, clean_pw):
        return AuthRole.ADMIN
    if verify_readonly_token(candidate, clean_pw):
        return AuthRole.READONLY
    return AuthRole.GUEST


class LoginRateLimiter:
    """Thread-sicherer Brute-Force-Schutz für Passwort-Anmeldungen."""

    def __init__(
        self,
        max_attempts: int = MAX_FAILED_LOGIN_ATTEMPTS,
        lockout_seconds: int = LOGIN_LOCKOUT_SECONDS,
    ) -> None:
        self._max_attempts = max_attempts
        self._lockout_seconds = lockout_seconds
        self._lock = threading.Lock()
        self._failed_timestamps: dict[str, list[float]] = {}

    def is_locked_out(self, client_key: str = "global") -> tuple[bool, int]:
        """Prüft, ob eine Anmeldung gesperrt ist und gibt die Restsekunden zurück."""
        now = time.monotonic()
        with self._lock:
            recent = self._prune_expired(client_key, now)
            if len(recent) < self._max_attempts:
                return False, 0
            remaining = int(self._lockout_seconds - (now - recent[0])) + 1
            return (remaining > 0), max(0, remaining)

    def record_failure(self, client_key: str = "global") -> None:
        """Zeichnet einen fehlgeschlagenen Anmeldeversuch auf."""
        now = time.monotonic()
        with self._lock:
            recent = self._prune_expired(client_key, now)
            recent.append(now)
            self._failed_timestamps[client_key] = recent

    def reset(self, client_key: str = "global") -> None:
        """Setzt den Fehlversuchszähler nach erfolgreicher Anmeldung zurück."""
        with self._lock:
            self._failed_timestamps.pop(client_key, None)

    def _prune_expired(self, client_key: str, now: float) -> list[float]:
        cutoff = now - self._lockout_seconds
        entries = [ts for ts in self._failed_timestamps.get(client_key, []) if ts >= cutoff]
        self._failed_timestamps[client_key] = entries
        return entries
