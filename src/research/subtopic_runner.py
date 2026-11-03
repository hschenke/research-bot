"""Parallele, zeitbudgetierte & teilergebnisfähige Ausführung einzelner Teilthemen."""

from __future__ import annotations

import logging
import threading
import time
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
from src.research.llm_helpers import is_quota_error, sleep_with_cancel_check
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


def _scope_gateway_for_subtopic(
    gateway: ResearchLLMGateway,
    action_hook: Callable[[str], None],
) -> ResearchLLMGateway:
    if hasattr(gateway, "with_action_callback"):
        return gateway.with_action_callback(action_hook)
    return gateway


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
    deadline_monotonic: float | None = None,
) -> list[SubTopicFinding]:
    """Verarbeitet offene Teilthemen parallel mit Live-Aktionsanzeige und Teilergebnis-Schutz."""
    completed: dict[int, SubTopicFinding] = {f.subtopic_index: f for f in existing_findings}
    total = max(1, len(plan.subtopics))
    pending = [st for st in plan.subtopics if st.index not in completed]
    if not pending:
        return [completed[idx] for idx in sorted(completed)]

    check_alive()
    workers = min(resolve_subtopic_parallelism(job.depth), len(pending))
    first_live = f"[Teilgebiet {pending[0].index}/{total}] 🔎 Starte Recherche zu »{pending[0].title[:45]}«..."
    repository.update_progress(
        job_id=job.id,
        phase=JobPhase.INVESTIGATING,
        progress_percent=20 + int((len(completed) / total) * 65),
        message=f"Starte Untersuchung von {len(pending)} offenen Teilgebiet(en)...",
        live_status_override=first_live,
    )

    lock = threading.Lock()
    active_actions: dict[int, str] = {}
    first_error: ResearchBotError | None = None
    abort_remaining = threading.Event()

    def _run_one(subtopic: SubTopicPlan) -> SubTopicFinding:
        check_alive()
        if abort_remaining.is_set():
            raise ResearchBotError("Vorzeitiger Abschluss mit Teilergebnis aktiv.")

        def _on_sub_action(detail: str) -> None:
            formatted = f"[Teilgebiet {subtopic.index}/{total}] {detail}"
            with lock:
                active_actions[subtopic.index] = formatted
            repository.update_live_action(job.id, formatted)

        _on_sub_action(f"🔎 Analysiere »{subtopic.title[:42]}«...")
        scoped_gw = _scope_gateway_for_subtopic(gateway, _on_sub_action)
        return investigate_subtopic(
            gateway=scoped_gw,
            web_fetcher=web_fetcher,
            job=job,
            subtopic=subtopic,
            profile=profile,
            baseline_excerpt=baseline_markdown,
            on_heartbeat=check_alive,
            on_action=_on_sub_action,
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
                with lock:
                    active_actions.pop(st.index, None)
                    if first_error is None:
                        first_error = exc
                    deadline_near = deadline_monotonic is not None and time.monotonic() >= deadline_monotonic - 5.0
                    if bool(completed) and deadline_near:
                        abort_remaining.set()
                        for other in future_map:
                            other.cancel()
                logger.warning("Teilgebiet %d ('%s') fehlgeschlagen: %s", st.index, st.title, exc)
                continue

            with lock:
                active_actions.pop(st.index, None)
                completed[st.index] = finding
                ordered = [completed[idx] for idx in sorted(completed)]
                repository.save_findings_checkpoint(job.id, ordered)
                pct = 20 + int((len(completed) / total) * 65)
                next_live = (
                    next(iter(active_actions.values()))
                    if active_actions
                    else (
                        "Alle Teilgebiete erhoben – starte Berichts-Synthese..."
                        if len(completed) >= total
                        else f"Bereite nächstes Teilgebiet ({len(completed) + 1}/{total}) vor..."
                    )
                )
                repository.update_progress(
                    job_id=job.id,
                    phase=JobPhase.INVESTIGATING,
                    progress_percent=pct,
                    message=f"Teilgebiet abgeschlossen ({len(completed)}/{total}): {st.title}",
                    live_status_override=next_live,
                )

            if profile.inter_step_pause_seconds > 0 and len(completed) < total:
                sleep_with_cancel_check(profile.inter_step_pause_seconds, check_alive)

    deadline_reached = deadline_monotonic is not None and time.monotonic() >= deadline_monotonic - 5.0
    can_use_partial = bool(completed) and (
        allow_partial_on_error
        or deadline_reached
        or (first_error is not None and is_quota_error(first_error))
    )
    if first_error is not None and not can_use_partial:
        raise first_error

    return [completed[idx] for idx in sorted(completed)]
