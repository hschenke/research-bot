"""CLI-Entrypoint für GitHub Actions und Cron-Watchdog (`python -m src.worker.cli_worker`)."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from src.config.settings import load_app_settings
from src.notifications.notifier import EmailNotifier
from src.research.gemini_client import GeminiInteractionsGateway
from src.research.orchestrator import ResearchOrchestrator
from src.research.web_fetcher import SafeWebFetcher
from src.storage.factory import create_repository


def _configure_logging() -> None:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hintergrund-Worker für langlaufende Research-Bot-Aufträge."
    )
    parser.add_argument(
        "--job-id",
        type=str,
        default="",
        help="Spezifische Job-ID zur sofortigen Verarbeitung.",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=3,
        help="Maximale Anzahl wartender/verwaister Jobs bei einem Watchdog-Durchlauf.",
    )
    return parser.parse_args(argv)


def run_worker_cli(argv: list[str] | None = None) -> int:
    """Führt einen einzelnen Job oder alle fälligen Warteschlangen-Jobs aus."""
    _configure_logging()
    args = _parse_args(argv)
    logger = logging.getLogger("src.worker.cli_worker")

    settings = load_app_settings()
    if not settings.has_gemini_configured:
        logger.error("Abbruch: GEMINI_API_KEY ist nicht gesetzt.")
        return 1

    repo = create_repository(settings, initialize=True)
    domain_cfg = repo.get_domain_config()
    routing = domain_cfg.model_routing if domain_cfg else None
    fetcher = SafeWebFetcher(
        settings=settings,
        enable_jina_reader=routing.enable_jina_reader if routing else True,
        enable_browserbase=routing.enable_browserbase if routing else False,
    )
    worker_id = f"gh-run-{os.getenv('GITHUB_RUN_ID', 'cli')}"

    try:
        gateway = GeminiInteractionsGateway(settings)
        notifier = EmailNotifier(settings)
        orchestrator = ResearchOrchestrator(
            settings=settings,
            repository=repo,
            llm_gateway=gateway,
            web_fetcher=fetcher,
            notifier=notifier,
        )

        target_ids = (
            [args.job_id.strip()]
            if args.job_id.strip()
            else repo.find_claimable_job_ids(
                stale_timeout_minutes=repo.get_domain_config().stale_job_timeout_minutes,
                limit=max(1, args.max_jobs),
            )
        )

        if not target_ids:
            logger.info("Keine wartenden oder unterbrochenen Recherche-Aufträge vorhanden.")
            return 0

        for current_id in target_ids:
            logger.info("Starte Verarbeitung von Auftrag '%s' (Worker: %s)...", current_id, worker_id)
            orchestrator.execute_job(job_id=current_id, worker_id=worker_id)
        return 0
    finally:
        fetcher.close()
        repo.close()


if __name__ == "__main__":
    raise SystemExit(run_worker_cli())
