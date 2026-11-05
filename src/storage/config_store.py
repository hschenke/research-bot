"""Persistenz-Store für editierbare Fachlogik- & KI-Konfigurationen (SQLite & Turso)."""

from __future__ import annotations

import json
import logging

from src.config.domain_loader import (
    compute_config_overrides,
    load_default_domain_config,
    merge_domain_config,
)
from src.domain.domain_config import DomainConfig
from src.security.config_validator import validate_domain_config_payload
from src.storage.base_repository import utc_now_iso
from src.storage.executor import SqlExecutor

logger = logging.getLogger(__name__)

_ACTIVE_CONFIG_KEY = "active_domain_overrides"


class DomainConfigStore:
    """Speichert und lädt Web-App-Anpassungen der Fachlogik parametrisiert als JSON-Diff."""

    def __init__(self, executor: SqlExecutor) -> None:
        self._executor = executor

    def load_domain_config(self) -> DomainConfig:
        """Lädt die aktive Fachlogik (Datei-Standardwerte + gespeicherte DB-Anpassungen)."""
        default_cfg = load_default_domain_config()
        try:
            res = self._executor.execute(
                "SELECT config_json FROM app_domain_config WHERE config_key = ? LIMIT 1",
                (_ACTIVE_CONFIG_KEY,),
            )
        except Exception as exc:
            logger.debug("app_domain_config noch nicht initialisiert (%s) – nutze Defaults.", exc)
            return default_cfg

        if not res.rows:
            return default_cfg

        raw_json = str(res.rows[0].get("config_json") or "")
        if not raw_json.strip():
            return default_cfg

        try:
            overrides = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.warning("Ungültiges JSON in app_domain_config ignoriert: %s", exc)
            return default_cfg

        if not isinstance(overrides, dict):
            return default_cfg
        return merge_domain_config(default_cfg, overrides)

    def has_custom_overrides(self) -> bool:
        """Prüft, ob aktuell benutzerdefinierte Anpassungen in der Datenbank aktiv sind."""
        try:
            res = self._executor.execute(
                "SELECT config_json FROM app_domain_config WHERE config_key = ? LIMIT 1",
                (_ACTIVE_CONFIG_KEY,),
            )
            if not res.rows:
                return False
            parsed = json.loads(str(res.rows[0].get("config_json") or "{}"))
            return isinstance(parsed, dict) and bool(parsed)
        except Exception:
            return False

    def save_domain_config(self, config: DomainConfig) -> DomainConfig:
        """Validiert und speichert Anpassungen gegenüber `settings.yaml` persistent in der DB."""
        validated = validate_domain_config_payload(config.model_dump(mode="json"))
        default_cfg = load_default_domain_config()
        overrides = compute_config_overrides(default_cfg, validated)
        if not overrides:
            self.reset_domain_config()
            return default_cfg

        serialized = json.dumps(overrides, ensure_ascii=False)
        self._executor.execute(
            """
            INSERT INTO app_domain_config (config_key, config_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(config_key) DO UPDATE SET
                config_json = excluded.config_json,
                updated_at = excluded.updated_at
            """,
            (_ACTIVE_CONFIG_KEY, serialized, utc_now_iso()),
        )
        return validated

    def reset_domain_config(self) -> DomainConfig:
        """Entfernt alle DB-Überschreibungen und kehrt zu `config/settings.yaml` zurück."""
        self._executor.execute(
            "DELETE FROM app_domain_config WHERE config_key = ?",
            (_ACTIVE_CONFIG_KEY,),
        )
        return load_default_domain_config()
