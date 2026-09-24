"""Shared configuration for scraper, Telegram bot, and HTTP API."""

import os
from urllib.parse import urlsplit, urlunsplit

GOROUTE_API_URL = os.getenv(
    "GOROUTE_API_URL",
    "https://onestudy.id.vn/goroute/v1/api/places/import",
)
GOROUTE_API_KEY = (os.getenv("GOROUTE_API_KEY") or os.getenv("PLACE_REFRESH_API_KEY") or "").strip()

_DEFAULT_GOROUTE_API_HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def goroute_api_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return headers for requests sent to GoRoute, including the service API key."""
    headers = dict(_DEFAULT_GOROUTE_API_HEADERS)
    if extra:
        headers.update(extra)
    if GOROUTE_API_KEY:
        headers["X-API-Key"] = GOROUTE_API_KEY
    return headers


GOROUTE_API_HEADERS = goroute_api_headers()


def _default_goroute_places_url(import_url: str) -> str:
    """Derive the place-list endpoint from the configured import endpoint."""
    parts = urlsplit(import_url)
    path = parts.path.rstrip("/")
    if path.endswith("/import"):
        path = path[: -len("/import")]
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


GOROUTE_PLACES_URL = os.getenv(
    "GOROUTE_PLACES_URL",
    _default_goroute_places_url(GOROUTE_API_URL),
)


def _default_review_refresh_url(places_url: str) -> str:
    parts = urlsplit(places_url)
    path = parts.path.rstrip("/")
    if path.endswith("/places"):
        path = path[: -len("/places")] + "/place-reviews"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


GOROUTE_REVIEW_REFRESH_URL = os.getenv(
    "GOROUTE_REVIEW_REFRESH_URL",
    _default_review_refresh_url(GOROUTE_PLACES_URL),
)

PLACE_API_HOST = os.getenv("PLACE_API_HOST", "0.0.0.0")
PLACE_API_PORT = int(os.getenv("PLACE_API_PORT", "8080"))
PLACE_API_MAX_WORKERS = max(1, int(os.getenv("PLACE_API_MAX_WORKERS", "1")))
PLACE_BROWSER_MAX_CONCURRENCY = max(1, int(os.getenv("PLACE_BROWSER_MAX_CONCURRENCY", "1")))
PLACE_BROWSER_PAGE_LOAD_STRATEGY = os.getenv("PLACE_BROWSER_PAGE_LOAD_STRATEGY", "eager").strip() or "eager"
PLACE_BROWSER_BLOCK_NONESSENTIAL = os.getenv("PLACE_BROWSER_BLOCK_NONESSENTIAL", "true").lower() in {
    "1", "true", "yes", "on"
}
PLACE_BROWSER_BLOCK_IMAGES = os.getenv("PLACE_BROWSER_BLOCK_IMAGES", "true").lower() in {
    "1", "true", "yes", "on"
}
PLACE_JOB_HISTORY_TTL_HOURS = max(1, int(os.getenv("PLACE_JOB_HISTORY_TTL_HOURS", "24")))
PLACE_JOB_HISTORY_MAX_COUNT = max(10, int(os.getenv("PLACE_JOB_HISTORY_MAX_COUNT", "200")))

PLACE_REFRESH_ENABLED = os.getenv("PLACE_REFRESH_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
PLACE_REFRESH_HOUR = int(os.getenv("PLACE_REFRESH_HOUR", "6"))
PLACE_REFRESH_MINUTE = int(os.getenv("PLACE_REFRESH_MINUTE", "0"))
PLACE_REFRESH_TIMEZONE = os.getenv("PLACE_REFRESH_TIMEZONE", "Asia/Bangkok")
PLACE_REFRESH_MAX_PLACES = int(os.getenv("PLACE_REFRESH_MAX_PLACES", "0"))
PLACE_REFRESH_DELAY_SECONDS = float(os.getenv("PLACE_REFRESH_DELAY_SECONDS", "5"))
PLACE_REFRESH_API_KEY = os.getenv("PLACE_REFRESH_API_KEY", "")

PLACE_REVIEW_REFRESH_ENABLED = os.getenv("PLACE_REVIEW_REFRESH_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
PLACE_REVIEW_REFRESH_HOUR = int(os.getenv("PLACE_REVIEW_REFRESH_HOUR", "7"))
PLACE_REVIEW_REFRESH_MINUTE = int(os.getenv("PLACE_REVIEW_REFRESH_MINUTE", "0"))
PLACE_REVIEW_REFRESH_MAX_PLACES = int(os.getenv("PLACE_REVIEW_REFRESH_MAX_PLACES", "0"))
PLACE_REVIEW_REFRESH_MAX_AGE_HOURS = int(os.getenv("PLACE_REVIEW_REFRESH_MAX_AGE_HOURS", "24"))
PLACE_REVIEW_REFRESH_DELAY_SECONDS = float(os.getenv("PLACE_REVIEW_REFRESH_DELAY_SECONDS", "0"))
PLACE_REVIEW_BROWSER_CRASH_RETRIES = max(0, int(os.getenv("PLACE_REVIEW_BROWSER_CRASH_RETRIES", "1")))
PLACE_REVIEW_MAX_SCROLLS = max(10, int(os.getenv("PLACE_REVIEW_MAX_SCROLLS", "300")))
PLACE_REVIEW_SCRAPE_TIMEOUT_SECONDS = max(
    60, int(os.getenv("PLACE_REVIEW_SCRAPE_TIMEOUT_SECONDS", "360"))
)
PLACE_REVIEW_NO_IMAGE_SCROLL_LIMIT = max(
    5, int(os.getenv("PLACE_REVIEW_NO_IMAGE_SCROLL_LIMIT", "30"))
)
PLACE_BROWSER_COMMAND_TIMEOUT_SECONDS = max(
    10, int(os.getenv("PLACE_BROWSER_COMMAND_TIMEOUT_SECONDS", "45"))
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_USER_ID = int(os.getenv("TELEGRAM_USER_ID", "5636346689"))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")

AI_PROVIDER = os.getenv("AI_PROVIDER", "DEEPSEEK").strip().upper()
AI_MODEL = os.getenv("AI_MODEL", "").strip()
AI_BASE_URL = os.getenv("AI_BASE_URL", "").strip()
AI_REQUEST_TIMEOUT_SECONDS = int(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "120"))
AI_API_KEY = os.getenv("AI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1-mini")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_WHISPER_MODEL = os.getenv("OPENAI_WHISPER_MODEL", "whisper-1")

LOCAL_WHISPER_ENABLED = os.getenv("LOCAL_WHISPER_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
LOCAL_WHISPER_MODEL = os.getenv("LOCAL_WHISPER_MODEL", "small")
LOCAL_WHISPER_DEVICE = os.getenv("LOCAL_WHISPER_DEVICE", "cpu")
LOCAL_WHISPER_COMPUTE_TYPE = os.getenv("LOCAL_WHISPER_COMPUTE_TYPE", "int8")
LOCAL_WHISPER_LANGUAGE = os.getenv("LOCAL_WHISPER_LANGUAGE", "vi")
LOCAL_WHISPER_CACHE_DIR = os.getenv("LOCAL_WHISPER_CACHE_DIR", "")

SOCIAL_YTDLP_COOKIE_FILE = os.getenv("SOCIAL_YTDLP_COOKIE_FILE", "/app/cookies.txt").strip()
SOCIAL_YTDLP_IMPERSONATE = os.getenv("SOCIAL_YTDLP_IMPERSONATE", "chrome").strip()
