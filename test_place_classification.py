from place_classification import derive_sub_types, google_categories


def test_whole_words_only():
    assert derive_sub_types(["Barber shop"], "OTHER") == []
    assert derive_sub_types(["Barbecue restaurant"], "FOOD_AND_DRINK") == ["RESTAURANT"]


def test_several_kinds_from_every_category():
    kinds = derive_sub_types(["Seafood restaurant", "Halal restaurant"], "FOOD_AND_DRINK")
    assert kinds == ["RESTAURANT", "SEAFOOD", "HALAL"]


def test_exclusions():
    assert "BAR" not in derive_sub_types(["Snack bar"], "FOOD_AND_DRINK")
    assert derive_sub_types(["Amusement park"], "ATTRACTIONS") == ["THEME_PARK"]


def test_group_hint_and_fallback():
    assert derive_sub_types([], "NATURE_AND_OUTDOORS") == ["NATURE"]
    assert derive_sub_types(["Tourist attraction"], "CULTURE_AND_HERITAGE") == ["HERITAGE"]
    assert derive_sub_types(["Buddhist temple"], "CULTURE_AND_HERITAGE") == ["SPIRITUAL"]


def test_categories_come_from_raw_data_when_missing_on_top():
    place = {"category": "Coffee shop", "rawData": '{"categories": ["Coffee shop", "Tea house"]}'}
    assert google_categories(place) == ["Coffee shop", "Tea house"]
    assert derive_sub_types(google_categories(place), "FOOD_AND_DRINK") == ["CAFE", "TEA"]


def test_place_group_is_one_of_three_and_whole_words_only():
    from place_classification import current_group, derive_place_group
    assert derive_place_group(["Vietnamese restaurant"]) == "FOOD_AND_DRINK"
    assert derive_place_group(["Hotel", "Restaurant"]) == "ACCOMMODATION"
    assert derive_place_group(["Barber shop"]) == "ATTRACTIONS"
    assert derive_place_group(["Community center"]) == "ATTRACTIONS"
    assert derive_place_group(["Bungalow"]) == "ATTRACTIONS"
    assert derive_place_group(["Buddhist temple"]) == "ATTRACTIONS"
    assert derive_place_group(["Tourist attraction", "Coffee shop"]) == "FOOD_AND_DRINK"
    assert current_group("NATURE_AND_OUTDOORS") == "ATTRACTIONS"
    assert current_group("FOOD_AND_DRINK") == "FOOD_AND_DRINK"
    assert current_group("") is None


def test_upload_payload_folds_legacy_group_but_keeps_its_kinds():
    from upload_to_api import format_place_for_api
    body = format_place_for_api({"placeId": "g1", "title": "Ba Na", "placeGroup": "NATURE_AND_OUTDOORS",
                                 "category": "Mountain peak", "latitude": 16.0, "longitude": 108.0})
    assert body["placeGroup"] == "ATTRACTIONS"
    assert body["subTypes"] == ["NATURE"]
