"""Sichere Serialisierung und Konvertierung zwischen DB-Zeilen und Domain-Modellen."""

from __future__ import annotations

import json
import logging
from typing import Any

from src.domain.enums import JobPhase, JobStatus, ResearchDepth
from src.domain.models import (
    ProgressLogEntry,
    ResearchJob,
    ResearchPlan,
    ResearchReport,
    SourceCitation,
    SubTopicFinding,
)

logger = logging.getLogger(__name__)


def _safe_json_loads(raw_json: str | None, fallback: Any) -> Any:
    """Parst JSON defensiv ohne unsichere Deserialisierer."""
    if not raw_json:
        return fallback
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.warning("Ungültiges JSON in DB-Feld ignoriert: %s", exc)
        return fallback


def map_row_to_job(row: dict[str, Any]) -> ResearchJob:
    """Konvertiert eine Datenbankzeile aus `research_jobs` in ein `ResearchJob`-Modell."""
    raw_logs = _safe_json_loads(row.get("progress_log_json"), [])
    progress_log = [
        ProgressLogEntry.model_validate(item)
        for item in raw_logs
        if isinstance(item, dict)
    ]

    return ResearchJob(
        id=str(row["id"]),
        topic=str(row["topic"]),
        focus_instructions=str(row.get("focus_instructions") or ""),
        depth=ResearchDepth(str(row.get("depth") or "standard")),
        language=str(row.get("language") or "de"),
        notify_email=str(row.get("notify_email") or ""),
        model=str(row.get("model") or "gemini-3.5-flash-lite"),
        status=JobStatus(str(row.get("status") or "queued")),
        phase=JobPhase(str(row.get("phase") or "queued")),
        progress_percent=int(row.get("progress_percent") or 0),
        status_message=str(row.get("status_message") or ""),
        error_message=str(row.get("error_message") or ""),
        cancel_requested=bool(int(row.get("cancel_requested") or 0)),
        email_sent=bool(int(row.get("email_sent") or 0)),
        worker_id=str(row.get("worker_id") or ""),
        created_at=str(row.get("created_at") or ""),
        started_at=str(row["started_at"]) if row.get("started_at") else None,
        completed_at=str(row["completed_at"]) if row.get("completed_at") else None,
        heartbeat_at=str(row["heartbeat_at"]) if row.get("heartbeat_at") else None,
        progress_log=progress_log,
    )


def map_row_to_report(row: dict[str, Any]) -> ResearchReport:
    """Konvertiert eine Datenbankzeile aus `research_reports` in ein `ResearchReport`-Modell."""
    raw_takeaways = _safe_json_loads(row.get("key_takeaways_json"), [])
    takeaways = [str(item) for item in raw_takeaways if item]

    raw_citations = _safe_json_loads(row.get("citations_json"), [])
    citations = [
        SourceCitation.model_validate(item)
        for item in raw_citations
        if isinstance(item, dict) and item.get("url")
    ]

    return ResearchReport(
        job_id=str(row["job_id"]),
        title=str(row["title"]),
        topic=str(row["topic"]),
        depth=ResearchDepth(str(row.get("depth") or "standard")),
        language=str(row.get("language") or "de"),
        executive_summary=str(row.get("executive_summary") or ""),
        key_takeaways=takeaways,
        content_markdown=str(row.get("content_markdown") or ""),
        citations=citations,
        model_used=str(row.get("model_used") or ""),
        duration_seconds=int(row.get("duration_seconds") or 0),
        created_at=str(row.get("created_at") or ""),
    )


def parse_checkpoint_plan(raw_json: str | None) -> ResearchPlan | None:
    """Lädt einen gespeicherten `ResearchPlan` aus einem Checkpoint."""
    if not raw_json or not raw_json.strip():
        return None
    try:
        return ResearchPlan.model_validate_json(raw_json)
    except ValueError as exc:
        logger.warning("Gespeicherter ResearchPlan konnte nicht geladen werden: %s", exc)
        return None


def parse_checkpoint_findings(raw_json: str | None) -> list[SubTopicFinding]:
    """Lädt bereits abgeschlossene Teilthemen-Ergebnisse aus einem Checkpoint."""
    raw_list = _safe_json_loads(raw_json, [])
    findings: list[SubTopicFinding] = []
    for item in raw_list:
        if isinstance(item, dict):
            try:
                findings.append(SubTopicFinding.model_validate(item))
            except ValueError as exc:
                logger.warning("Ungültiges SubTopicFinding übersprungen: %s", exc)
    return findings
