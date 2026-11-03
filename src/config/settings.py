"""Sicheres Laden der Anwendungskonfiguration aus ENV, Streamlit Secrets und YAML."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from src.config.constants import (
    DEFAULT_DEEP_MODEL,
    DEFAULT_FAST_MODEL,
    DEFAULT_MAX_CONCURRENT_LOCAL_JOBS,
    DEFAULT_SQLITE_REL_PATH,
    DEFAULT_STALE_JOB_MINUTES,
)
from src.domain.enums import EmailProvider, ResearchDepth, WorkerMode

load_dotenv()
logger = logging.getLogger(__name__)

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent


def _read_streamlit_secret(key: str) -> str | None:
    """Liest optional einen Schlüssel aus `st.secrets`, falls innerhalb von Streamlit aktiv."""
    if "STREAMLIT_SERVER_PORT" not in os.environ and "streamlit" not in sys.modules:
        return None
    try:
        import streamlit as st

        if hasattr(st, "secrets") and key in st.secrets:
            value = st.secrets[key]
            return str(value).strip() if value is not None else None
    except Exception as exc:
        logger.debug("Streamlit Secrets nicht verfügbar für '%s': %s", key, exc)
    return None


def get_secret_or_env(key: str, default: str = "") -> str:
    """Ermittelt einen Konfigurationswert priorisiert aus ENV, danach `st.secrets`."""
    env_val = os.getenv(key)
    if env_val is not None and env_val.strip():
        return env_val.strip()
    secret_val = _read_streamlit_secret(key)
    if secret_val:
        return secret_val
    return default


def _load_yaml_defaults(yaml_path: Path | None = None) -> dict[str, Any]:
    """Lädt nicht-sensible Standardeinstellungen aus `config/settings.yaml`."""
    target = yaml_path or (PROJECT_ROOT / "config" / "settings.yaml")
    if not target.is_file():
        return {}
    try:
        content = target.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Konnte settings.yaml nicht laden (%s): %s", target, exc)
        return {}


def _parse_enum(enum_cls: Any, raw_value: str, fallback: Any) -> Any:
    """Konvertiert einen String fehlertolerant in einen Enum-Wert."""
    cleaned = raw_value.strip().lower()
    for member in enum_cls:
        if member.value == cleaned:
            return member
    return fallback


@dataclass(frozen=True)
class AppSettings:
    """
    Zentrales, unveränderliches Konfigurationsobjekt.
    Alle sensiblen Felder nutzen `repr=False`, damit niemals Secrets in Logs landen.
    """

    gemini_api_key: str = field(default="", repr=False)
    gemini_fast_model: str = DEFAULT_FAST_MODEL
    gemini_deep_model: str = DEFAULT_DEEP_MODEL
    enable_deep_research_agent: bool = True

    turso_url: str = ""
    turso_auth_token: str = field(default="", repr=False)
    sqlite_path: str = DEFAULT_SQLITE_REL_PATH

    worker_mode: WorkerMode = WorkerMode.AUTO
    github_token: str = field(default="", repr=False)
    github_repo: str = ""
    github_branch: str = "main"
    github_workflow_file: str = "run_research.yaml"
    max_concurrent_local_jobs: int = DEFAULT_MAX_CONCURRENT_LOCAL_JOBS
    stale_job_timeout_minutes: int = DEFAULT_STALE_JOB_MINUTES

    email_provider: EmailProvider = EmailProvider.RESEND
    resend_api_key: str = field(default="", repr=False)
    email_to: str = ""
    email_from: str = "Research Bot <onboarding@resend.dev>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = field(default="", repr=False)

    app_password: str = field(default="", repr=False)
    streamlit_app_url: str = ""
    default_language: str = "de"
    default_depth: ResearchDepth = ResearchDepth.STANDARD

    @property
    def has_turso_configured(self) -> bool:
        """Prüft, ob gültige Turso-Zugangsdaten gesetzt sind."""
        return bool(
            self.turso_url
            and self.turso_auth_token
            and not self.turso_auth_token.startswith("your_")
            and "deine-datenbank" not in self.turso_url
        )

    @property
    def has_gemini_configured(self) -> bool:
        """Prüft, ob ein echter Gemini API Key hinterlegt ist."""
        return bool(self.gemini_api_key and not self.gemini_api_key.startswith("your_"))

    @property
    def has_github_worker_configured(self) -> bool:
        """Prüft, ob GitHub Actions Dispatching verfügbar ist."""
        return bool(
            self.github_token
            and not self.github_token.startswith("your_")
            and "/" in self.github_repo
        )

    @property
    def has_email_configured(self) -> bool:
        """Prüft, ob ein E-Mail-Versandweg vollständig konfiguriert ist."""
        if self.email_provider == EmailProvider.RESEND:
            return bool(self.resend_api_key and not self.resend_api_key.startswith("your_"))
        if self.email_provider == EmailProvider.SMTP:
            return bool(self.smtp_host and self.smtp_user and self.smtp_password)
        return False


def load_app_settings(yaml_path: Path | None = None) -> AppSettings:
    """Erstellt eine validierte `AppSettings`-Instanz aus Umgebungsvariablen und Defaults."""
    yaml_cfg = _load_yaml_defaults(yaml_path)

    turso_url = get_secret_or_env("TURSO_URL") or get_secret_or_env("TURSO_DATABASE_URL")
    turso_key = get_secret_or_env("TURSO_KEY") or get_secret_or_env("TURSO_AUTH_TOKEN")

    raw_smtp_port = get_secret_or_env("SMTP_PORT", "587")
    smtp_port = int(raw_smtp_port) if raw_smtp_port.isdigit() else 587

    raw_enable_agent = get_secret_or_env(
        "ENABLE_DEEP_RESEARCH_AGENT",
        str(yaml_cfg.get("enable_deep_research_agent", True)),
    )
    enable_agent = raw_enable_agent.lower() in {"1", "true", "yes", "on"}

    return AppSettings(
        gemini_api_key=get_secret_or_env("GEMINI_API_KEY"),
        gemini_fast_model=get_secret_or_env("GEMINI_MODEL", DEFAULT_FAST_MODEL),
        gemini_deep_model=get_secret_or_env("GEMINI_DEEP_MODEL", DEFAULT_DEEP_MODEL),
        enable_deep_research_agent=enable_agent,
        turso_url=turso_url,
        turso_auth_token=turso_key,
        sqlite_path=get_secret_or_env("SQLITE_PATH", DEFAULT_SQLITE_REL_PATH),
        worker_mode=_parse_enum(
            WorkerMode,
            get_secret_or_env("WORKER_MODE", "auto"),
            WorkerMode.AUTO,
        ),
        github_token=get_secret_or_env("GITHUB_TOKEN"),
        github_repo=get_secret_or_env("GITHUB_REPO"),
        github_branch=get_secret_or_env("GITHUB_BRANCH", "main"),
        github_workflow_file=get_secret_or_env("GITHUB_WORKFLOW_FILE", "run_research.yaml"),
        max_concurrent_local_jobs=int(
            yaml_cfg.get("max_concurrent_local_jobs", DEFAULT_MAX_CONCURRENT_LOCAL_JOBS)
        ),
        stale_job_timeout_minutes=int(
            yaml_cfg.get("stale_job_timeout_minutes", DEFAULT_STALE_JOB_MINUTES)
        ),
        email_provider=_parse_enum(
            EmailProvider,
            get_secret_or_env("EMAIL_PROVIDER", "resend"),
            EmailProvider.RESEND,
        ),
        resend_api_key=get_secret_or_env("RESEND_API_KEY"),
        email_to=get_secret_or_env("EMAIL_TO"),
        email_from=get_secret_or_env("EMAIL_FROM", "Research Bot <onboarding@resend.dev>"),
        smtp_host=get_secret_or_env("SMTP_HOST"),
        smtp_port=smtp_port,
        smtp_user=get_secret_or_env("SMTP_USER"),
        smtp_password=get_secret_or_env("SMTP_PASS"),
        app_password=get_secret_or_env("APP_PASSWORD"),
        streamlit_app_url=get_secret_or_env("STREAMLIT_APP_URL").rstrip("/"),
        default_language=str(yaml_cfg.get("default_language", "de")),
        default_depth=_parse_enum(
            ResearchDepth,
            str(yaml_cfg.get("default_depth", "standard")),
            ResearchDepth.STANDARD,
        ),
    )
