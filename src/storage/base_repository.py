"""Abstrakte Repository-Schnittstelle und Checkpoint-Struktur (Dependency Inversion)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime

from src.config.constants import (
    DEFAULT_JOBS_LIST_LIMIT,
    DEFAULT_REPORTS_LIST_LIMIT,
    DEFAULT_STALE_JOB_MINUTES,
)
from src.config.domain_loader import load_default_domain_config
from src.domain.domain_config import DomainConfig
from src.domain.enums import JobPhase, JobStatus
from src.domain.models import (
    ResearchJob,
    ResearchPlan,
    ResearchReport,
    ResearchRequest,
    SubTopicFinding,
)


def utc_now_iso() -> str:
    """Gibt den aktuellen UTC-Zeitstempel im ISO-8601-Format zurück."""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class JobCheckpoint:
    """Gespeicherter Zwischenstand für das unterbrechungsfreie Fortsetzen langer Läufe (>1h)."""

    plan: ResearchPlan | None
    baseline_markdown: str
    findings: list[SubTopicFinding]


class ResearchRepository(ABC):
    """Abstrakte Schnittstelle für alle Persistenzoperationen."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Name des aktiven Datenbank-Backends."""

    @abstractmethod
    def initialize_schema(self) -> None:
        """Legt alle benötigten Tabellen und Indizes idempotent an."""

    @abstractmethod
    def create_job(self, request: ResearchRequest) -> ResearchJob:
        """Erstellt einen neuen Recherche-Auftrag im Status `queued`."""

    @abstractmethod
    def get_job(self, job_id: str) -> ResearchJob | None:
        """Lädt einen einzelnen Auftrag anhand seiner ID."""

    @abstractmethod
    def list_jobs(
        self,
        statuses: tuple[JobStatus, ...] | None = None,
        limit: int = DEFAULT_JOBS_LIST_LIMIT,
    ) -> list[ResearchJob]:
        """Listet Aufträge absteigend nach Erstellungszeitpunkt auf."""

    def count_jobs(self, statuses: tuple[JobStatus, ...] | None = None) -> int:
        """Zählt Aufträge exakt ohne Limit-Verzerrung (z. B. für Tab-Badges)."""
        return len(self.list_jobs(statuses=statuses, limit=200))

    @abstractmethod
    def find_claimable_job_ids(
        self,
        stale_timeout_minutes: int = DEFAULT_STALE_JOB_MINUTES,
        limit: int = 5,
    ) -> list[str]:
        """Findet wartende (`queued`) oder verwaiste (`running` ohne Heartbeat) Aufträge."""

    @abstractmethod
    def claim_job(
        self,
        job_id: str,
        worker_id: str,
        stale_timeout_minutes: int = DEFAULT_STALE_JOB_MINUTES,
    ) -> bool:
        """Beansprucht einen Auftrag atomar für einen Worker (verhindert Doppelausführung)."""

    @abstractmethod
    def update_progress(
        self,
        job_id: str,
        phase: JobPhase,
        progress_percent: int,
        message: str,
        live_status_override: str | None = None,
    ) -> None:
        """Aktualisiert Phase, Fortschritt, Heartbeat und Aktivitätsprotokoll eines Jobs."""

    def update_live_action(
        self,
        job_id: str,
        action_message: str,
        progress_percent: int | None = None,
    ) -> None:
        """Aktualisiert die Live-Aktionsanzeige hinter der Prozentzahl ohne Log-Duplikat."""
        _ = (job_id, action_message, progress_percent)

    @abstractmethod
    def touch_heartbeat(self, job_id: str) -> None:
        """Aktualisiert den Lebenszeichen-Zeitstempel während langer Operationen."""

    @abstractmethod
    def load_checkpoint(self, job_id: str) -> JobCheckpoint:
        """Lädt vorhandene Zwischenergebnisse (Plan, Baseline, Teilthemen) eines Jobs."""

    @abstractmethod
    def save_plan_checkpoint(self, job_id: str, plan: ResearchPlan) -> None:
        """Speichert den generierten Recherche-Plan persistent als Checkpoint."""

    @abstractmethod
    def save_baseline_checkpoint(self, job_id: str, baseline_markdown: str) -> None:
        """Speichert das Ergebnis des Deep-Research-Baseline-Agenten als Checkpoint."""

    @abstractmethod
    def save_findings_checkpoint(self, job_id: str, findings: list[SubTopicFinding]) -> None:
        """Speichert die bisherigen Teilthemen-Analysen persistent als Checkpoint."""

    @abstractmethod
    def request_cancellation(self, job_id: str) -> bool:
        """Bricht einen wartenden oder laufenden Auftrag sofort ab (`cancelled`)."""

    def cleanup_stuck_cancellations(self) -> int:
        """Bereinigt veraltete Jobs im Zwischenzustand `running` + `cancel_requested = 1`."""
        return 0

    @abstractmethod
    def requeue_job_from_checkpoint(self, job_id: str) -> bool:
        """Setzt einen fehlgeschlagenen oder abgebrochenen Auftrag unter Erhalt aller Checkpoints auf `queued` zurück."""

    @abstractmethod
    def is_cancellation_requested(self, job_id: str) -> bool:
        """Prüft leichtgewichtig, ob ein Abbruch für diesen Auftrag angefordert wurde."""

    @abstractmethod
    def mark_job_completed(
        self,
        job_id: str,
        report: ResearchReport,
        status_note: str | None = None,
    ) -> None:
        """Speichert den fertigen Bericht und markiert den Job atomar als `completed`."""

    @abstractmethod
    def mark_job_email_sent(self, job_id: str) -> None:
        """Vermerkt den erfolgreichen Versand der E-Mail-Benachrichtigung."""

    @abstractmethod
    def mark_job_failed(self, job_id: str, error_message: str) -> None:
        """Setzt den Job-Status bei nicht behebbaren Fehlern auf `failed`."""

    @abstractmethod
    def mark_job_cancelled(self, job_id: str) -> None:
        """Bestätigt den vollständigen Abbruch eines Auftrags."""

    @abstractmethod
    def get_report(self, job_id: str) -> ResearchReport | None:
        """Lädt den fertiggestellten Recherche-Bericht zu einer Job-ID."""

    @abstractmethod
    def list_reports(
        self,
        search_query: str = "",
        limit: int = DEFAULT_REPORTS_LIST_LIMIT,
    ) -> list[ResearchReport]:
        """Listet fertige Berichte optional gefiltert nach Suchbegriff auf."""

    @abstractmethod
    def delete_job_and_report(self, job_id: str) -> bool:
        """Löscht einen Auftrag samt zugehörigem Bericht endgültig."""

    def delete_jobs_by_status(
        self,
        statuses: tuple[JobStatus, ...] = (JobStatus.CANCELLED, JobStatus.FAILED),
    ) -> int:
        """Entfernt alle Aufträge der angegebenen Endstatus (z. B. fehlgeschlagene/abgebrochene Läufe)."""
        deleted = 0
        for job in self.list_jobs(statuses=statuses, limit=200):
            if self.delete_job_and_report(job.id):
                deleted += 1
        return deleted

    def get_domain_config(self) -> DomainConfig:
        """Lädt die aktive Fach- und KI-Konfiguration (inkl. eventueller Web-App-Anpassungen)."""
        return load_default_domain_config()

    def has_custom_domain_config(self) -> bool:
        """Gibt zurück, ob vom Standard abweichende Fachlogik-Einstellungen gespeichert sind."""
        return False

    def save_domain_config(self, config: DomainConfig) -> DomainConfig:
        """Speichert angepasste Fachlogik-Einstellungen persistent."""
        return config

    def reset_domain_config(self) -> DomainConfig:
        """Setzt die Fachlogik auf die Standardwerte aus `config/settings.yaml` zurück."""
        return load_default_domain_config()

    @abstractmethod
    def close(self) -> None:
        """Schließt darunterliegende Datenbankverbindungen."""
