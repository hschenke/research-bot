"""SQL-Injection-sichere Repository-Implementierung für SQLite und Turso."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from src.config.constants import (
    DEFAULT_JOBS_LIST_LIMIT,
    DEFAULT_REPORTS_LIST_LIMIT,
    DEFAULT_STALE_JOB_MINUTES,
    MAX_PROGRESS_LOG_ENTRIES,
)
from src.domain.enums import JobPhase, JobStatus
from src.domain.models import (
    ProgressLogEntry,
    ResearchJob,
    ResearchPlan,
    ResearchReport,
    ResearchRequest,
    SubTopicFinding,
)
from src.storage.base_repository import JobCheckpoint, ResearchRepository, utc_now_iso
from src.storage.executor import SqlExecutor
from src.storage.report_store import CheckpointAndReportStore
from src.storage.row_mapper import map_row_to_job
from src.storage.schema import SCHEMA_STATEMENTS


def _stale_cutoff_iso(stale_timeout_minutes: int) -> str:
    """Berechnet den ISO-Grenzzeitpunkt für verwaiste Worker-Heartbeats."""
    minutes = max(5, stale_timeout_minutes)
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


class SqlResearchRepository(ResearchRepository):
    """Gemeinsame, parametrisierte SQL-Implementierung ohne duplizierte Queries."""

    def __init__(self, executor: SqlExecutor) -> None:
        self._executor = executor
        self._report_store = CheckpointAndReportStore(executor)

    @property
    def backend_name(self) -> str:
        return self._executor.backend_name

    def initialize_schema(self) -> None:
        self._executor.execute_batch([(stmt, ()) for stmt in SCHEMA_STATEMENTS])

    def create_job(self, request: ResearchRequest) -> ResearchJob:
        job_id = uuid.uuid4().hex[:12]
        now_iso = utc_now_iso()
        initial_log = [
            ProgressLogEntry(
                timestamp_iso=now_iso,
                phase=JobPhase.QUEUED,
                message="Auftrag angelegt und in Warteschlange eingereiht.",
            ).model_dump()
        ]
        self._executor.execute(
            """
            INSERT INTO research_jobs (
                id, topic, focus_instructions, depth, language, notify_email,
                status, phase, progress_percent, status_message, progress_log_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                request.topic,
                request.focus_instructions,
                request.depth.value,
                request.language,
                request.notify_email,
                JobStatus.QUEUED.value,
                JobPhase.QUEUED.value,
                0,
                "Wartet auf Hintergrund-Worker...",
                json.dumps(initial_log, ensure_ascii=False),
                now_iso,
            ),
        )
        created = self.get_job(job_id)
        if created is None:
            raise RuntimeError(f"Neu angelegter Job '{job_id}' konnte nicht gelesen werden.")
        return created

    def get_job(self, job_id: str) -> ResearchJob | None:
        res = self._executor.execute(
            "SELECT * FROM research_jobs WHERE id = ? LIMIT 1",
            (job_id.strip(),),
        )
        return map_row_to_job(res.rows[0]) if res.rows else None

    def list_jobs(
        self,
        statuses: tuple[JobStatus, ...] | None = None,
        limit: int = DEFAULT_JOBS_LIST_LIMIT,
    ) -> list[ResearchJob]:
        safe_limit = max(1, min(limit, 200))
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            sql = (
                f"SELECT * FROM research_jobs WHERE status IN ({placeholders}) "  # noqa: S608
                "ORDER BY created_at DESC LIMIT ?"
            )
            params: tuple[str | int, ...] = tuple(s.value for s in statuses) + (safe_limit,)
        else:
            sql = "SELECT * FROM research_jobs ORDER BY created_at DESC LIMIT ?"
            params = (safe_limit,)

        res = self._executor.execute(sql, params)
        return [map_row_to_job(row) for row in res.rows]

    def find_claimable_job_ids(
        self,
        stale_timeout_minutes: int = DEFAULT_STALE_JOB_MINUTES,
        limit: int = 5,
    ) -> list[str]:
        cutoff = _stale_cutoff_iso(stale_timeout_minutes)
        res = self._executor.execute(
            """
            SELECT id FROM research_jobs
            WHERE cancel_requested = 0
              AND (
                  status = 'queued'
                  OR (status = 'running' AND (heartbeat_at IS NULL OR heartbeat_at < ?))
              )
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (cutoff, max(1, limit)),
        )
        return [str(row["id"]) for row in res.rows]

    def claim_job(
        self,
        job_id: str,
        worker_id: str,
        stale_timeout_minutes: int = DEFAULT_STALE_JOB_MINUTES,
    ) -> bool:
        now_iso = utc_now_iso()
        cutoff = _stale_cutoff_iso(stale_timeout_minutes)
        res = self._executor.execute(
            """
            UPDATE research_jobs
            SET status = 'running',
                worker_id = ?,
                started_at = COALESCE(started_at, ?),
                heartbeat_at = ?
            WHERE id = ?
              AND cancel_requested = 0
              AND (
                  status = 'queued'
                  OR (status = 'running' AND (heartbeat_at IS NULL OR heartbeat_at < ?))
              )
            """,
            (worker_id, now_iso, now_iso, job_id.strip(), cutoff),
        )
        return res.affected_rows == 1

    def update_progress(
        self,
        job_id: str,
        phase: JobPhase,
        progress_percent: int,
        message: str,
    ) -> None:
        now_iso = utc_now_iso()
        existing = self.get_job(job_id)
        entries = [entry.model_dump() for entry in (existing.progress_log if existing else [])]
        entries.append(
            ProgressLogEntry(timestamp_iso=now_iso, phase=phase, message=message).model_dump()
        )
        self._executor.execute(
            """
            UPDATE research_jobs
            SET phase = ?,
                progress_percent = ?,
                status_message = ?,
                progress_log_json = ?,
                heartbeat_at = ?
            WHERE id = ?
            """,
            (
                phase.value,
                max(0, min(100, progress_percent)),
                message,
                json.dumps(entries[-MAX_PROGRESS_LOG_ENTRIES:], ensure_ascii=False),
                now_iso,
                job_id.strip(),
            ),
        )

    def touch_heartbeat(self, job_id: str) -> None:
        self._executor.execute(
            "UPDATE research_jobs SET heartbeat_at = ? WHERE id = ?",
            (utc_now_iso(), job_id.strip()),
        )

    def load_checkpoint(self, job_id: str) -> JobCheckpoint:
        return self._report_store.load_checkpoint(job_id)

    def save_plan_checkpoint(self, job_id: str, plan: ResearchPlan) -> None:
        self._report_store.save_plan_checkpoint(job_id, plan)

    def save_baseline_checkpoint(self, job_id: str, baseline_markdown: str) -> None:
        self._report_store.save_baseline_checkpoint(job_id, baseline_markdown)

    def save_findings_checkpoint(self, job_id: str, findings: list[SubTopicFinding]) -> None:
        self._report_store.save_findings_checkpoint(job_id, findings)

    def request_cancellation(self, job_id: str) -> bool:
        clean_id = job_id.strip()
        queued_res = self._executor.execute(
            """
            UPDATE research_jobs
            SET status = 'cancelled',
                phase = 'cancelled',
                cancel_requested = 1,
                status_message = 'Vor Start durch Nutzer abgebrochen.',
                completed_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (utc_now_iso(), clean_id),
        )
        if queued_res.affected_rows > 0:
            return True

        running_res = self._executor.execute(
            """
            UPDATE research_jobs
            SET cancel_requested = 1,
                status_message = 'Abbruch angefordert – stoppe nach aktuellem Schritt...'
            WHERE id = ? AND status = 'running'
            """,
            (clean_id,),
        )
        return running_res.affected_rows > 0

    def requeue_job_from_checkpoint(self, job_id: str) -> bool:
        res = self._executor.execute(
            """
            UPDATE research_jobs
            SET status = 'queued',
                error_message = '',
                cancel_requested = 0,
                completed_at = NULL,
                status_message = 'Wiederanlauf vom letzten gespeicherten Checkpoint...'
            WHERE id = ? AND status IN ('failed', 'cancelled')
            """,
            (job_id.strip(),),
        )
        return res.affected_rows > 0

    def is_cancellation_requested(self, job_id: str) -> bool:
        res = self._executor.execute(
            "SELECT cancel_requested, status FROM research_jobs WHERE id = ? LIMIT 1",
            (job_id.strip(),),
        )
        if not res.rows:
            return True
        row = res.rows[0]
        return bool(int(row.get("cancel_requested") or 0)) or row.get("status") == "cancelled"

    def mark_job_completed(self, job_id: str, report: ResearchReport) -> None:
        self._report_store.persist_completed_report(job_id, report)

    def mark_job_email_sent(self, job_id: str) -> None:
        self._executor.execute(
            "UPDATE research_jobs SET email_sent = 1 WHERE id = ?",
            (job_id.strip(),),
        )

    def mark_job_failed(self, job_id: str, error_message: str) -> None:
        now_iso = utc_now_iso()
        self._executor.execute(
            """
            UPDATE research_jobs
            SET status = 'failed',
                phase = 'failed',
                status_message = 'Auftrag mit Fehler abgebrochen.',
                error_message = ?,
                completed_at = ?,
                heartbeat_at = ?
            WHERE id = ?
            """,
            (error_message.strip()[:500], now_iso, now_iso, job_id.strip()),
        )

    def mark_job_cancelled(self, job_id: str) -> None:
        now_iso = utc_now_iso()
        self._executor.execute(
            """
            UPDATE research_jobs
            SET status = 'cancelled',
                phase = 'cancelled',
                status_message = 'Auftrag durch Nutzer abgebrochen.',
                completed_at = ?,
                heartbeat_at = ?
            WHERE id = ?
            """,
            (now_iso, now_iso, job_id.strip()),
        )

    def get_report(self, job_id: str) -> ResearchReport | None:
        return self._report_store.get_report(job_id)

    def list_reports(
        self,
        search_query: str = "",
        limit: int = DEFAULT_REPORTS_LIST_LIMIT,
    ) -> list[ResearchReport]:
        return self._report_store.list_reports(search_query=search_query, limit=limit)

    def delete_job_and_report(self, job_id: str) -> bool:
        return self._report_store.delete_job_and_report(job_id)

    def close(self) -> None:
        self._executor.close()
