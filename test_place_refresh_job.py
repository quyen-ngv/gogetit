import unittest
from unittest.mock import Mock, patch

from job_store import JobStore
from place_refresh_job import fetch_place_refresh_candidates, run_place_detail_refresh


class PlaceRefreshJobTest(unittest.TestCase):
    @staticmethod
    def _candidate_response(items, status_code=200):
        response = Mock()
        response.status_code = status_code
        response.json.return_value = {"data": {"eligibleCount": len(items), "items": items}}
        return response

    @patch("place_refresh_job.requests.get")
    def test_candidates_come_from_db_response_and_skip_invalid_urls(self, get_mock):
        get_mock.return_value = self._candidate_response([
            {"id": "1", "googleMapsLink": "https://www.google.com/maps/place/One"},
            {"id": "2", "googleMapsLink": ""},
            {"id": "3", "googleMapsLink": "https://example.com/not-maps"},
        ])

        candidates, database_count = fetch_place_refresh_candidates(max_places=None)

        self.assertEqual(database_count, 3)
        self.assertEqual([item["id"] for item in candidates], ["1"])
        self.assertTrue(get_mock.call_args.args[0].endswith("/detail-refresh-candidates"))

    @patch("place_refresh_job.requests.get")
    def test_active_only_by_default_and_inactive_is_opt_in(self, get_mock):
        get_mock.return_value = self._candidate_response([])

        fetch_place_refresh_candidates()
        self.assertEqual(get_mock.call_args.kwargs["params"]["includeInactive"], "false")

        fetch_place_refresh_candidates(include_inactive=True)
        self.assertEqual(get_mock.call_args.kwargs["params"]["includeInactive"], "true")

    @patch("place_refresh_job.requests.get")
    def test_single_place_id_is_sent_as_a_filter(self, get_mock):
        get_mock.return_value = self._candidate_response([
            {"id": "db-id", "googleMapsLink": "https://www.google.com/maps/place/One"},
        ])

        candidates, database_count = fetch_place_refresh_candidates(place_id="db-id")

        self.assertEqual(database_count, 1)
        self.assertEqual(candidates[0]["id"], "db-id")
        self.assertTrue(get_mock.call_args.args[0].endswith("/detail-refresh-candidates"))
        self.assertEqual(get_mock.call_args.kwargs["params"]["placeId"], "db-id")

    @patch("place_refresh_job.requests.get")
    def test_falls_back_to_the_legacy_place_list_when_endpoint_is_missing(self, get_mock):
        missing = Mock()
        missing.status_code = 404
        legacy = Mock()
        legacy.status_code = 200
        legacy.json.return_value = {
            "data": [{"id": "1", "googleMapsLink": "https://www.google.com/maps/place/One"}]
        }
        get_mock.side_effect = [missing, legacy]

        candidates, database_count = fetch_place_refresh_candidates()

        self.assertEqual(database_count, 1)
        self.assertEqual(candidates[0]["id"], "1")
        legacy.raise_for_status.assert_called_once()

    @patch("place_refresh_job.scrape_and_import")
    @patch("place_refresh_job.fetch_place_refresh_candidates")
    def test_refresh_is_details_only_and_preserves_db_identity(self, fetch_mock, scrape_mock):
        fetch_mock.return_value = (
            [
                {
                    "id": "db-id",
                    "placeId": "google-id",
                    "title": "Existing title",
                    "placeGroup": "FOOD_AND_DRINK",
                    "status": "OPEN",
                    "visibilityStatus": "ACTIVE",
                    "googleMapsLink": "https://www.google.com/maps/place/One",
                }
            ],
            1,
        )
        result = Mock()
        result.success = True
        result.blocked_by_google = False
        result.to_dict.return_value = {"success": True}
        scrape_mock.return_value = result
        progress = []

        report = run_place_detail_refresh(progress_callback=progress.append)

        self.assertTrue(report["allSucceeded"])
        self.assertEqual(report["successCount"], 1)
        self.assertEqual(progress[-1]["processedCount"], 1)
        _, kwargs = scrape_mock.call_args
        self.assertFalse(kwargs["include_reviews"])
        self.assertEqual(kwargs["place_overrides"]["placeId"], "google-id")
        self.assertEqual(kwargs["place_overrides"]["placeGroup"], "FOOD_AND_DRINK")
        self.assertEqual(kwargs["place_overrides"]["status"], "OPEN")

    def test_job_store_rejects_duplicate_active_refresh(self):
        store = JobStore(max_workers=1)
        first, first_created = store.create_unique("place-detail-refresh", {"source": "api"})
        second, second_created = store.create_unique("place-detail-refresh", {"source": "schedule"})

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.id, second.id)

    @patch("place_refresh_job.scrape_and_import")
    @patch("place_refresh_job.fetch_place_refresh_candidates")
    def test_refresh_stops_before_next_place_when_cancelled(self, fetch_mock, scrape_mock):
        fetch_mock.return_value = (
            [{"id": "db-id", "googleMapsLink": "https://www.google.com/maps/place/One"}],
            1,
        )

        report = run_place_detail_refresh(cancel_requested=lambda: True)

        self.assertTrue(report["cancelled"])
        self.assertEqual(report["processedCount"], 0)
        scrape_mock.assert_not_called()

    @staticmethod
    def _scrape_result(*, blocked):
        result = Mock()
        result.success = not blocked
        result.blocked_by_google = blocked
        result.to_dict.return_value = {"success": not blocked, "blockedByGoogle": blocked}
        return result

    @staticmethod
    def _places(*ids):
        return [{"id": place_id, "googleMapsLink": f"https://www.google.com/maps/place/{place_id}"} for place_id in ids], len(ids)

    @patch("place_refresh_job.time.sleep")
    @patch("place_refresh_job.PLACE_REFRESH_BLOCK_BACKOFF_SECONDS", (60.0,))
    @patch("place_refresh_job.scrape_and_import")
    @patch("place_refresh_job.fetch_place_refresh_candidates")
    def test_a_lone_blocked_place_is_skipped_without_waiting(self, fetch_mock, scrape_mock, sleep_mock):
        fetch_mock.return_value = self._places("blocked", "next")
        scrape_mock.side_effect = [self._scrape_result(blocked=True), self._scrape_result(blocked=False)]

        report = run_place_detail_refresh(delay_seconds=0)

        self.assertTrue(report["success"])
        self.assertEqual(report["processedCount"], 2)
        self.assertEqual(report["successCount"], 1)
        self.assertEqual(report["failedCount"], 1)
        self.assertEqual(
            [call.args[0].rsplit("/", 1)[-1] for call in scrape_mock.call_args_list], ["blocked", "next"]
        )
        sleep_mock.assert_not_called()

    @patch("place_refresh_job.time.sleep")
    @patch("place_refresh_job.PLACE_REFRESH_BLOCK_BACKOFF_SECONDS", (60.0,))
    @patch("place_refresh_job.scrape_and_import")
    @patch("place_refresh_job.fetch_place_refresh_candidates")
    def test_blocks_in_a_row_back_off_before_moving_to_the_next_place(self, fetch_mock, scrape_mock, sleep_mock):
        fetch_mock.return_value = self._places("one", "two", "three")
        scrape_mock.side_effect = [
            self._scrape_result(blocked=True),
            self._scrape_result(blocked=True),
            self._scrape_result(blocked=False),
        ]

        report = run_place_detail_refresh(delay_seconds=0)

        self.assertTrue(report["success"])
        self.assertEqual(report["processedCount"], 3)
        self.assertEqual(report["failedCount"], 2)
        self.assertEqual(scrape_mock.call_count, 3)
        self.assertAlmostEqual(sum(call.args[0] for call in sleep_mock.call_args_list), 60.0, delta=1.0)

    @patch("place_refresh_job.time.sleep")
    @patch("place_refresh_job.PLACE_REFRESH_BLOCK_BACKOFF_SECONDS", (60.0,))
    @patch("place_refresh_job.scrape_and_import")
    @patch("place_refresh_job.fetch_place_refresh_candidates")
    def test_cancel_during_a_block_backoff_ends_the_job_as_cancelled(self, fetch_mock, scrape_mock, sleep_mock):
        fetch_mock.return_value = self._places("one", "two", "three")
        scrape_mock.return_value = self._scrape_result(blocked=True)
        cancel_checks = iter([False, False, False, True])

        report = run_place_detail_refresh(delay_seconds=0, cancel_requested=lambda: next(cancel_checks, True))

        self.assertTrue(report["cancelled"])
        self.assertEqual(report["processedCount"], 2)
        self.assertEqual(scrape_mock.call_count, 2)

    @patch("place_refresh_job.time.sleep")
    @patch("place_refresh_job.PLACE_REFRESH_BLOCK_BACKOFF_SECONDS", (60.0,))
    @patch("place_refresh_job.scrape_and_import")
    @patch("place_refresh_job.fetch_place_refresh_candidates")
    def test_refresh_stops_the_batch_when_places_stay_blocked_through_every_backoff(
        self, fetch_mock, scrape_mock, sleep_mock
    ):
        fetch_mock.return_value = self._places("one", "two", "three", "never-reached")
        scrape_mock.return_value = self._scrape_result(blocked=True)

        report = run_place_detail_refresh(delay_seconds=0)

        self.assertFalse(report["success"])
        self.assertTrue(report["blockedByGoogle"])
        self.assertEqual(report["processedCount"], 3)
        self.assertEqual(report["failedCount"], 3)
        self.assertEqual(scrape_mock.call_count, 3)

if __name__ == "__main__":
    unittest.main()
