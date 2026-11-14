"""Zentrale technische Konstanten; sämtliche Fachlogik wird deklarativ aus `settings.yaml` geladen."""

from src.config.domain_loader import load_default_domain_config
from src.domain.enums import ResearchDepth
from src.domain.models import DepthProfile

_DEFAULT_DOMAIN = load_default_domain_config()

# --- KI-Modelle & Agenten (Standardwerte aus config/settings.yaml, in Web-App editierbar) ---
DEFAULT_MODEL: str = _DEFAULT_DOMAIN.default_model
DEFAULT_FAST_MODEL: str = DEFAULT_MODEL
DEFAULT_DEEP_MODEL: str = DEFAULT_MODEL
FALLBACK_LITE_MODEL: str = DEFAULT_MODEL

FALLBACK_AVAILABLE_MODELS: tuple[str, ...] = tuple(_DEFAULT_DOMAIN.available_models)
CANDIDATE_FAST_MODELS: tuple[str, ...] = FALLBACK_AVAILABLE_MODELS
CANDIDATE_DEEP_MODELS: tuple[str, ...] = FALLBACK_AVAILABLE_MODELS

DEEP_RESEARCH_AGENT_FAST: str = _DEFAULT_DOMAIN.depth_profiles[ResearchDepth.DEEP].deep_agent_name

# --- KI-Aufruf-Timeouts, Retries & Workload-Limits ---
MIN_LLM_TIMEOUT_SECONDS: float = 15.0
LLM_QUICK_TIMEOUT_SECONDS: float = 60.0
LLM_FAST_TIMEOUT_SECONDS: float = 60.0
LLM_DEFAULT_TIMEOUT_SECONDS: float = 120.0
MAX_STEP_ERROR_ATTEMPTS: int = 3
LLM_PER_MODEL_RETRIES: int = 2
MODEL_SWITCH_PAUSE_SECONDS: float = 0.5
QUICK_MAX_TOTAL_SECONDS: float = 300.0
QUICK_RESUME_COOLDOWN_SECONDS: float = 1.0
GROUNDING_QUOTA_COOLDOWN_SECONDS: float = 180.0

MAX_AUTO_JOB_RESUMES: int = 2
AUTO_RESUME_COOLDOWN_SECONDS: float = 4.0
CANCEL_POLL_SLICE_SECONDS: float = 0.2
HEARTBEAT_INTERVAL_SECONDS: float = 15.0
AUTO_RECOVERY_CHECK_INTERVAL_SECONDS: float = 30.0
UI_LIVE_REFRESH_INTERVAL: str = "3s"

# --- Parallelisierung von Teilthemen (aus settings.yaml abgeleitet) ---
QUICK_PARALLEL_SUBTOPICS: int = _DEFAULT_DOMAIN.depth_profiles[ResearchDepth.QUICK].parallel_subtopics
STANDARD_PARALLEL_SUBTOPICS: int = _DEFAULT_DOMAIN.depth_profiles[ResearchDepth.STANDARD].parallel_subtopics

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
MAX_CONFIG_YAML_BYTES: int = 100_000
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
DEFAULT_STALE_JOB_MINUTES: int = _DEFAULT_DOMAIN.stale_job_timeout_minutes
DEFAULT_JOBS_LIST_LIMIT: int = 50
DEFAULT_REPORTS_LIST_LIMIT: int = 50
DEFAULT_MAX_CONCURRENT_LOCAL_JOBS: int = _DEFAULT_DOMAIN.max_concurrent_local_jobs

# --- Security & Authentifizierung ---
ADMIN_TOKEN_MAX_AGE_SECONDS: int = 2_592_000  # 30 Tage persistente Session
CLOCK_SKEW_TOLERANCE_SECONDS: int = 60
MAX_FAILED_LOGIN_ATTEMPTS: int = 5
LOGIN_LOCKOUT_SECONDS: int = 300  # 5 Minuten Sperre nach Fehlversuchen
AUTH_COOKIE_NAME: str = "research_bot_admin_token"

# --- Validierungsgrenzen ---
MIN_TOPIC_LENGTH: int = 3
MAX_TOPIC_LENGTH: int = 300
MAX_FOCUS_LENGTH: int = 2_000
MAX_EMAIL_LENGTH: int = 254

# --- Standard-Recherche-Tiefen-Profile (geladen aus config/settings.yaml) ---
DEPTH_PROFILES: dict[ResearchDepth, DepthProfile] = _DEFAULT_DOMAIN.to_depth_profiles_map()
