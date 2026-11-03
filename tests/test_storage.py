"""Storage-Tests: SQLite-Repository, atomares Job-Claiming, Checkpoints & Turso-Hrana-Protokoll."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from src.domain.enums import JobPhase, JobStatus, ResearchDepth
from src.domain.models import (
    ResearchPlan,
    ResearchReport,
    ResearchRequest,
    SourceCitation,
    SubTopicFinding,
    SubTopicPlan,
)
from src.storage.executor import SqliteExecutor
from src.storage.repository import SqlResearchRepository
from src.storage.turso_executor import TursoExecutor, normalize_turso_pipeline_url


def _make_memory_repo() -> SqlResearchRepository:
    repo = SqlResearchRepository(SqliteExecutor(":memory:"))
    repo.initialize_schema()
    return repo


class TestSqlResearchRepository:
    def test_job_lifecycle_atomic_claim_and_checkpoints(self) -> None:
        repo = _make_memory_repo()
        req = ResearchRequest(
            topic="Grüner Wasserstoff in Europa",
            focus_instructions="Kosten und Elektrolyse-Pipelines",
            depth=ResearchDepth.DEEP,
            language="de",
            notify_email="analyst@beispiel.de",
            model="gemini-3.6-flash",
        )
        job = repo.create_job(req)
        assert job.status == JobStatus.QUEUED
        assert job.model == "gemini-3.6-flash"

        # Erster Worker beansprucht den Job erfolgreich; zweiter Worker wird atomar abgewiesen
        assert repo.claim_job(job.id, worker_id="worker-1") is True
        assert repo.claim_job(job.id, worker_id="worker-2") is False

        # Plan- & Teilthemen-Checkpoint speichern und wiederherstellen
        plan = ResearchPlan(
            normalized_title="Wasserstoff-Infrastruktur 2026",
            objective_summary="Analyse der europäischen Kapazitäten.",
            subtopics=[
                SubTopicPlan(
                    index=1,
                    title="Elektrolyse-Kosten",
                    guiding_question="Wie entwickeln sich die CAPEX?",
                    search_queries=["electrolyzer capex europe 2026"],
                )
            ],
        )
        repo.save_plan_checkpoint(job.id, plan)
        repo.save_baseline_checkpoint(job.id, "Baseline Fakten...")
        repo.save_findings_checkpoint(
            job.id,
            [
                SubTopicFinding(
                    subtopic_index=1,
                    title="Elektrolyse-Kosten",
                    analysis_markdown="Die Kosten sanken deutlich.",
                    key_facts=["CAPEX sank um 18%"],
                    citations=[SourceCitation(url="https://example.com/h2", title="H2 Studie")],
                )
            ],
        )

        cp = repo.load_checkpoint(job.id)
        assert cp.plan is not None
        assert cp.plan.normalized_title == "Wasserstoff-Infrastruktur 2026"
        assert cp.baseline_markdown == "Baseline Fakten..."
        assert len(cp.findings) == 1
        assert cp.findings[0].key_facts == ["CAPEX sank um 18%"]

        # Abschluss mit Bericht & Volltextsuche
        report = ResearchReport(
            job_id=job.id,
            title="Dossier: Grüner Wasserstoff",
            topic=job.topic,
            depth=job.depth,
            language="de",
            executive_summary="Umfassende Zusammenfassung.",
            key_takeaways=["Punkt 1", "Punkt 2"],
            content_markdown="# Dossier\n\nInhalt mit Elektrolyse-Details.",
            citations=[SourceCitation(url="https://example.com/h2", title="H2 Studie")],
            model_used="gemini-4-argon",
            duration_seconds=3720,
            created_at=datetime.now(UTC).isoformat(),
        )
        repo.mark_job_completed(job.id, report)
        repo.mark_job_email_sent(job.id)

        updated_job = repo.get_job(job.id)
        assert updated_job is not None
        assert updated_job.status == JobStatus.COMPLETED
        assert updated_job.email_sent is True

        found_reports = repo.list_reports(search_query="Elektrolyse")
        assert len(found_reports) == 1
        assert found_reports[0].title == "Dossier: Grüner Wasserstoff"
        repo.close()

    def test_stale_running_job_can_be_recovered_by_watchdog(self) -> None:
        executor = SqliteExecutor(":memory:")
        repo = SqlResearchRepository(executor)
        repo.initialize_schema()

        job = repo.create_job(ResearchRequest(topic="Verwaister Test-Job"))
        assert repo.claim_job(job.id, worker_id="crashed-worker") is True

        # Simuliere eingefrorenen Heartbeat vor 45 Minuten
        old_hb = (datetime.now(UTC) - timedelta(minutes=45)).isoformat()
        executor.execute("UPDATE research_jobs SET heartbeat_at = ? WHERE id = ?", (old_hb, job.id))

        claimable = repo.find_claimable_job_ids(stale_timeout_minutes=20)
        assert job.id in claimable
        assert repo.claim_job(job.id, worker_id="recovery-worker", stale_timeout_minutes=20) is True
        repo.close()

    def test_cancellation_and_requeue_preserve_checkpoints(self) -> None:
        repo = _make_memory_repo()
        job = repo.create_job(ResearchRequest(topic="Abzubrechender Auftrag"))
        repo.claim_job(job.id, worker_id="w1")
        repo.update_progress(job.id, JobPhase.INVESTIGATING, 45, "Schritt 2/4")

        assert repo.request_cancellation(job.id) is True
        cancelled_now = repo.get_job(job.id)
        assert cancelled_now is not None
        assert cancelled_now.status == JobStatus.CANCELLED
        assert repo.is_cancellation_requested(job.id) is True

        # Nachzügler-Worker darf abgebrochenen Job nicht wieder überschreiben
        repo.update_progress(job.id, JobPhase.SYNTHESIZING, 90, "Später Schritt")
        repo.mark_job_failed(job.id, "Später Fehler")
        still_cancelled = repo.get_job(job.id)
        assert still_cancelled is not None
        assert still_cancelled.status == JobStatus.CANCELLED

        assert repo.requeue_job_from_checkpoint(job.id) is True
        requeued = repo.get_job(job.id)
        assert requeued is not None
        assert requeued.status == JobStatus.QUEUED
        assert requeued.cancel_requested is False
        repo.close()

    def test_cleanup_stuck_cancellations_count_and_bulk_delete(self) -> None:
        executor = SqliteExecutor(":memory:")
        repo = SqlResearchRepository(executor)
        repo.initialize_schema()

        j_active = repo.create_job(ResearchRequest(topic="Aktiver Job"))
        j_stuck = repo.create_job(ResearchRequest(topic="Hängender Abbruch"))
        j_failed = repo.create_job(ResearchRequest(topic="Fehlgeschlagener Job"))

        repo.claim_job(j_stuck.id, worker_id="w-stuck")
        executor.execute(
            "UPDATE research_jobs SET cancel_requested = 1 WHERE id = ?",
            (j_stuck.id,),
        )
        repo.mark_job_failed(j_failed.id, "503 high demand")

        cleaned = repo.cleanup_stuck_cancellations()
        assert cleaned == 1
        stuck_after = repo.get_job(j_stuck.id)
        assert stuck_after is not None
        assert stuck_after.status == JobStatus.CANCELLED

        assert repo.count_jobs(statuses=(JobStatus.RUNNING, JobStatus.QUEUED)) == 1
        purged = repo.delete_jobs_by_status((JobStatus.CANCELLED, JobStatus.FAILED))
        assert purged == 2
        assert repo.get_job(j_active.id) is not None
        assert repo.get_job(j_stuck.id) is None
        assert repo.get_job(j_failed.id) is None
        repo.close()


class TestTursoExecutorProtocol:
    def test_normalizes_libsql_and_https_urls(self) -> None:
        assert (
            normalize_turso_pipeline_url("libsql://my-db-hschenke.turso.io")
            == "https://my-db-hschenke.turso.io/v2/pipeline"
        )
        assert (
            normalize_turso_pipeline_url("https://my-db.turso.io/")
            == "https://my-db.turso.io/v2/pipeline"
        )

    def test_executes_and_decodes_hrana_v2_pipeline(self) -> None:
        turso = TursoExecutor("libsql://demo.turso.io", auth_token="tok_test")
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "results": [
                {
                    "type": "ok",
                    "response": {
                        "type": "execute",
                        "result": {
                            "cols": [{"name": "id"}, {"name": "progress_percent"}],
                            "rows": [
                                [
                                    {"type": "text", "value": "abc123"},
                                    {"type": "integer", "value": "75"},
                                ]
                            ],
                            "affected_row_count": 1,
                        },
                    },
                },
                {"type": "ok", "response": {"type": "close"}},
            ]
        }
        turso._session.post = MagicMock(return_value=fake_response)  # type: ignore[method-assign]

        result = turso.execute("SELECT id, progress_percent FROM research_jobs WHERE id = ?", ("abc123",))
        assert result.affected_rows == 1
        assert result.rows == [{"id": "abc123", "progress_percent": 75}]

        sent_payload: dict[str, Any] = turso._session.post.call_args.kwargs["json"]
        assert sent_payload["requests"][0]["stmt"]["args"] == [{"type": "text", "value": "abc123"}]
        turso.close()
