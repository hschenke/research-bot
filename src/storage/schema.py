"""Zentrale Datenbank-DDL-Definitionen für SQLite und Turso (LibSQL)."""

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS research_jobs (
        id TEXT PRIMARY KEY,
        topic TEXT NOT NULL,
        focus_instructions TEXT NOT NULL DEFAULT '',
        depth TEXT NOT NULL DEFAULT 'standard',
        language TEXT NOT NULL DEFAULT 'de',
        notify_email TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'queued',
        phase TEXT NOT NULL DEFAULT 'queued',
        progress_percent INTEGER NOT NULL DEFAULT 0,
        status_message TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        email_sent INTEGER NOT NULL DEFAULT 0,
        worker_id TEXT NOT NULL DEFAULT '',
        plan_json TEXT NOT NULL DEFAULT '',
        baseline_markdown TEXT NOT NULL DEFAULT '',
        findings_json TEXT NOT NULL DEFAULT '[]',
        progress_log_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        heartbeat_at TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS research_reports (
        job_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        topic TEXT NOT NULL,
        depth TEXT NOT NULL,
        language TEXT NOT NULL,
        executive_summary TEXT NOT NULL,
        key_takeaways_json TEXT NOT NULL DEFAULT '[]',
        content_markdown TEXT NOT NULL,
        citations_json TEXT NOT NULL DEFAULT '[]',
        model_used TEXT NOT NULL DEFAULT '',
        duration_seconds INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON research_jobs(status, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_heartbeat ON research_jobs(status, heartbeat_at);",
    "CREATE INDEX IF NOT EXISTS idx_reports_created ON research_reports(created_at DESC);",
)
