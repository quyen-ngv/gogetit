"""Process-wide browser capacity control shared by every scraper flow."""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from collections.abc import Iterator

from config import PLACE_BROWSER_MAX_CONCURRENCY

try:  # Linux container lock; Windows development falls back to the semaphore.
    import fcntl
except ImportError:  # pragma: no cover - Windows only
    fcntl = None


LOG = logging.getLogger(__name__)
_BROWSER_SLOTS = threading.BoundedSemaphore(PLACE_BROWSER_MAX_CONCURRENCY)
_BROWSER_LOCK_FILE = os.getenv("PLACE_BROWSER_LOCK_FILE", "/tmp/goroute-browser.lock")
_THREAD_STATE = threading.local()


def acquire_browser_slot() -> None:
    started_at = time.monotonic()
    _BROWSER_SLOTS.acquire()
    lock_handle = None
    try:
        if fcntl is not None and PLACE_BROWSER_MAX_CONCURRENCY == 1:
            lock_handle = open(_BROWSER_LOCK_FILE, "a+", encoding="utf-8")
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        handles = getattr(_THREAD_STATE, "lock_handles", None)
        if handles is None:
            handles = []
            _THREAD_STATE.lock_handles = handles
        handles.append(lock_handle)
    except Exception:
        if lock_handle is not None:
            lock_handle.close()
        _BROWSER_SLOTS.release()
        raise
    waited = time.monotonic() - started_at
    if waited >= 1:
        LOG.info("Browser capacity acquired after %.1fs", waited)


def release_browser_slot() -> None:
    handles = getattr(_THREAD_STATE, "lock_handles", None) or []
    lock_handle = handles.pop() if handles else None
    try:
        if lock_handle is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
    finally:
        _BROWSER_SLOTS.release()


@contextmanager
def browser_slot() -> Iterator[None]:
    acquire_browser_slot()
    try:
        yield
    finally:
        release_browser_slot()
