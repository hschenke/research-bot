"""SQL-Operationen für Checkpoints (Plan, Baseline, Teilthemen) und fertige Berichte."""

from __future__ import annotations

import json

from src.config.constants import DEFAULT_REPORTS_LIST_LIMIT
from src.domain.models import (
    ResearchPlan,
    ResearchReport,
    SubTopicFinding,
)
from src.storage.base_repository import JobCheckpoint, utc_now_iso
from src.storage.executor import SqlExecutor
from src.storage.row_mapper import (
    map_row_to_report,
    parse_checkpoint_findings,
    parse_checkpoint_plan,
)


class CheckpointAndReportStore:
    """Kapselt alle parametrisierten SQL-Zugriffe auf Checkpoints und `research_reports`."""

    def __init__(self, executor: SqlExecutor) -> None:
        self._executor = executor

    def load_checkpoint(self, job_id: str) -> JobCheckpoint:
        res = self._executor.execute(
            "SELECT plan_json, baseline_markdown, findings_json FROM research_jobs WHERE id = ? LIMIT 1",
            (job_id.strip(),),
        )
        if not res.rows:
            return JobCheckpoint(plan=None, baseline_markdown="", findings=[])
        row = res.rows[0]
        return JobCheckpoint(
            plan=parse_checkpoint_plan(row.get("plan_json")),
            baseline_markdown=str(row.get("baseline_markdown") or ""),
            findings=parse_checkpoint_findings(row.get("findings_json")),
        )

    def save_plan_checkpoint(self, job_id: str, plan: ResearchPlan) -> None:
        self._executor.execute(
            """
            UPDATE research_jobs
            SET plan_json = ?, heartbeat_at = ?
            WHERE id = ? AND cancel_requested = 0 AND status != 'cancelled'
            """,
            (plan.model_dump_json(), utc_now_iso(), job_id.strip()),
        )

    def save_baseline_checkpoint(self, job_id: str, baseline_markdown: str) -> None:
        self._executor.execute(
            """
            UPDATE research_jobs
            SET baseline_markdown = ?, heartbeat_at = ?
            WHERE id = ? AND cancel_requested = 0 AND status != 'cancelled'
            """,
            (baseline_markdown, utc_now_iso(), job_id.strip()),
        )

    def save_findings_checkpoint(self, job_id: str, findings: list[SubTopicFinding]) -> None:
        serialized = json.dumps([f.model_dump() for f in findings], ensure_ascii=False)
        self._executor.execute(
            """
            UPDATE research_jobs
            SET findings_json = ?, heartbeat_at = ?
            WHERE id = ? AND cancel_requested = 0 AND status != 'cancelled'
            """,
            (serialized, utc_now_iso(), job_id.strip()),
        )

    def _is_job_still_active(self, clean_id: str) -> bool:
        """Stellt sicher, dass ein gelöschter oder abgebrochener Job keinen Bericht mehr anlegt."""
        res = self._executor.execute(
            "SELECT status, cancel_requested FROM research_jobs WHERE id = ? LIMIT 1",
            (clean_id,),
        )
        if not res.rows:
            return False
        row = res.rows[0]
        return not bool(int(row.get("cancel_requested") or 0)) and row.get("status") != "cancelled"

    def persist_completed_report(self, job_id: str, report: ResearchReport) -> None:
        clean_id = job_id.strip()
        if not self._is_job_still_active(clean_id):
            return

        now_iso = utc_now_iso()
        upsert_report_sql = """
            INSERT INTO research_reports (
                job_id, title, topic, depth, language, executive_summary,
                key_takeaways_json, content_markdown, citations_json,
                model_used, duration_seconds, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                title = excluded.title,
                executive_summary = excluded.executive_summary,
                key_takeaways_json = excluded.key_takeaways_json,
                content_markdown = excluded.content_markdown,
                citations_json = excluded.citations_json,
                model_used = excluded.model_used,
                duration_seconds = excluded.duration_seconds
        """
        update_job_sql = """
            UPDATE research_jobs
            SET status = 'completed',
                phase = 'completed',
                progress_percent = 100,
                status_message = 'Recherche erfolgreich abgeschlossen.',
                completed_at = ?,
                heartbeat_at = ?
            WHERE id = ? AND cancel_requested = 0 AND status != 'cancelled'
        """
        self._executor.execute_batch(
            [
                (
                    upsert_report_sql,
                    (
                        report.job_id,
                        report.title,
                        report.topic,
                        report.depth.value,
                        report.language,
                        report.executive_summary,
                        json.dumps(report.key_takeaways, ensure_ascii=False),
                        report.content_markdown,
                        json.dumps([c.model_dump() for c in report.citations], ensure_ascii=False),
                        report.model_used,
                        report.duration_seconds,
                        report.created_at or now_iso,
                    ),
                ),
                (update_job_sql, (now_iso, now_iso, clean_id)),
            ]
        )

    def get_report(self, job_id: str) -> ResearchReport | None:
        res = self._executor.execute(
            "SELECT * FROM research_reports WHERE job_id = ? LIMIT 1",
            (job_id.strip(),),
        )
        return map_row_to_report(res.rows[0]) if res.rows else None

    def list_reports(
        self,
        search_query: str = "",
        limit: int = DEFAULT_REPORTS_LIST_LIMIT,
    ) -> list[ResearchReport]:
        safe_limit = max(1, min(limit, 200))
        cleaned_query = search_query.strip()
        if cleaned_query:
            pattern = f"%{cleaned_query}%"
            sql = """
                SELECT * FROM research_reports
                WHERE title LIKE ? OR topic LIKE ? OR executive_summary LIKE ? OR content_markdown LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
            """
            params: tuple[str | int, ...] = (pattern, pattern, pattern, pattern, safe_limit)
        else:
            sql = "SELECT * FROM research_reports ORDER BY created_at DESC LIMIT ?"
            params = (safe_limit,)

        res = self._executor.execute(sql, params)
        return [map_row_to_report(row) for row in res.rows]

    def delete_job_and_report(self, job_id: str) -> bool:
        clean_id = job_id.strip()
        results = self._executor.execute_batch(
            [
                ("DELETE FROM research_reports WHERE job_id = ?", (clean_id,)),
                ("DELETE FROM research_jobs WHERE id = ?", (clean_id,)),
            ]
        )
        return any(r.affected_rows > 0 for r in results)
