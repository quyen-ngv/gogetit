from unittest.mock import Mock, patch

import backend_place_search
import social_location_extractor
from backend_place_search import build_map_searcher

BACKEND_URL = "http://goroute-app:8080/v1/api/internal/place-search/text"


def _backend_answer(status_code=200, data=None):
    response = Mock(status_code=status_code)
    response.ok = 200 <= status_code < 300
    response.json.return_value = {"meta": {"code": 200}, "data": data}
    return response


def test_scrape_or_missing_url_keeps_the_browser_search():
    assert build_map_searcher(provider="SCRAPE", url=BACKEND_URL, token="t", headless=True) is None
    assert build_map_searcher(provider=None, url=BACKEND_URL, token="t", headless=True) is None
    assert build_map_searcher(provider="GOOGLE", url=None, token="t", headless=True) is None
    assert build_map_searcher(provider="GOOGLE", url="  ", token="t", headless=True) is None


def test_google_asks_the_backend_with_the_callback_token():
    data = {"success": True, "query": "Cong Ca Phe", "count": 1, "candidates": [{"placeId": "ChIJ1"}]}
    searcher = build_map_searcher(provider="google", url=BACKEND_URL, token="shared-secret", headless=True)

    with patch("backend_place_search.requests.post", return_value=_backend_answer(data=data)) as post, patch(
        "backend_place_search.search_google_maps"
    ) as browser:
        result = searcher("Cong Ca Phe", 3)

    assert result == data
    assert post.call_args.args[0] == BACKEND_URL
    assert post.call_args.kwargs["json"] == {"query": "Cong Ca Phe", "limit": 3}
    assert post.call_args.kwargs["headers"]["X-Internal-Token"] == "shared-secret"
    browser.assert_not_called()


def test_backend_failure_falls_back_to_the_browser_search():
    searcher = build_map_searcher(provider="GOOGLE", url=BACKEND_URL, token="t", headless=False)
    browser_result = {"success": True, "count": 0, "candidates": []}

    with patch("backend_place_search.requests.post", return_value=_backend_answer(status_code=503)), patch(
        "backend_place_search.search_google_maps", return_value=browser_result
    ) as browser:
        result = searcher("Pho Hoa", 3)

    assert result is browser_result
    browser.assert_called_once_with("Pho Hoa", limit=3, headless=False)


def test_backend_answer_without_success_falls_back_too():
    searcher = build_map_searcher(provider="GOOGLE", url=BACKEND_URL, token="t", headless=True)

    with patch("backend_place_search.requests.post", return_value=_backend_answer(data=None)), patch(
        "backend_place_search.search_google_maps", return_value={"success": True, "candidates": []}
    ) as browser:
        searcher("Pho Hoa", 3)

    browser.assert_called_once()


def test_enrichment_uses_the_given_searcher_instead_of_the_browser():
    searcher = Mock(return_value={"success": True, "candidates": []})
    candidate = {"name": "Pho Hoa", "query": "Pho Hoa", "city_hint": "Ho Chi Minh"}

    with patch("social_location_extractor.search_google_maps") as browser:
        enriched = social_location_extractor.enrich_with_map_search(
            [candidate], search_limit=1, headless=True, map_searcher=searcher
        )

    searcher.assert_called_once_with("Pho Hoa Ho Chi Minh", 3)
    browser.assert_not_called()
    assert enriched[0]["resolutionStatus"] == "UNRESOLVED"


def test_enrichment_without_a_searcher_still_drives_the_browser():
    candidate = {"name": "Pho Hoa", "query": "Pho Hoa"}

    with patch(
        "social_location_extractor.search_google_maps", return_value={"success": True, "candidates": []}
    ) as browser:
        social_location_extractor.enrich_with_map_search([candidate], search_limit=1, headless=True)

    browser.assert_called_once_with("Pho Hoa", limit=3, headless=True)


def test_module_exposes_the_provider_names_the_backend_sends():
    assert backend_place_search.MAP_SEARCH_PROVIDER_SCRAPE == "SCRAPE"
    assert backend_place_search.MAP_SEARCH_PROVIDER_GOOGLE == "GOOGLE"
