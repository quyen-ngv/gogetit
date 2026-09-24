"""Search Google Maps for place candidates without scraping reviews."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote_plus, urlparse, parse_qs

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from run_job import close_driver, dismiss_cookies, extract_coords, extract_place_id, js_place_detail, setup_driver


def _search_url(query: str, latitude: float | None, longitude: float | None, zoom: int) -> str:
    encoded = quote_plus(query.strip())
    if latitude is not None and longitude is not None:
        return f"https://www.google.com/maps/search/{encoded}/@{latitude},{longitude},{zoom}z?hl=en"
    return f"https://www.google.com/maps/search/{encoded}?hl=en"


def _extract_cid(url: str) -> str:
    parsed = urlparse(url)
    cid = parse_qs(parsed.query).get("cid", [""])[0].strip()
    if cid:
        return cid
    match = re.search(r"0x[0-9a-fA-F]+:0x([0-9a-fA-F]+)", url)
    if match:
        try:
            return str(int(match.group(1), 16))
        except ValueError:
            return match.group(1)
    return ""


def _compact_lines(text: str) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = " ".join(raw.split())
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return lines


def _parse_rating(lines: list[str]) -> tuple[str, str]:
    rating = ""
    reviews = ""
    for line in lines:
        if not rating and re.search(r"\b[1-5][.,]\d\b", line):
            rating = line
        if not reviews and re.search(r"\b(\d[\d.,]*)\s+(reviews?|bài đánh giá|đánh giá)\b", line, re.I):
            reviews = line
    return rating, reviews


def _candidate_from_url(rank: int, title: str, url: str, raw_text: str = "") -> dict[str, Any]:
    lat, lng = extract_coords(url)
    place_id = extract_place_id(url, url)
    lines = _compact_lines(raw_text)
    rating_text, review_text = _parse_rating(lines)
    return {
        "rank": rank,
        "title": title.strip(),
        "googleMapsLink": url,
        "resolvedUrl": url,
        "placeId": place_id,
        "cid": _extract_cid(url),
        "latitude": lat,
        "longitude": lng,
        "ratingText": rating_text,
        "reviewText": review_text,
        "rawText": "\n".join(lines[:12]),
    }


def _extract_result_cards(driver, limit: int) -> list[dict[str, Any]]:
    raw_cards = driver.execute_script(
        """
        const text = (el) => (el && el.textContent || '').trim();
        const attr = (el, name) => (el && el.getAttribute(name) || '').trim();
        const cards = Array.from(document.querySelectorAll('div.Nv2PK, div[role="article"]'));
        const results = [];
        const seen = new Set();

        for (const card of cards) {
          const link = card.querySelector('a.hfpxzc[href], a[href*="/maps/place/"], a[href*="maps.google.com"]');
          const href = attr(link, 'href');
          if (!href || seen.has(href)) continue;
          seen.add(href);
          const title = attr(link, 'aria-label') || text(card.querySelector('.qBF1Pd')) || text(card).split('\\n')[0] || '';
          results.push({title, href, rawText: text(card)});
        }

        if (results.length === 0) {
          for (const link of document.querySelectorAll('a.hfpxzc[href], a[href*="/maps/place/"]')) {
            const href = attr(link, 'href');
            if (!href || seen.has(href)) continue;
            seen.add(href);
            results.push({title: attr(link, 'aria-label') || text(link), href, rawText: text(link)});
          }
        }

        return results;
        """
    ) or []

    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in raw_cards:
        url = str(item.get("href") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        candidates.append(_candidate_from_url(len(candidates) + 1, title, url, str(item.get("rawText") or "")))
        if len(candidates) >= limit:
            break
    return candidates


def search_google_maps(
    query: str,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
    zoom: int = 14,
    limit: int = 5,
    max_scrolls: int = 20,
    headless: bool = True,
) -> dict[str, Any]:
    """Search Google Maps and return lightweight candidate URLs/metadata."""
    cleaned_query = " ".join(query.strip().split())
    if not cleaned_query:
        raise ValueError("query is required")

    driver = setup_driver(headless)
    url = _search_url(cleaned_query, latitude, longitude, zoom)
    try:
        driver.get("https://www.google.com")
        time.sleep(1.0)
        dismiss_cookies(driver)

        driver.get(url)
        try:
            WebDriverWait(driver, 8).until(
                EC.any_of(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "a.hfpxzc[href], div.Nv2PK")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, "h1.DUwDvf, h1")),
                )
            )
        except TimeoutException:
            pass
        time.sleep(2.0)
        dismiss_cookies(driver)

        candidates_by_url: dict[str, dict[str, Any]] = {}
        idle_scrolls = 0
        for _ in range(max(1, max_scrolls)):
            before = len(candidates_by_url)
            for candidate in _extract_result_cards(driver, limit):
                candidates_by_url.setdefault(candidate["googleMapsLink"], candidate)
            if len(candidates_by_url) >= limit:
                break
            idle_scrolls = idle_scrolls + 1 if len(candidates_by_url) == before else 0
            if idle_scrolls >= 3:
                break
            scrolled = driver.execute_script(
                """
                const feed = document.querySelector('div[role="feed"]')
                  || Array.from(document.querySelectorAll('div')).find(el =>
                    el.scrollHeight > el.clientHeight + 300 && el.querySelector('div.Nv2PK'));
                if (!feed) return false;
                feed.scrollTop = feed.scrollHeight;
                return true;
                """
            )
            if not scrolled:
                break
            time.sleep(1.2)
        candidates = list(candidates_by_url.values())
        for rank, candidate in enumerate(candidates, start=1):
            candidate["rank"] = rank
        if not candidates:
            detail = js_place_detail(driver, url, driver.current_url)
            title = detail.get("title") or detail.get("name") or ""
            if title:
                candidates = [
                    {
                        "rank": 1,
                        "title": title,
                        "googleMapsLink": detail.get("googleMapsLink") or detail.get("link") or driver.current_url,
                        "resolvedUrl": detail.get("resolved_url") or driver.current_url,
                        "placeId": detail.get("placeId") or detail.get("place_id") or "",
                        "cid": detail.get("cid") or "",
                        "latitude": detail.get("latitude"),
                        "longitude": detail.get("longitude"),
                        "category": detail.get("category") or "",
                        "address": detail.get("address") or "",
                        "ratingText": str(detail.get("reviewRating") or ""),
                        "reviewText": str(detail.get("reviewCount") or ""),
                        "rawText": "",
                    }
                ]

        return {
            "success": True,
            "query": cleaned_query,
            "searchUrl": url,
            "resolvedUrl": driver.current_url,
            "count": len(candidates),
            "candidates": candidates[:limit],
        }
    finally:
        try:
            close_driver(driver)
        except Exception:
            pass
