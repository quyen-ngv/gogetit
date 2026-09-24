import unittest

from nationwide_regions import REGIONS, load_search_config, location_queries, region_queries, selected_regions


class NationwideRegionsTest(unittest.TestCase):
    def test_contains_64_unique_legacy_regions(self):
        self.assertEqual(64, len(REGIONS))
        self.assertEqual(64, len({region.code for region in REGIONS}))
        self.assertIn("hatay", {region.code for region in REGIONS})

    def test_tourism_priority_and_query_cap(self):
        ordered = selected_regions()
        self.assertGreaterEqual(ordered[0].priority, ordered[-1].priority)
        queries = region_queries(next(region for region in REGIONS if region.code == "quangnam"), 5)
        self.assertEqual(5, len(queries))
        self.assertIn("Hội An", queries[0])

    def test_region_filter_keeps_requested_ordering(self):
        selected = selected_regions(["hatay", "danang"])
        self.assertEqual(["danang", "hatay"], [region.code for region in selected])

    def test_food_config_covers_every_legacy_region(self):
        foods = load_search_config()["regionalFoods"]
        self.assertEqual({region.code for region in REGIONS}, set(foods))
        self.assertTrue(all(foods[region.code] for region in REGIONS))

    def test_custom_query_can_append_or_replace_defaults(self):
        hanoi = next(region for region in REGIONS if region.code == "hanoi")
        appended = region_queries(hanoi, 3, custom_queries=["bún riêu {region}"])
        self.assertTrue(appended[0].startswith("bún riêu"))
        replaced = region_queries(
            hanoi,
            20,
            custom_queries=["quán {food} {region}"],
            query_mode="REPLACE",
        )
        self.assertEqual(len(load_search_config()["regionalFoods"]["hanoi"]), len(replaced))
        self.assertTrue(all("Hà Nội" in query for query in replaced))

    def test_location_queries_do_not_append_a_city_name(self):
        queries = location_queries(10, custom_queries=["bún bò {region}"], query_mode="APPEND")
        self.assertEqual("bún bò", queries[0])
        self.assertIn("quán ăn", queries)


if __name__ == "__main__":
    unittest.main()
