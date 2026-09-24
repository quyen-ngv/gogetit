"""
COMMENTED OUT DUE TO SCRAPE REVIEW ERRORS
All tests in this file have been disabled because review scraping functionality is disabled.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

# THESE IMPORTS WILL FAIL BECAUSE THE FUNCTIONS ARE DISABLED
# from place_review_refresh_job import (
#     _is_retryable_browser_error,
#     _refresh_one,
#     fetch_place_review_candidates,
#     is_daily_refresh_enabled,
#     run_place_review_refresh,
# )
# from run_job import _review_from_dom_payload


# ALL TESTS BELOW ARE DISABLED
"""
class PlaceReviewRefreshJobTest(unittest.TestCase):
    @patch("place_review_refresh_job.requests.get")
    def test_candidates_are_active_and_due(self, get_mock):
        get_mock.return_value.status_code = 200
        get_mock.return_value.raise_for_status.return_value = None
        get_mock.return_value.json.return_value = {
            "data": {
                "databaseCount": 3,
                "eligibleCount": 2,
                "items": [
                    {
                        "id": "more-google-images",
                        "placeId": "google-1",
                        "visibilityStatus": "ACTIVE",
                        "googleMapsLink": "https://www.google.com/maps/place/one",
                        "googleImageReviewCount": 20,
                        "recalculationNeededCount": 10,
                    },
                    {
                        "id": "empty-place",
                        "placeId": "google-2",
                        "visibilityStatus": "ACTIVE",
                        "googleMapsLink": "https://www.google.com/maps/place/two",
                        "googleImageReviewCount": 0,
                        "recalculationNeededCount": 0,
                    },
                ],
            }
        }

        candidates, database_count = fetch_place_review_candidates(max_age_hours=24)

        self.assertEqual(database_count, 3)
        self.assertEqual(
            [candidate["id"] for candidate in candidates],
            ["more-google-images", "empty-place"],
        )
        self.assertTrue(get_mock.call_args.kwargs["params"]["maxAgeHours"] == 24)

    @patch("place_review_refresh_job.scrape_place_reviews_only")
    @patch("place_review_refresh_job._post_backend")
    def test_old_reviews_are_not_deleted_before_one_place_is_scraped(self, post_mock, scrape_mock):
        calls = []

        def post(path, **kwargs):
            calls.append(path)
            return {"ok": True}

        post_mock.side_effect = post
        scrape_mock.side_effect = lambda *args, **kwargs: calls.append("scrape") or {
            "scrapeStatus": "ok",
            "reviews": [
                {
                    "reviewId": "review-1",
                    "name": "Reviewer",
                    "rating": 5,
                    "images": ["https://googleusercontent.example/photo.jpg"],
                    "when": "2026-08-10T00:00:00Z",
                }
            ],
        }

        result = _refresh_one(
            {
                "id": "db-place-id",
                "placeId": "google-place-id",
                "visibilityStatus": "ACTIVE",
                "googleMapsLink": "https://www.google.com/maps/place/one",
            },
            headless=True,
        )

        self.assertTrue(result["success"])
        self.assertEqual(calls, ["scrape", "/complete-refresh"])
        self.assertEqual(scrape_mock.call_args.kwargs["max_reviews"], 200)
        self.assertEqual(
            scrape_mock.call_args.kwargs["google_place_id"],
            "google-place-id",
        )
        self.assertTrue(scrape_mock.call_args.kwargs["require_newest_sort"])

    @patch("place_review_refresh_job.scrape_place_reviews_only")
    @patch("place_review_refresh_job._post_backend")
    def test_scrape_failure_never_calls_backend_replacement(self, post_mock, scrape_mock):
        scrape_mock.return_value = {
            "status": "failed",
            "error": "Review scrape exceeded safety limit",
            "reviews": [],
        }

        with self.assertRaises(RuntimeError):
            _refresh_one(
                {
                    "id": "db-place-id",
                    "placeId": "google-place-id",
                    "googleMapsLink": "https://www.google.com/maps/place/one",
                },
                headless=True,
            )

        post_mock.assert_not_called()

    def test_browser_transport_timeout_is_retryable(self):
        self.assertTrue(_is_retryable_browser_error("HTTPConnectionPool: Read timed out"))
        self.assertTrue(_is_retryable_browser_error("Google Maps returned limited view"))

    @patch("place_review_refresh_job.time.sleep")
    @patch("place_review_refresh_job.scrape_place_reviews_only")
    @patch("place_review_refresh_job._post_backend")
    def test_transport_timeout_resets_browser_and_retries(
        self, post_mock, scrape_mock, sleep_mock
    ):
        scrape_mock.side_effect = [
            {"status": "failed", "error": "HTTPConnectionPool: Read timed out"},
            {
                "scrapeStatus": "ok",
                "reviews": [{
                    "reviewId": "review-1",
                    "name": "Reviewer",
                    "rating": 5,
                    "images": ["https://lh3.googleusercontent.com/photo.jpg"],
                }],
            },
        ]
        post_mock.return_value = {"inserted": 1}
        browser_session = Mock()
        browser_session.get.return_value = object()

        result = _refresh_one(
            {
                "id": "db-place-id",
                "placeId": "google-place-id",
                "googleMapsLink": "https://www.google.com/maps/place/one",
            },
            headless=True,
            browser_session=browser_session,
        )

        self.assertTrue(result["success"])
        self.assertEqual(scrape_mock.call_count, 2)
        browser_session.reset.assert_called_once()
        sleep_mock.assert_called_once_with(10)

    @patch("place_review_refresh_job._refresh_one")
    @patch("place_review_refresh_job.fetch_place_review_candidates")
    def test_job_reuses_one_browser_session_across_candidates(self, fetch_mock, refresh_mock):
        fetch_mock.return_value = ([
            {"id": "place-1", "placeId": "google-1", "title": "One"},
            {"id": "place-2", "placeId": "google-2", "title": "Two"},
        ], 2)
        refresh_mock.side_effect = lambda candidate, **kwargs: {
            "placeId": candidate["id"],
            "success": True,
        }

        result = run_place_review_refresh(delay_seconds=0)

        self.assertTrue(result["allSucceeded"])
        first_session = refresh_mock.call_args_list[0].kwargs["browser_session"]
        second_session = refresh_mock.call_args_list[1].kwargs["browser_session"]
        self.assertIs(first_session, second_session)

    def test_dom_snapshot_is_converted_without_selenium_round_trips(self):
        review = _review_from_dom_payload(
            {
                "author": "Reviewer",
                "ratingLabel": "5 stars",
                "text": "Good place",
                "rawDate": "a week ago",
                "likesText": "12",
                "photos": ["https://lh3.googleusercontent.com/photo=w100-h100"],
                "profilePicture": "https://lh3.googleusercontent.com/profile=s40",
                "profileUrl": "https://www.google.com/maps/contrib/1",
                "reviewerInfo": "Local Guide · 45 reviews · 8 photos",
            },
            "review-1",
        )

        self.assertEqual(review.review_id, "review-1")
        self.assertEqual(review.rating, 5.0)
        self.assertEqual(review.reviewer_total_reviews, 45)
        self.assertEqual(review.reviewer_total_photos, 8)
        self.assertTrue(review.reviewer_is_local_guide)
        self.assertEqual(len(review.photos), 1)

    @patch("place_review_refresh_job.requests.get")
    def test_daily_refresh_switch_is_read_from_the_config_table(self, get_mock):
        get_mock.return_value.raise_for_status.return_value = None
        get_mock.return_value.json.return_value = {
            "data": {"dailyRefreshEnabled": False, "maxReviews": 200}
        }

        self.assertFalse(is_daily_refresh_enabled())
        self.assertTrue(get_mock.call_args.args[0].endswith("/refresh-settings"))

        get_mock.return_value.json.return_value = {
            "data": {"dailyRefreshEnabled": True, "maxReviews": 200}
        }
        self.assertTrue(is_daily_refresh_enabled())

    @patch("place_review_refresh_job.requests.get")
    def test_unreadable_switch_skips_the_scheduled_run(self, get_mock):
        get_mock.side_effect = RuntimeError("backend unreachable")

        self.assertFalse(is_daily_refresh_enabled())



if __name__ == "__main__":
    unittest.main()
