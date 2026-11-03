"""Startet langlaufende Recherche-Jobs (>1h) entkoppelt über die GitHub Actions REST API."""

from __future__ import annotations

import logging

import requests

from src.config.constants import GITHUB_API_TIMEOUT_SECONDS
from src.config.settings import AppSettings
from src.domain.exceptions import DispatchError

logger = logging.getLogger(__name__)


class GitHubActionsDispatcher:
    """Triggert `.github/workflows/run_research.yaml` via `workflow_dispatch`."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def trigger_workflow(self, job_id: str) -> None:
        """Löst für `job_id` asynchron einen isolierten GitHub-Actions-Runner aus."""
        if not self._settings.has_github_worker_configured:
            raise DispatchError("GitHub-Worker ist nicht vollständig konfiguriert (Token/Repo fehlt).")

        repo = self._settings.github_repo.strip("/")
        workflow = self._settings.github_workflow_file.strip()
        endpoint = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"

        try:
            response = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {self._settings.github_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "ResearchBot/0.1",
                },
                json={
                    "ref": self._settings.github_branch,
                    "inputs": {"job_id": job_id.strip()},
                },
                timeout=GITHUB_API_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise DispatchError(f"Verbindung zur GitHub Actions API fehlgeschlagen: {exc}") from exc

        if response.status_code != 204:
            raise DispatchError(
                f"GitHub Actions Dispatch abgelehnt (HTTP {response.status_code}). "
                "Bitte Token-Berechtigung `actions: write` und Workflow-Datei prüfen."
            )

        logger.info("GitHub Actions Workflow '%s' erfolgreich für Job '%s' gestartet.", workflow, job_id)
