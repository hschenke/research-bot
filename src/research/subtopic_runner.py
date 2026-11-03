"""Parallele und Checkpoint-gesicherte Ausführung einzelner Teilthemen-Untersuchungen."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.config.constants import QUICK_PARALLEL_SUBTOPICS, STANDARD_PARALLEL_SUBTOPICS
from src.domain.enums import JobPhase, ResearchDepth
from src.domain.exceptions import JobCancelledError, ResearchBotError
from src.domain.models import (
    DepthProfile,
    ResearchJob,
    ResearchPlan,
    SubTopicFinding,
    SubTopicPlan,
)
from src.research.gemini_client import ResearchLLMGateway
from src.research.investigator import investigate_subtopic
from src.research.llm_helpers import sleep_with_cancel_check
from src.research.web_fetcher import SafeWebFetcher
from src.storage.base_repository import ResearchRepository

logger = logging.getLogger(__name__)


def resolve_subtopic_parallelism(depth: ResearchDepth) -> int:
    """Bestimmt die optimale Anzahl paralleler Teilthemen-Worker pro Recherche-Tiefe."""
    if depth == ResearchDepth.QUICK:
        return QUICK_PARALLEL_SUBTOPICS
    if depth == ResearchDepth.STANDARD:
        return STANDARD_PARALLEL_SUBTOPICS
    return 2


def execute_subtopics_batch(
    repository: ResearchRepository,
    gateway: ResearchLLMGateway,
    web_fetcher: SafeWebFetcher,
    job: ResearchJob,
    plan: ResearchPlan,
    profile: DepthProfile,
    existing_findings: list[SubTopicFinding],
    baseline_markdown: str,
    check_alive: Callable[[], None],
    allow_partial_on_error: bool = False,
) -> list[SubTopicFinding]:
    """Verarbeitet alle noch offenen Teilthemen parallel mit sofortiger Checkpoint-Sicherung."""
    completed: dict[int, SubTopicFinding] = {f.subtopic_index: f for f in existing_findings}
    total = max(1, len(plan.subtopics))
    pending = [st for st in plan.subtopics if st.index not in completed]
    if not pending:
        return [completed[idx] for idx in sorted(completed)]

    check_alive()
    workers = min(resolve_subtopic_parallelism(job.depth), len(pending))
    repository.update_progress(
        job_id=job.id,
        phase=JobPhase.INVESTIGATING,
        progress_percent=20 + int((len(completed) / total) * 65),
        message=(
            f"Untersuche {len(pending)} Teilgebiet(e) parallel..."
            if workers > 1
            else f"Untersuche Teilgebiet {len(completed) + 1}/{total}: {pending[0].title}"
        ),
    )

    lock = threading.Lock()
    first_error: ResearchBotError | None = None

    def _run_one(subtopic: SubTopicPlan) -> SubTopicFinding:
        check_alive()
        return investigate_subtopic(
            gateway=gateway,
            web_fetcher=web_fetcher,
            job=job,
            subtopic=subtopic,
            profile=profile,
            baseline_excerpt=baseline_markdown,
            on_heartbeat=check_alive,
        )

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="subtopic") as pool:
        future_map = {pool.submit(_run_one, st): st for st in pending}
        for fut in as_completed(future_map):
            st = future_map[fut]
            try:
                finding = fut.result()
            except JobCancelledError:
                for other in future_map:
                    other.cancel()
                raise
            except ResearchBotError as exc:
                if first_error is None:
                    first_error = exc
                logger.warning("Teilgebiet %d ('%s') fehlgeschlagen: %s", st.index, st.title, exc)
                continue

            with lock:
                completed[st.index] = finding
                ordered = [completed[idx] for idx in sorted(completed)]
                repository.save_findings_checkpoint(job.id, ordered)
                pct = 20 + int((len(completed) / total) * 65)
                repository.update_progress(
                    job_id=job.id,
                    phase=JobPhase.INVESTIGATING,
                    progress_percent=pct,
                    message=f"Teilgebiet abgeschlossen ({len(completed)}/{total}): {st.title}",
                )

            if profile.inter_step_pause_seconds > 0 and len(completed) < total:
                sleep_with_cancel_check(profile.inter_step_pause_seconds, check_alive)

    if first_error is not None and (not allow_partial_on_error or not completed):
        raise first_error

    return [completed[idx] for idx in sorted(completed)]
