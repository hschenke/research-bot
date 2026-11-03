"""SQL-Zustandsübergänge für Abbruch, Fehlerbehandlung, Wiederanlauf und Bereinigung."""

from __future__ import annotations

import json

from src.config.constants import MAX_PROGRESS_LOG_ENTRIES
from src.domain.enums import JobPhase, JobStatus
from src.domain.models import ProgressLogEntry, ResearchJob
from src.storage.base_repository import utc_now_iso
from src.storage.executor import SqlExecutor


class JobLifecycleStore:
    """Kapselt atomare Statuswechsel, Sofort-Abbruch und Bereinigung von Recherche-Jobs."""

    def __init__(self, executor: SqlExecutor) -> None:
        self._executor = executor

    def append_progress_update(
        self,
        existing: ResearchJob | None,
        job_id: str,
        phase: JobPhase,
        progress_percent: int,
        message: str,
        live_status_override: str | None = None,
    ) -> None:
        if existing is None or existing.cancel_requested or existing.status == JobStatus.CANCELLED:
            return
        now_iso = utc_now_iso()
        entries = [entry.model_dump() for entry in existing.progress_log]
        entries.append(
            ProgressLogEntry(timestamp_iso=now_iso, phase=phase, message=message).model_dump()
        )
        live_text = (live_status_override or message).strip()[:240]
        self._executor.execute(
            """
            UPDATE research_jobs
            SET phase = ?,
                progress_percent = ?,
                status_message = ?,
                progress_log_json = ?,
                heartbeat_at = ?
            WHERE id = ?
              AND cancel_requested = 0
              AND status NOT IN ('cancelled', 'completed')
            """,
            (
                phase.value,
                max(0, min(100, progress_percent)),
                live_text,
                json.dumps(entries[-MAX_PROGRESS_LOG_ENTRIES:], ensure_ascii=False),
                now_iso,
                job_id.strip(),
            ),
        )

    def update_live_action(
        self,
        job_id: str,
        action_message: str,
        progress_percent: int | None = None,
    ) -> None:
        """Schreibt die gerade laufende Detail-Aktion direkt in `status_message` ohne Log-Spam."""
        cleaned = action_message.strip()[:240]
        if not cleaned:
            return
        now_iso = utc_now_iso()
        if progress_percent is None:
            self._executor.execute(
                """
                UPDATE research_jobs
                SET status_message = ?, heartbeat_at = ?
                WHERE id = ? AND cancel_requested = 0 AND status = 'running'
                """,
                (cleaned, now_iso, job_id.strip()),
            )
            return
        self._executor.execute(
            """
            UPDATE research_jobs
            SET status_message = ?, progress_percent = ?, heartbeat_at = ?
            WHERE id = ? AND cancel_requested = 0 AND status = 'running'
            """,
            (cleaned, max(0, min(100, progress_percent)), now_iso, job_id.strip()),
        )

    def cancel_immediately(self, existing: ResearchJob | None, job_id: str) -> bool:
        """Setzt wartende und laufende Aufträge ohne Verzögerung direkt auf `cancelled`."""
        clean_id = job_id.strip()
        now_iso = utc_now_iso()
        entries = [entry.model_dump() for entry in (existing.progress_log if existing else [])]
        entries.append(
            ProgressLogEntry(
                timestamp_iso=now_iso,
                phase=JobPhase.CANCELLED,
                message="Auftrag durch Nutzer sofort abgebrochen.",
            ).model_dump()
        )
        res = self._executor.execute(
            """
            UPDATE research_jobs
            SET status = 'cancelled',
                phase = 'cancelled',
                cancel_requested = 1,
                status_message = 'Auftrag durch Nutzer abgebrochen.',
                progress_log_json = ?,
                completed_at = ?,
                heartbeat_at = ?
            WHERE id = ? AND status IN ('queued', 'running')
            """,
            (
                json.dumps(entries[-MAX_PROGRESS_LOG_ENTRIES:], ensure_ascii=False),
                now_iso,
                now_iso,
                clean_id,
            ),
        )
        return res.affected_rows > 0

    def cleanup_stuck_cancellations(self) -> int:
        """Überführt verwaiste Alt-Aufträge (`running` + `cancel_requested = 1`) sofort in `cancelled`."""
        now_iso = utc_now_iso()
        res = self._executor.execute(
            """
            UPDATE research_jobs
            SET status = 'cancelled',
                phase = 'cancelled',
                status_message = 'Auftrag abgebrochen.',
                completed_at = COALESCE(completed_at, ?),
                heartbeat_at = ?
            WHERE status = 'running' AND cancel_requested = 1
            """,
            (now_iso, now_iso),
        )
        return res.affected_rows

    def requeue_from_checkpoint(self, job_id: str) -> bool:
        res = self._executor.execute(
            """
            UPDATE research_jobs
            SET status = 'queued',
                phase = 'queued',
                error_message = '',
                cancel_requested = 0,
                worker_id = '',
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

    def mark_failed(self, job_id: str, error_message: str) -> None:
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
            WHERE id = ? AND cancel_requested = 0 AND status != 'cancelled'
            """,
            (error_message.strip()[:500], now_iso, now_iso, job_id.strip()),
        )

    def mark_cancelled(self, job_id: str) -> None:
        now_iso = utc_now_iso()
        self._executor.execute(
            """
            UPDATE research_jobs
            SET status = 'cancelled',
                phase = 'cancelled',
                cancel_requested = 1,
                status_message = 'Auftrag durch Nutzer abgebrochen.',
                completed_at = COALESCE(completed_at, ?),
                heartbeat_at = ?
            WHERE id = ?
            """,
            (now_iso, now_iso, job_id.strip()),
        )

    def delete_by_statuses(self, statuses: tuple[JobStatus, ...]) -> int:
        if not statuses:
            return 0
        placeholders = ", ".join("?" for _ in statuses)
        params: tuple[str, ...] = tuple(s.value for s in statuses)
        results = self._executor.execute_batch(
            [
                (
                    f"DELETE FROM research_reports WHERE job_id IN (SELECT id FROM research_jobs WHERE status IN ({placeholders}))",  # noqa: S608
                    params,
                ),
                (
                    f"DELETE FROM research_jobs WHERE status IN ({placeholders})",  # noqa: S608
                    params,
                ),
            ]
        )
        return results[-1].affected_rows if results else 0
