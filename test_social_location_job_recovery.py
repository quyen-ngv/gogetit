from unittest.mock import Mock, patch

from api_server import _post_social_location_callback
from job_store import Job, JobStore, format_job_for_poll


def test_callback_retries_with_backoff_and_internal_token():
    response = Mock(status_code=503, text="unavailable")
    response.ok = False

    with patch("api_server.requests.post", return_value=response) as post, patch(
        "api_server.time.sleep"
    ) as sleep:
        result = _post_social_location_callback(
            "http://goroute-app:8080/callback",
            {"gorouteJobId": "job-1"},
            "shared-secret",
        )

    assert result["success"] is False
    assert post.call_count == 6
    assert sleep.call_count == 5
    assert post.call_args.kwargs["headers"]["X-Internal-Token"] == "shared-secret"


def test_failed_social_job_keeps_extraction_result_for_backend_reconciliation():
    job = Job(
        id="python-job",
        job_type="social-location-extract",
        status="failed",
        created_at="2026-01-01T00:00:00+00:00",
        request={"goroute_job_id": "goroute-job"},
        result={
            "success": False,
            "extractionSucceeded": True,
            "extraction": {"success": True, "extraction": {"candidates": []}},
        },
        error={"code": "CALLBACK_DELIVERY_FAILED", "message": "callback unavailable"},
    )

    payload = format_job_for_poll(job)

    assert payload["status"] == "failed"
    assert payload["result"]["extractionSucceeded"] is True
    assert payload["error"]["code"] == "CALLBACK_DELIVERY_FAILED"


def test_job_store_finds_existing_job_by_goroute_idempotency_key():
    store = JobStore(max_workers=1)
    job = store.create(
        "social-location-extract",
        {"goroute_job_id": "goroute-job", "url": "https://www.tiktok.com/video/1"},
    )

    assert (
        store.find_by_request_value(
            "social-location-extract", "goroute_job_id", "goroute-job"
        )
        is job
    )
