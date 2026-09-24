import json
import unittest
from unittest.mock import patch

from nationwide_job import _discover_region, run_nationwide_job
from nationwide_regions import Region


REGION = Region("test", "Test City", 1, ())
CANDIDATE = {
    "placeId": "google-1",
    "cid": "123",
    "googleMapsLink": "https://maps.google.com/place/test",
    "latitude": 10.0,
    "longitude": 106.0,
    "searchQuery": "food Test City",
}


def place(*, rating=4.5, review_count=150, reviews=None):
    reviews = reviews or []
    return {
        "placeId": "google-1",
        "cid": "123",
        "title": "Test Restaurant",
        "category": "Restaurant",
        "placeGroup": "FOOD_AND_DRINK",
        "latitude": 10.0,
        "longitude": 106.0,
        "reviewRating": rating,
        "reviewCount": review_count,
        "images": [
            {"title": "one", "image": "https://images/one.jpg"},
            {"title": "two", "image": "https://images/two.jpg"},
        ],
        "userReviews": reviews,
        "reviews": reviews,
    }


def run_kwargs():
    return {
        "goroute_job_id": "goroute-job",
        "python_job_id": "python-job",
        "callback_url": "http://callback",
        "import_url": "http://import",
        "callback_token": "token",
        "max_reviews": 200,
        "selected_reviews": 20,
        "min_review_count": 101,
        "min_google_rating": 4.0,
        "search_limit_per_query": 40,
        "max_queries_per_region": 20,
        "headless": True,
        "region_codes": None,
        "query_mode": "APPEND",
        "custom_queries": [],
        "include_regional_specialties": True,
        "include_tourist_areas": True,
        "duplicate_check_url": "http://duplicates",
        "cancel_requested": lambda: False,
    }


class NationwideJobTest(unittest.TestCase):
    @patch("nationwide_job.search_google_maps")
    def test_coordinate_search_passes_center_and_filters_outside_radius(self, search):
        search.return_value = {
            "candidates": [
                CANDIDATE,
                {
                    **CANDIDATE,
                    "placeId": "outside",
                    "latitude": 11.0,
                    "longitude": 107.0,
                },
            ]
        }

        candidates, query_count = _discover_region(
            REGION,
            1,
            40,
            True,
            query_mode="REPLACE",
            custom_queries=["quán ăn"],
            include_regional_specialties=True,
            include_tourist_areas=True,
            latitude=10.0,
            longitude=106.0,
            radius_km=1,
            search_zoom=13,
        )

        self.assertEqual(1, query_count)
        self.assertEqual(["google-1"], [candidate["placeId"] for candidate in candidates])
        search.assert_called_once_with(
            "quán ăn",
            latitude=10.0,
            longitude=106.0,
            zoom=13,
            limit=40,
            max_scrolls=25,
            headless=True,
        )

    @patch("nationwide_job._event")
    @patch("nationwide_job._post_json")
    @patch("nationwide_job.scrape_place")
    @patch("nationwide_job._existing_candidate_keys", return_value=set())
    @patch("nationwide_job._discover_region", return_value=([CANDIDATE], 1))
    @patch("nationwide_job.selected_regions", return_value=[REGION])
    def test_filtered_place_is_saved_inactive_with_one_image_and_no_reviews(
        self, _regions, _discover, _existing, scrape, post_json, _event
    ):
        scrape.return_value = place(rating=3.9)
        post_json.return_value = {
            "imported": True,
            "outcome": "SAVED_INACTIVE_GOOGLE_RATING",
        }

        result = run_nationwide_job(**run_kwargs())

        self.assertEqual(1, scrape.call_count)
        self.assertFalse(scrape.call_args.kwargs["include_reviews"])
        body = post_json.call_args.args[1]
        self.assertEqual("GOOGLE_RATING", body["filterReason"])
        self.assertEqual([], body["reviews"])
        self.assertEqual("INACTIVE", body["place"]["visibilityStatus"])
        self.assertIsNone(body["place"]["userReviews"])
        self.assertEqual(1, len(json.loads(body["place"]["images"])))
        self.assertEqual(1, result["imported_count"])

    @patch("nationwide_job._event")
    @patch("nationwide_job._post_json")
    @patch("nationwide_job.scrape_place")
    @patch("nationwide_job._existing_candidate_keys", return_value=set())
    @patch("nationwide_job._discover_region", return_value=([CANDIDATE], 1))
    @patch("nationwide_job.selected_regions", return_value=[REGION])
    def test_eligible_new_place_is_scraped_again_with_reviews(
        self, _regions, _discover, _existing, scrape, post_json, _event
    ):
        review = {
            "reviewId": "review-1",
            "name": "Reviewer",
            "rating": 5,
            "description": "Great",
            "when": "2026-07-01",
        }
        scrape.side_effect = [place(), place(reviews=[review])]
        post_json.return_value = {
            "imported": True,
            "outcome": "IMPORTED",
            "selectedReviewCount": 1,
        }

        run_nationwide_job(**run_kwargs())

        self.assertEqual(
            [False, True],
            [item.kwargs["include_reviews"] for item in scrape.call_args_list],
        )
        body = post_json.call_args.args[1]
        self.assertIsNone(body["filterReason"])
        self.assertEqual(1, len(body["reviews"]))
        self.assertEqual(2, len(json.loads(body["place"]["images"])))

    @patch("nationwide_job._event")
    @patch("nationwide_job._post_json")
    @patch("nationwide_job.scrape_place")
    @patch("nationwide_job._existing_candidate_keys", return_value={"google-1"})
    @patch("nationwide_job._discover_region", return_value=([CANDIDATE], 1))
    @patch("nationwide_job.selected_regions", return_value=[REGION])
    def test_existing_place_is_not_scraped(
        self, _regions, _discover, _existing, scrape, post_json, _event
    ):
        result = run_nationwide_job(**run_kwargs())

        scrape.assert_not_called()
        post_json.assert_not_called()
        self.assertEqual(1, result["skipped_count"])


if __name__ == "__main__":
    unittest.main()
