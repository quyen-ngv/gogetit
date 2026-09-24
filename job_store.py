"""In-memory async job store for background scrape/import tasks."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from config import PLACE_API_MAX_WORKERS, PLACE_JOB_HISTORY_MAX_COUNT, PLACE_JOB_HISTORY_TTL_HOURS


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    job_type: str
    status: str
    created_at: str
    request: dict[str, Any]
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | str | None = None
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return format_job_for_poll(self)


def _build_input_summary(request: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"url": request.get("url", "")}
    for key in ("source", "place_id", "max_places", "headless", "continue_on_error"):
        if key in request:
            summary[key] = request[key]
    if request.get("platform"):
        summary["platform"] = request["platform"]
    if request.get("goroute_job_id"):
        summary["gorouteJobId"] = request["goroute_job_id"]
    contribution = request.get("contribution") or {}
    if contribution.get("contributionGroupId"):
        summary["contributionGroupId"] = contribution["contributionGroupId"]
    if contribution.get("gorouteJobId"):
        summary["gorouteJobId"] = contribution["gorouteJobId"]
    return summary


def _build_error(job: Job) -> dict[str, Any]:
    if isinstance(job.error, dict) and job.error.get("code"):
        return job.error

    result = job.result or {}
    if result.get("scrapeError"):
        return {"code": "SCRAPE_FAILED", "message": str(result["scrapeError"])}

    upload = result.get("upload") or {}
    if upload.get("error"):
        return {"code": "IMPORT_FAILED", "message": str(upload["error"])}

    if isinstance(job.error, str) and job.error:
        return {"code": "JOB_FAILED", "message": job.error}

    return {"code": "JOB_FAILED", "message": "Unknown error"}


def _build_completed_result(result: dict[str, Any]) -> dict[str, Any]:
    place = result.get("place") or {}
    poll_result: dict[str, Any] = {
        "googlePlaceId": result.get("googlePlaceId") or place.get("placeId") or "",
        "title": result.get("title") or place.get("title") or "",
        "placeAlreadyExists": bool(result.get("placeAlreadyExists", False)),
        "importStatus": "success" if result.get("success") else "failed",
        "importHttpStatus": result.get("importHttpStatus") or (result.get("upload") or {}).get("statusCode"),
    }

    for key in ("goroutePlaceId", "reviewsPublished", "contributorsAdded"):
        if key in result:
            poll_result[key] = result[key]

    return poll_result


def format_job_for_poll(job: Job) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jobId": job.id,
        "status": job.status,
        "createdAt": job.created_at,
        "input": _build_input_summary(job.request),
    }

    if job.started_at:
        payload["startedAt"] = job.started_at
    if job.finished_at:
        payload["completedAt"] = job.finished_at

    if job.job_type == "social-location-extract":
        if job.status == "failed":
            payload["error"] = _build_error(job)
        if job.result is not None:
            payload["result"] = job.result
        return payload

    if job.job_type in {"scrape-and-import-batch", "place-detail-refresh", "place-review-refresh", "nationwide-place-import"}:
        if job.error:
            payload["error"] = _build_error(job)
        if job.result is not None:
            payload["result"] = job.result
        return payload

    if job.status == "failed":
        payload["error"] = _build_error(job)
        if job.result:
            payload["result"] = _build_completed_result(job.result)
        return payload

    if job.status == "completed" and job.result is not None:
        payload["result"] = _build_completed_result(job.result)
        return payload

    return payload


class JobStore:
    def __init__(
        self,
        max_workers: int = PLACE_API_MAX_WORKERS,
        *,
        history_ttl_hours: int = PLACE_JOB_HISTORY_TTL_HOURS,
        history_max_count: int = PLACE_JOB_HISTORY_MAX_COUNT,
    ) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="place-job")
        self._history_ttl = timedelta(hours=max(1, history_ttl_hours))
        self._history_max_count = max(10, history_max_count)

    def _prune_locked(self) -> None:
        """Bound completed-job retention without ever removing active work."""
        now = datetime.now(timezone.utc)
        completed: list[tuple[datetime, str]] = []
        expired: list[str] = []
        for job_id, job in self._jobs.items():
            if job.status in {"pending", "running"}:
                continue
            timestamp_text = job.finished_at or job.created_at
            try:
                timestamp = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                else:
                    timestamp = timestamp.astimezone(timezone.utc)
            except (TypeError, ValueError):
                timestamp = now
            if now - timestamp > self._history_ttl:
                expired.append(job_id)
            else:
                completed.append((timestamp, job_id))

        for job_id in expired:
            self._jobs.pop(job_id, None)

        completed.sort(reverse=True)
        for _, job_id in completed[self._history_max_count :]:
            self._jobs.pop(job_id, None)

    def create(self, job_type: str, request: dict[str, Any]) -> Job:
        job = Job(
            id=str(uuid.uuid4()),
            job_type=job_type,
            status="pending",
            created_at=_utc_now(),
            request=request,
        )
        with self._lock:
            self._prune_locked()
            self._jobs[job.id] = job
        return job

    def create_unique(self, job_type: str, request: dict[str, Any]) -> tuple[Job, bool]:
        """Create a job unless another job of the same type is pending or running."""
        with self._lock:
            self._prune_locked()
            active = next(
                (
                    job
                    for job in self._jobs.values()
                    if job.job_type == job_type and job.status in {"pending", "running"}
                ),
                None,
            )
            if active is not None:
                return active, False
            job = Job(
                id=str(uuid.uuid4()),
                job_type=job_type,
                status="pending",
                created_at=_utc_now(),
                request=request,
            )
            self._jobs[job.id] = job
            return job, True

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            self._prune_locked()
            return self._jobs.get(job_id)

    def find_by_request_value(self, job_type: str, key: str, value: Any) -> Job | None:
        """Return a retained job matching an idempotency value in its request."""
        if value is None:
            return None
        with self._lock:
            self._prune_locked()
            return next(
                (
                    job
                    for job in self._jobs.values()
                    if job.job_type == job_type and job.request.get(key) == value
                ),
                None,
            )

    def update_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.status in {"pending", "running"}:
                job.result = {"progress": progress}

    def request_cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in {"pending", "running"}:
                return False
            job.cancel_requested = True
            return True

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.cancel_requested)

    def submit(
        self,
        job: Job,
        worker: Callable[[], dict[str, Any]],
        *,
        serial_review_queue: bool = False,
    ) -> None:
        def run() -> None:
            self._mark_running(job.id)
            try:
                result = worker()
                self._mark_completed(job.id, result)
            except Exception as exc:
                self._mark_failed(job.id, {"code": "INTERNAL_ERROR", "message": str(exc)})

        # All background work shares one bounded executor. This prevents review,
        # nationwide and social jobs from multiplying Chrome/ffmpeg/Whisper load.
        self._executor.submit(run)

    def _mark_running(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = _utc_now()

    def _mark_completed(self, job_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.cancel_requested or result.get("cancelled"):
                job.status = "cancelled"
            else:
                job.status = "completed" if result.get("success") else "failed"
            job.finished_at = _utc_now()
            job.result = result
            if not result.get("success"):
                callback = result.get("callback") or {}
                if job.job_type == "social-location-extract" and callback.get("success") is False:
                    job.error = {
                        "code": "CALLBACK_DELIVERY_FAILED",
                        "message": str(callback.get("error") or "GoRoute callback delivery failed"),
                    }
                elif result.get("scrapeError"):
                    job.error = {"code": "SCRAPE_FAILED", "message": str(result["scrapeError"])}
                else:
                    upload_error = (result.get("upload") or {}).get("error") or "Import failed"
                    job.error = {"code": "IMPORT_FAILED", "message": str(upload_error)}

    def _mark_failed(self, job_id: str, error: dict[str, Any] | str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "failed"
            job.finished_at = _utc_now()
            job.error = error


job_store = JobStore()
