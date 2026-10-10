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


def test_google_natural_features_and_worship():
    for category in ["Nature preserve", "National park", "Mountain peak", "Woods", "Hiking area", "Waterfall"]:
        assert "NATURE" in derive_sub_types([category], "ATTRACTIONS"), category
    assert derive_sub_types(["National park"], "ATTRACTIONS") == ["NATURE"]
    assert derive_sub_types(["Beach"], "ATTRACTIONS") == ["BEACH"]
    assert derive_sub_types(["Scenic spot"], "ATTRACTIONS") == ["VIEWPOINT"]
    assert derive_sub_types(["Buddhist temple"], "ATTRACTIONS") == ["SPIRITUAL"]
    assert derive_sub_types(["Catholic church"], "ATTRACTIONS") == ["SPIRITUAL"]


def test_places_api_type_spelling_matches_too():
    assert derive_sub_types(["coffee_shop"], "FOOD_AND_DRINK") == ["CAFE"]
    assert derive_sub_types(["nature_preserve"], "ATTRACTIONS") == ["NATURE"]
    assert derive_sub_types(["observation_deck"], "ATTRACTIONS") == ["VIEWPOINT"]


def test_historical_landmark_is_heritage_not_landmark():
    assert derive_sub_types(["Historical landmark"], "ATTRACTIONS") == ["HERITAGE"]
    assert derive_sub_types(["Cultural landmark"], "ATTRACTIONS") == ["HERITAGE"]
    assert derive_sub_types(["Bridge"], "ATTRACTIONS") == ["LANDMARK"]


def test_generic_tourist_attraction_falls_back_to_landmark():
    assert derive_sub_types(["Tourist attraction"], "ATTRACTIONS") == ["LANDMARK"]
    assert derive_sub_types(["Tourist attraction", "Museum"], "ATTRACTIONS") == ["MUSEUM"]
    assert derive_sub_types(["Tourist attraction"], "FOOD_AND_DRINK") == []


def test_parks_that_are_not_parks():
    for category in ["Car park", "Industrial park", "RV park", "Garden center"]:
        assert "PARK" not in derive_sub_types([category], "ATTRACTIONS"), category
    assert derive_sub_types(["City park"], "ATTRACTIONS") == ["PARK"]
    assert derive_sub_types(["Water park"], "ATTRACTIONS") == ["THEME_PARK"]


def test_entertainment_venues():
    for category in ["Night club", "Water puppet theater", "Live music venue", "Zoo", "Karaoke"]:
        assert derive_sub_types([category], "ATTRACTIONS") == ["ENTERTAINMENT"], category


def test_google_food_and_drink_types():
    assert derive_sub_types(["Coffee shop"], "FOOD_AND_DRINK") == ["CAFE"]
    assert derive_sub_types(["Bubble tea store"], "FOOD_AND_DRINK") == ["TEA"]
    assert derive_sub_types(["Tea store"], "FOOD_AND_DRINK") == []
    assert derive_sub_types(["Pho restaurant"], "FOOD_AND_DRINK") == ["RESTAURANT"]
    assert derive_sub_types(["Noodle shop"], "FOOD_AND_DRINK") == ["STREET_FOOD"]
    assert derive_sub_types(["Bar & grill"], "FOOD_AND_DRINK") == ["RESTAURANT"]
    assert derive_sub_types(["Bar and grill"], "FOOD_AND_DRINK") == ["RESTAURANT"]
    assert derive_sub_types(["Ice cream shop"], "FOOD_AND_DRINK") == ["DESSERT"]
    assert derive_sub_types(["Oyster bar restaurant"], "FOOD_AND_DRINK") == ["RESTAURANT", "SEAFOOD"]
    assert derive_sub_types(["Vegetarian restaurant"], "FOOD_AND_DRINK") == ["RESTAURANT", "VEGETARIAN"]
    assert derive_sub_types(["Cocktail bar"], "FOOD_AND_DRINK") == ["BAR"]
    assert derive_sub_types(["Beer garden"], "FOOD_AND_DRINK") == ["BAR"]


def test_vietnamese_category_names():
    assert derive_sub_types(["Quán cà phê"], "FOOD_AND_DRINK") == ["CAFE"]
    assert derive_sub_types(["Nhà hàng hải sản"], "FOOD_AND_DRINK") == ["RESTAURANT", "SEAFOOD"]
    assert derive_sub_types(["Công viên"], "ATTRACTIONS") == ["PARK"]
    assert derive_sub_types(["Bảo tàng"], "ATTRACTIONS") == ["MUSEUM"]
    assert derive_sub_types(["Điểm thu hút khách du lịch"], "ATTRACTIONS") == ["LANDMARK"]


def test_shops_with_a_food_word_are_not_food():
    from place_classification import derive_place_group
    assert derive_place_group(["Internet cafe"]) == "ATTRACTIONS"
    assert derive_place_group(["Food store"]) == "ATTRACTIONS"
    assert derive_place_group(["Karaoke bar"]) == "ATTRACTIONS"
    assert derive_place_group(["Beer garden"]) == "FOOD_AND_DRINK"
    assert derive_place_group(["Juice shop"]) == "FOOD_AND_DRINK"
    assert derive_place_group(["Bed & breakfast"]) == "ACCOMMODATION"
    assert derive_place_group(["Bed and breakfast"]) == "ACCOMMODATION"
    assert "CAFE" not in derive_sub_types(["Internet cafe"], "ATTRACTIONS")
