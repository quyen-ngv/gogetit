"""Google Maps URL validation and extraction."""

import re


def is_google_maps_url(text: str) -> bool:
    """Check if text contains a Google Maps URL."""
    patterns = [
        r"https?://(?:www\.)?google\.com/maps",
        r"https?://maps\.google\.com",
        r"https?://goo\.gl/maps",
        r"https?://maps\.app\.goo\.gl",
    ]
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def is_google_maps_viewport_url(url: str) -> bool:
    """A /maps/@lat,lng,zoom URL is a map viewport, not a place detail URL."""
    return bool(re.search(r"https?://(?:www\.)?google\.com/maps/@-?[\d.]+,-?[\d.]+,\d+(?:\.\d+)?z", url, re.IGNORECASE))


def extract_urls(text: str) -> list[str]:
    """Extract all Google Maps URLs from text."""
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    found_urls = re.findall(url_pattern, text)
    return [url for url in found_urls if is_google_maps_url(url)]
