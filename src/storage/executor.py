"""Abstrakte SQL-Ausführungsschnittstelle und lokaler SQLite-Executor (WAL-Modus)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from src.config.constants import SQLITE_BUSY_TIMEOUT_MS
from src.config.settings import PROJECT_ROOT
from src.domain.exceptions import StorageConnectionError, StorageError
from src.security.sanitization import resolve_safe_path


@dataclass(frozen=True)
class StatementResult:
    """Standardisiertes Ergebnis einer einzelnen parametrisierten SQL-Anweisung."""

    rows: list[dict[str, Any]]
    affected_rows: int = 0


class SqlExecutor(Protocol):
    """Protokoll für austauschbare SQL-Ausführungs-Engines (SQLite lokal vs. Turso Cloud)."""

    @property
    def backend_name(self) -> str:
        """Menschenlesbarer Name des aktiven Speicher-Backends."""
        ...

    def execute(self, sql: str, params: Sequence[Any] = ()) -> StatementResult:
        """Führt ein einzelnes parametrisiertes SQL-Statement aus."""
        ...

    def execute_batch(
        self,
        statements: Sequence[tuple[str, Sequence[Any]]],
    ) -> list[StatementResult]:
        """Führt mehrere parametrisierte SQL-Statements atomar als Transaktion aus."""
        ...

    def close(self) -> None:
        """Gibt offene Verbindungsressourcen sauber frei."""
        ...


def _resolve_sqlite_target(db_path: str | Path) -> str:
    """Ermittelt einen sicheren Dateipfad für SQLite oder erlaubt `:memory:` für Tests."""
    raw = str(db_path).strip()
    if raw == ":memory:":
        return ":memory:"

    candidate = Path(raw)
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else resolve_safe_path(PROJECT_ROOT, candidate)
    )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return str(resolved)


class SqliteExecutor:
    """Thread-sicherer lokaler SQLite-Executor mit Write-Ahead-Logging (WAL)."""

    def __init__(self, db_path: str | Path) -> None:
        self._target = _resolve_sqlite_target(db_path)
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(
                self._target,
                timeout=SQLITE_BUSY_TIMEOUT_MS / 1000.0,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._configure_pragmas()
        except sqlite3.Error as exc:
            raise StorageConnectionError(
                f"SQLite-Datenbank konnte nicht geöffnet werden: {exc}"
            ) from exc

    @property
    def backend_name(self) -> str:
        return "SQLite (In-Memory)" if self._target == ":memory:" else "SQLite (Lokal)"

    def _configure_pragmas(self) -> None:
        with self._conn:
            if self._target != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS};")
            self._conn.execute("PRAGMA foreign_keys=ON;")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> StatementResult:
        return self.execute_batch([(sql, params)])[0]

    def execute_batch(
        self,
        statements: Sequence[tuple[str, Sequence[Any]]],
    ) -> list[StatementResult]:
        if not statements:
            return []
        with self._lock:
            try:
                results: list[StatementResult] = []
                with self._conn:
                    cursor = self._conn.cursor()
                    for sql, params in statements:
                        cursor.execute(sql, tuple(params))
                        rows = (
                            [dict(row) for row in cursor.fetchall()]
                            if cursor.description is not None
                            else []
                        )
                        affected = max(0, cursor.rowcount) if cursor.rowcount != -1 else 0
                        results.append(StatementResult(rows=rows, affected_rows=affected))
                return results
            except sqlite3.Error as exc:
                raise StorageError(f"SQLite-Abfragefehler: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()
