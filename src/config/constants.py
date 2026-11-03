"""Zentrale fachliche und technische Konstanten (keine Magic Values im Code)."""

from src.domain.enums import ResearchDepth
from src.domain.models import DepthProfile

# --- KI-Modelle & Agenten (gemäß aktuellem Google GenAI SDK >= 2.30.1) ---
DEFAULT_MODEL: str = "gemini-3.5-flash-lite"
DEFAULT_FAST_MODEL: str = DEFAULT_MODEL
DEFAULT_DEEP_MODEL: str = DEFAULT_MODEL
FALLBACK_LITE_MODEL: str = "gemini-3.5-flash-lite"

DEPRECATED_MODEL_PREFIXES: tuple[str, ...] = ("gemini-1.", "gemini-2.")
DEPRECATED_EXACT_MODELS: frozenset[str] = frozenset(
    {
        "gemini-3.5-flash",
        "gemini-3.7-flash",
    }
)

FALLBACK_AVAILABLE_MODELS: tuple[str, ...] = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-4-argon",
)

CANDIDATE_FAST_MODELS: tuple[str, ...] = (
    DEFAULT_MODEL,
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-4-argon",
)
CANDIDATE_DEEP_MODELS: tuple[str, ...] = (
    DEFAULT_MODEL,
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-4-argon",
    "gemini-3.1-flash-lite",
)

DEEP_RESEARCH_AGENT_FAST: str = "deep-research-preview-04-2026"
DEEP_RESEARCH_AGENT_MAX: str = "deep-research-max-preview-04-2026"

# --- Kurze KI-Timeouts, 3-Versuche-Budget & Zeitlimits ---
LLM_QUICK_TIMEOUT_SECONDS: float = 12.0
LLM_FAST_TIMEOUT_SECONDS: float = 18.0
LLM_DEFAULT_TIMEOUT_SECONDS: float = 30.0
MAX_STEP_ERROR_ATTEMPTS: int = 3  # Nach 3 Fehlversuchen sofort Methode wechseln / Teilergebnis!
LLM_PER_MODEL_RETRIES: int = 1
MODEL_SWITCH_PAUSE_SECONDS: float = 0.25
QUICK_MAX_TOTAL_SECONDS: float = 50.0  # Hartes Zeitbudget, damit Schnellüberblick < 1 Min. bleibt
QUICK_RESUME_COOLDOWN_SECONDS: float = 0.5
GROUNDING_QUOTA_COOLDOWN_SECONDS: float = 180.0

MAX_AUTO_JOB_RESUMES: int = 2
AUTO_RESUME_COOLDOWN_SECONDS: float = 4.0
CANCEL_POLL_SLICE_SECONDS: float = 0.2
HEARTBEAT_INTERVAL_SECONDS: float = 15.0
AUTO_RECOVERY_CHECK_INTERVAL_SECONDS: float = 30.0
UI_LIVE_REFRESH_INTERVAL: str = "3s"

# --- Parallelisierung von Teilthemen ---
QUICK_PARALLEL_SUBTOPICS: int = 2
STANDARD_PARALLEL_SUBTOPICS: int = 2

# --- Hintergrund-Interaktionen & Polling ---
DEEP_AGENT_POLL_INTERVAL_SECONDS: float = 8.0
DEEP_AGENT_MAX_WAIT_SECONDS: float = 1800.0

# --- Netzwerk-, Scraping- & DoS-Limits ---
HTTP_CONNECT_TIMEOUT_SECONDS: float = 3.0
HTTP_READ_TIMEOUT_SECONDS: float = 7.0
WEB_FALLBACK_TIMEOUT_SECONDS: float = 4.5
TURSO_HTTP_TIMEOUT_SECONDS: float = 20.0
EMAIL_HTTP_TIMEOUT_SECONDS: float = 15.0
GITHUB_API_TIMEOUT_SECONDS: float = 12.0

MAX_HTTP_REDIRECTS: int = 4
MAX_WEB_PAGE_BYTES: int = 2_000_000  # 2 MB Obergrenze gegen Memory-Exhaustion
MAX_EXTRACTED_CHARS_PER_PAGE: int = 10_000
MAX_CANDIDATE_URL_PROBE_FACTOR: int = 2
ALLOWED_HTTP_PORTS: frozenset[int] = frozenset({80, 443})
ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})

# --- Retry-Parameter ---
DEFAULT_HTTP_RETRIES: int = 2
RETRY_BACKOFF_BASE_SECONDS: float = 1.0
RETRY_BACKOFF_MAX_SECONDS: float = 4.0

# --- Datenbank & Job-Verwaltung ---
DEFAULT_SQLITE_REL_PATH: str = "data/research_bot.db"
SQLITE_BUSY_TIMEOUT_MS: int = 10_000
TURSO_BATCH_SIZE: int = 40
MAX_PROGRESS_LOG_ENTRIES: int = 60
DEFAULT_STALE_JOB_MINUTES: int = 5
DEFAULT_JOBS_LIST_LIMIT: int = 50
DEFAULT_REPORTS_LIST_LIMIT: int = 50
DEFAULT_MAX_CONCURRENT_LOCAL_JOBS: int = 4

# --- Security & Authentifizierung ---
ADMIN_TOKEN_MAX_AGE_SECONDS: int = 86_400  # 24 Stunden
CLOCK_SKEW_TOLERANCE_SECONDS: int = 60
MAX_FAILED_LOGIN_ATTEMPTS: int = 5
LOGIN_LOCKOUT_SECONDS: int = 300  # 5 Minuten Sperre nach Fehlversuchen
AUTH_COOKIE_NAME: str = "research_bot_admin_token"

# --- Validierungsgrenzen ---
MIN_TOPIC_LENGTH: int = 3
MAX_TOPIC_LENGTH: int = 300
MAX_FOCUS_LENGTH: int = 2_000
MAX_EMAIL_LENGTH: int = 254

# --- Recherche-Tiefen-Profile (Schnellüberblick garantiert < 1 Min.) ---
DEPTH_PROFILES: dict[ResearchDepth, DepthProfile] = {
    ResearchDepth.QUICK: DepthProfile(
        depth=ResearchDepth.QUICK,
        label="⚡ Schnellüberblick",
        estimated_duration_label="max. 1 Min.",
        subtopic_count=3,
        max_urls_per_subtopic=0,
        followup_rounds=0,
        use_deep_research_agent=False,
        deep_agent_name="",
        inter_step_pause_seconds=0.0,
    ),
    ResearchDepth.STANDARD: DepthProfile(
        depth=ResearchDepth.STANDARD,
        label="🔎 Standard-Recherche",
        estimated_duration_label="ca. 2–6 Min.",
        subtopic_count=5,
        max_urls_per_subtopic=2,
        followup_rounds=1,
        use_deep_research_agent=False,
        deep_agent_name="",
        inter_step_pause_seconds=0.2,
    ),
    ResearchDepth.DEEP: DepthProfile(
        depth=ResearchDepth.DEEP,
        label="🧠 Tiefenrecherche",
        estimated_duration_label="ca. 12–30 Min.",
        subtopic_count=10,
        max_urls_per_subtopic=3,
        followup_rounds=1,
        use_deep_research_agent=True,
        deep_agent_name=DEEP_RESEARCH_AGENT_FAST,
        inter_step_pause_seconds=0.8,
    ),
    ResearchDepth.EXHAUSTIVE: DepthProfile(
        depth=ResearchDepth.EXHAUSTIVE,
        label="🏛️ Marathon Deep-Dive (> 1 Std. möglich)",
        estimated_duration_label="ca. 40–90+ Min.",
        subtopic_count=15,
        max_urls_per_subtopic=4,
        followup_rounds=2,
        use_deep_research_agent=True,
        deep_agent_name=DEEP_RESEARCH_AGENT_MAX,
        inter_step_pause_seconds=1.2,
    ),
}
