"""Factory zum automatischen Erstellen des passenden Datenbank-Repositories."""

from __future__ import annotations

import logging

from src.config.settings import AppSettings
from src.storage.executor import SqliteExecutor
from src.storage.repository import ResearchRepository, SqlResearchRepository
from src.storage.turso_executor import TursoExecutor

logger = logging.getLogger(__name__)


def create_repository(settings: AppSettings, initialize: bool = True) -> ResearchRepository:
    """
    Erstellt ein `ResearchRepository` auf Basis der Konfiguration:
    - Turso Cloud (LibSQL), sofern `TURSO_URL` und `TURSO_KEY` gesetzt sind.
    - Lokales SQLite (`data/research_bot.db`), falls keine Cloud-DB konfiguriert ist.
    """
    if settings.has_turso_configured:
        executor = TursoExecutor(
            database_url=settings.turso_url,
            auth_token=settings.turso_auth_token,
        )
        logger.info("Speicher-Backend initialisiert: Turso Cloud.")
    else:
        executor = SqliteExecutor(db_path=settings.sqlite_path)
        logger.info("Speicher-Backend initialisiert: Lokales SQLite (%s).", settings.sqlite_path)

    repository = SqlResearchRepository(executor=executor)
    if initialize:
        repository.initialize_schema()
    return repository
