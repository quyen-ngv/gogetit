"""Report each AI provider call of a social-video job to the backend's `ai_api_calls` log.

`api_server` binds the job's callback URL/token before running the extraction; the extractor
calls `record(...)` after every provider call. Reporting is best effort and never raises.
Inline images (base64 frames) are replaced by a size marker so a row stays small.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlparse

import requests

LOG = logging.getLogger(__name__)
_INTERNAL_PATH = "/v1/api/internal/ai-calls"
_CALLBACK_SUFFIX = "/v1/api/internal/social-location/jobs/callback"
_DATA_URL = re.compile(r"data:[\w.+/-]+;base64,[A-Za-z0-9+/=]{64,}")
_BASE64_RUN = re.compile(r"^[A-Za-z0-9+/=]{512,}$")

_target: ContextVar[dict[str, str] | None] = ContextVar("ai_call_log_target", default=None)


def bind(callback_url: str | None, token: str | None, trace_id: str | None):
    """Send this thread's reports next to the job callback (same host, same internal token)."""
    if not callback_url:
        return _target.set(None)
    if callback_url.endswith(_CALLBACK_SUFFIX):
        url = callback_url[: -len(_CALLBACK_SUFFIX)] + _INTERNAL_PATH
    else:
        parsed = urlparse(callback_url)
        url = f"{parsed.scheme}://{parsed.netloc}{_INTERNAL_PATH}"
    return _target.set({"url": url, "token": token or "", "traceId": trace_id or ""})


def unbind(token) -> None:
    _target.reset(token)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        if _BASE64_RUN.match(value):
            return f"<base64 {len(value)} chars>"
        return _DATA_URL.sub(lambda m: f"<data-url {len(m.group())} chars>", value)
    return value


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list, str, int, float, bool)):
        return value
    dump = getattr(value, "model_dump", None)  # Anthropic SDK objects
    if callable(dump):
        try:
            return dump(mode="json")
        except Exception:
            pass
    return str(value)


def record(*, operation: str, provider: str | None, model: str | None, ok: bool, request: Any,
           response: Any = None, error: Any = None, input_tokens: int | None = None,
           cached_input_tokens: int | None = None, output_tokens: int | None = None,
           latency_ms: int | None = None) -> None:
    target = _target.get()
    if not target:
        return
    entry = {
        "feature": "SOCIAL_LOCATION", "operation": operation, "correlationId": str(uuid.uuid4()),
        "traceId": target["traceId"] or None, "provider": (provider or "").lower() or None, "model": model,
        "status": "SUCCEEDED" if ok else "FAILED",
        "request": _redact(_jsonable(request)), "response": _redact(_jsonable(response)), "error": _jsonable(error),
        "inputTokens": input_tokens, "cachedInputTokens": cached_input_tokens, "outputTokens": output_tokens,
        "latencyMs": latency_ms,
    }
    try:
        headers = {"Content-Type": "application/json"}
        if target["token"]:
            headers["X-Internal-Token"] = target["token"]
        response_ = requests.post(target["url"], data=json.dumps(entry, ensure_ascii=False, default=str).encode(),
                                  headers=headers, timeout=10)
        if response_.status_code >= 300:
            LOG.warning("AI call log rejected (%s): %s", response_.status_code, response_.text[:200])
    except Exception as exc:  # never let logging break a job
        LOG.warning("AI call log not delivered (%s %s): %s", operation, model, exc)


def chat_usage(payload: Any) -> tuple[int | None, int | None, int | None]:
    """(input, cached input, output) from an OpenAI/DeepSeek chat/completions body."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None, None, None
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    if cached is None:
        cached = usage.get("prompt_cache_hit_tokens")
    return usage.get("prompt_tokens"), cached, usage.get("completion_tokens")


def anthropic_usage(message: Any) -> tuple[int | None, int | None, int | None]:
    """Anthropic counts cache reads outside input_tokens; the log's input includes them."""
    usage = getattr(message, "usage", None)
    if usage is None:
        return None, None, None
    fresh, cached = getattr(usage, "input_tokens", None), getattr(usage, "cache_read_input_tokens", None)
    total = (fresh or 0) + (cached or 0) if fresh is not None or cached is not None else None
    return total, cached, getattr(usage, "output_tokens", None)
