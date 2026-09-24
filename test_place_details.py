import json
import unittest

from run_job import (
    extract_data_id,
    normalize_place_information,
    normalize_about_option,
    parse_open_hours_cells,
    parse_open_hours_copy_label,
    parse_popular_time_label,
)
from upload_to_api import format_place_for_api


class PlaceDetailParserTest(unittest.TestCase):
    def test_google_maps_identity_and_information_normalization(self):
        url = "https://google.com/maps/place/x/data=!1s0x3135ab49e8fb3385:0xec8c09bc5fcd7c39!8m2"
        self.assertEqual(
            extract_data_id(url),
            "0x3135ab49e8fb3385:0xec8c09bc5fcd7c39",
        )
        normalized = normalize_place_information({
            "address": "Address: 29 P. Hàng Trống, Hà Nội",
            "phone": "Phone: +84 985 627 976",
            "plusCode": "Plus code: 2RJX+8R Hoan Kiem",
            "priceRange": "Price range, ₫100,000–200,000 per person, Reported by 10,171 people",
            "currentOpenStatus": "Hours Closed · Opens 10 AM Wed Show open hours for the week",
        })
        self.assertEqual(normalized["address"], "29 P. Hàng Trống, Hà Nội")
        self.assertEqual(normalized["phone"], "+84 985 627 976")
        self.assertEqual(normalized["plusCode"], "2RJX+8R Hoan Kiem")
        self.assertEqual(normalized["priceRange"], "₫100,000–200,000 per person")
        self.assertEqual(normalized["currentOpenStatus"], "Closed · Opens 10 AM Wed")
        dynamic_status = normalize_place_information({"status": "Closed · Opens 10 AM Wed"})
        self.assertEqual(dynamic_status["status"], "")
        self.assertEqual(dynamic_status["currentOpenStatus"], "Closed · Opens 10 AM Wed")

    def test_open_hours_keep_legacy_day_to_list_shape(self):
        self.assertEqual(
            parse_open_hours_copy_label(
                "Monday, 11 AM to 2 PM, 5 PM to 11 PM, Copy open hours"
            ),
            ("Monday", ["11 AM - 2 PM", "5 PM - 11 PM"]),
        )
        self.assertEqual(
            parse_open_hours_copy_label(
                "Sunday, Open 24 hours, Copy open hours"
            ),
            ("Sunday", ["Open 24 hours"]),
        )
        self.assertEqual(
            parse_open_hours_copy_label(
                "Thursday, 7\u202fAM to 2\u202fPM, Copy open hours"
            ),
            ("Thursday", ["7 AM - 2 PM"]),
        )
        self.assertEqual(
            parse_open_hours_copy_label("Sunday, Closed, Copy open hours"),
            ("Sunday", ["Closed"]),
        )
        self.assertEqual(
            parse_open_hours_copy_label("Monday, 8 AM to 5 PM, Copy business hours"),
            ("Monday", ["8 AM - 5 PM"]),
        )
        self.assertEqual(
            parse_open_hours_cells(["Monday", "10 AM–11 PM", "Copy open hours"]),
            ("Monday", ["10 AM–11 PM"]),
        )

    def test_popular_time_uses_24_hour_string_keys(self):
        self.assertEqual(parse_popular_time_label("65% busy at 12 PM."), ("12", 65))
        self.assertEqual(parse_popular_time_label("52% busy at 11 AM."), ("11", 52))
        self.assertEqual(parse_popular_time_label("10% busy at 12 AM."), ("0", 10))

    def test_about_preserves_negative_options(self):
        self.assertEqual(
            normalize_about_option(
                "No wheelchair accessible entrance",
                "Wheelchair accessible entrance",
            ),
            {"name": "Wheelchair accessible entrance", "enabled": False},
        )
        self.assertEqual(
            normalize_about_option("Accepts credit cards", "Credit cards"),
            {"name": "Credit cards", "enabled": True},
        )
        self.assertEqual(
            normalize_about_option("Serves dine-in", "\ue5ca Dine-in"),
            {"name": "Dine-in", "enabled": True},
        )

    def test_uploader_serializes_existing_database_fields(self):
        body = format_place_for_api(
            {
                "placeId": "ChIJ-test",
                "title": "Test place",
                "latitude": 21.0,
                "longitude": 105.0,
                "openHours": {"Monday": ["9 AM - 5 PM"]},
                "popularTimes": {"Monday": {"9": 25}},
                "menu": {
                    "data": [{"title": "Photo 1 of 2", "url": "https://img.example/menu-1"}],
                    "highlights": [{"title": "Pho Tai Chin", "url": ""}],
                },
                "reservations": {"available": True},
                "orderOnline": {"available": True},
                "completeAddress": {"formatted": "Hanoi"},
                "about": [
                    {
                        "id": "accessibility",
                        "name": "Accessibility",
                        "options": [
                            {
                                "name": "Wheelchair accessible entrance",
                                "enabled": False,
                            }
                        ],
                    }
                ],
                "owner": {},
                "emails": [],
                "rawData": {"source": "google_maps_web"},
            }
        )

        self.assertEqual(
            json.loads(body["openHours"]),
            {"Monday": ["9 AM - 5 PM"]},
        )
        self.assertEqual(json.loads(body["popularTimes"]), {"Monday": {"9": 25}})
        self.assertEqual(
            json.loads(body["about"])[0]["options"][0]["enabled"],
            False,
        )
        self.assertEqual(
            json.loads(body["menu"])["data"][0]["url"],
            "https://img.example/menu-1",
        )
        self.assertEqual(set(json.loads(body["menu"])), {"data", "highlights"})
        self.assertEqual(
            json.loads(body["menu"])["highlights"][0]["title"],
            "Pho Tai Chin",
        )
        self.assertTrue(json.loads(body["reservations"])["available"])
        self.assertTrue(json.loads(body["orderOnline"])["available"])


if __name__ == "__main__":
    unittest.main()
