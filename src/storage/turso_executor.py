"""Cloud-SQL-Executor für Turso / LibSQL über die reine HTTPS Hrana v2 Pipeline."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

import requests

from src.config.constants import (
    DEFAULT_HTTP_RETRIES,
    RETRY_BACKOFF_BASE_SECONDS,
    TURSO_BATCH_SIZE,
    TURSO_HTTP_TIMEOUT_SECONDS,
)
from src.domain.exceptions import StorageConnectionError, StorageError
from src.storage.executor import StatementResult

logger = logging.getLogger(__name__)


def normalize_turso_pipeline_url(raw_url: str) -> str:
    """Konvertiert `libsql://` oder `https://` Datenbank-URLs in den `/v2/pipeline` Endpunkt."""
    cleaned = raw_url.strip()
    if cleaned.startswith("libsql://"):
        cleaned = "https://" + cleaned.removeprefix("libsql://")
    elif not cleaned.startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"

    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise StorageConnectionError("Ungültige Turso-Datenbank-URL.")
    return f"{parsed.scheme}://{parsed.netloc}/v2/pipeline"


def _encode_turso_arg(value: Any) -> dict[str, Any]:
    """Kodiert einen Python-Parameter typsicher in das Turso Hrana v2 Format."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


def _decode_turso_cell(cell: dict[str, Any]) -> Any:
    """Dekodiert eine Turso Hrana v2 Ergebniszelle in native Python-Typen."""
    cell_type = cell.get("type")
    raw_val = cell.get("value")
    if cell_type == "null" or raw_val is None:
        return None
    if cell_type == "integer":
        return int(raw_val)
    if cell_type == "float":
        return float(raw_val)
    return str(raw_val)


class TursoExecutor:
    """Kompatibler Cloud-Executor über HTTPS ohne native C-Abhängigkeiten."""

    def __init__(
        self,
        database_url: str,
        auth_token: str,
        timeout_seconds: float = TURSO_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        clean_token = auth_token.strip()
        if not clean_token:
            raise StorageConnectionError("Kein Turso Auth-Token angegeben.")

        self._pipeline_url = normalize_turso_pipeline_url(database_url)
        self._timeout = timeout_seconds
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {clean_token}",
                "Content-Type": "application/json",
                "User-Agent": "ResearchBot/0.1",
            }
        )

    @property
    def backend_name(self) -> str:
        return "Turso Cloud (LibSQL)"

    def execute(self, sql: str, params: Sequence[Any] = ()) -> StatementResult:
        return self.execute_batch([(sql, params)])[0]

    def execute_batch(
        self,
        statements: Sequence[tuple[str, Sequence[Any]]],
    ) -> list[StatementResult]:
        if not statements:
            return []

        all_results: list[StatementResult] = []
        for offset in range(0, len(statements), TURSO_BATCH_SIZE):
            chunk = statements[offset : offset + TURSO_BATCH_SIZE]
            all_results.extend(self._execute_chunk(chunk))
        return all_results

    def _execute_chunk(
        self,
        chunk: Sequence[tuple[str, Sequence[Any]]],
    ) -> list[StatementResult]:
        payload_requests: list[dict[str, Any]] = [
            {
                "type": "execute",
                "stmt": {
                    "sql": sql,
                    "args": [_encode_turso_arg(param) for param in params],
                },
            }
            for sql, params in chunk
        ]
        payload_requests.append({"type": "close"})

        response_json = self._post_with_retry({"requests": payload_requests})
        return self._parse_pipeline_results(response_json)

    def _post_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(DEFAULT_HTTP_RETRIES + 1):
            try:
                response = self._session.post(
                    self._pipeline_url,
                    json=payload,
                    timeout=self._timeout,
                )
                is_transient = response.status_code == 429 or response.status_code >= 500
                if is_transient and attempt < DEFAULT_HTTP_RETRIES:
                    time.sleep(RETRY_BACKOFF_BASE_SECONDS * (attempt + 1))
                    continue
                if response.status_code != 200:
                    raise StorageConnectionError(
                        f"Turso HTTP-Fehler {response.status_code} bei Pipeline-Aufruf."
                    )
                return response.json()
            except requests.RequestException as exc:
                if attempt < DEFAULT_HTTP_RETRIES:
                    wait_sec = RETRY_BACKOFF_BASE_SECONDS * (attempt + 1)
                    logger.warning(
                        "Turso-Verbindungsproblem (Versuch %d/%d), erneuter Versuch in %.1fs.",
                        attempt + 1,
                        DEFAULT_HTTP_RETRIES + 1,
                        wait_sec,
                    )
                    time.sleep(wait_sec)
                    continue
                raise StorageConnectionError(
                    "Netzwerkverbindung zu Turso fehlgeschlagen."
                ) from exc

        raise StorageConnectionError("Turso-Anfrage nach Wiederholungen fehlgeschlagen.")

    @staticmethod
    def _parse_pipeline_results(data: dict[str, Any]) -> list[StatementResult]:
        parsed_results: list[StatementResult] = []
        for entry in data.get("results", []):
            entry_type = entry.get("type")
            if entry_type == "error":
                msg = entry.get("error", {}).get("message", "Unbekannter Turso-SQL-Fehler")
                raise StorageError(f"Turso SQL-Fehler: {msg}")
            if entry_type != "ok":
                continue

            resp_obj = entry.get("response", {})
            if resp_obj.get("type") != "execute":
                continue

            exec_res = resp_obj.get("result", {})
            col_names = [col["name"] for col in exec_res.get("cols", [])]
            rows = [
                {col_names[idx]: _decode_turso_cell(cell) for idx, cell in enumerate(raw_row)}
                for raw_row in exec_res.get("rows", [])
            ]
            affected = int(exec_res.get("affected_row_count", 0) or 0)
            parsed_results.append(StatementResult(rows=rows, affected_rows=affected))
        return parsed_results

    def close(self) -> None:
        self._session.close()
