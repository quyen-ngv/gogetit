"""Place sub-types (backend ``PlaceSubType``) derived from Google Maps categories.

Mirrors the rules of the backend backfill migration ``V218__place_sub_types.sql`` so a place
scraped today gets the same kinds as one backfilled from the database. Whole-word matches
only: substring matching is what files "Barber shop" under bars.

The backend drops kinds that do not fit the place group, and only fills a place that has no
sub-types yet, so this module never needs to know what an operator chose.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

# (code, pattern, exclude pattern, place group that implies the kind on its own).
# Order is PlaceSubType declaration order.
_RULES: list[tuple[str, str, str | None, str | None]] = [
    ("NATURE", r"nature|national park|nature preserve|mountain|mountain peak|waterfall|lake|forest|cave|hiking area|island|river|bay|valley|hot spring|rice terrace", None, "NATURE_AND_OUTDOORS"),
    ("BEACH", r"beach|bãi biển", None, None),
    ("PARK", r"park|garden|botanical garden", r"(amusement|theme|water)\s+park", None),
    ("VIEWPOINT", r"scenic spot|scenic point|observation deck|viewpoint|vista point|lookout", None, None),
    ("SPIRITUAL", r"temple|pagoda|church|cathedral|basilica|shrine|mosque|monastery|place of worship|chùa|đền|nhà thờ|miếu", None, None),
    ("MUSEUM", r"museum|art gallery|gallery", None, None),
    ("HERITAGE", r"historical landmark|historical place|heritage|monument|memorial|citadel|palace|fortress|castle|ruins|mausoleum", None, None),
    ("LANDMARK", r"landmark|tower|bridge|square|plaza", None, None),
    ("SHOPPING", r"shopping mall|mall|shopping center|department store|souvenir store|gift shop|boutique", None, None),
    ("MARKET", r"market|night market|chợ", None, None),
    ("ENTERTAINMENT", r"entertainment|cinema|movie theater|theater|theatre|performing arts|karaoke|bowling alley|escape room|casino|arcade|zoo|aquarium", None, None),
    ("THEME_PARK", r"amusement park|theme park|water park", None, None),
    ("RESTAURANT", r"restaurant", None, None),
    ("STREET_FOOD", r"street food|food stall|food court|hawker|eatery", None, None),
    ("CAFE", r"cafe|café|coffee|espresso bar|cà phê", None, None),
    ("TEA", r"tea|teahouse|bubble tea|milk tea|trà", None, None),
    ("BAR", r"bar|pub|beer|brewery|brewpub|cocktail|lounge|izakaya", r"(snack|juice|espresso|salad|sushi|noodle|coffee|tea|dessert|oyster|smoothie|milk)\s+bar", None),
    ("BAKERY", r"bakery|patisserie|pastry shop|cake shop", None, None),
    ("DESSERT", r"dessert|ice cream|gelato|frozen yogurt|sweets|confectionery|donut|chè", None, None),
    ("SEAFOOD", r"seafood|oyster|crab|fish restaurant|hải sản", None, None),
    ("VEGETARIAN", r"vegetarian|quán chay", None, None),
    ("VEGAN", r"vegan", None, None),
    ("HALAL", r"halal", None, None),
]

_COMPILED = [
    (code, re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE),
     re.compile(rf"\b{exclude}\b", re.IGNORECASE) if exclude else None, group_hint)
    for code, pattern, exclude, group_hint in _RULES
]

# Backend PlaceGroup since V219: everything that is not food or a stay is an attraction.
FOOD_AND_DRINK = "FOOD_AND_DRINK"
ACCOMMODATION = "ACCOMMODATION"
ATTRACTIONS = "ATTRACTIONS"
LEGACY_ATTRACTION_GROUPS = frozenset({"CULTURE_AND_HERITAGE", "NATURE_AND_OUTDOORS", "SHOPPING_AND_MARKET", "OTHER"})

_FOOD = re.compile(
    r"\b(?:restaurant|cafe|café|coffee|food|eatery|bistro|diner|noodle|pho|bakery|bar|pub|brewery|"
    r"tea house|teahouse|bubble tea|dessert|ice cream|quán|nhà hàng|cà phê)\b", re.IGNORECASE)
_STAY = re.compile(
    r"\b(?:hotel|resort|hostel|accommodation|lodging|motel|guest house|guesthouse|homestay|inn|villa|"
    r"apartment hotel|khách sạn|nhà nghỉ)\b", re.IGNORECASE)


def current_group(group: str | None) -> str | None:
    """A stored or scraped group in today's three-value form; legacy sightseeing codes fold in."""
    code = (group or "").strip().upper()
    if not code:
        return None
    return ATTRACTIONS if code in LEGACY_ATTRACTION_GROUPS else code


def derive_place_group(categories: Iterable[str]) -> str:
    """FOOD_AND_DRINK, ACCOMMODATION or ATTRACTIONS from Google categories, primary one first.
    Whole words only: "Barber shop" is not a bar and "Community center" is not food."""
    texts = [str(category) for category in categories if category]
    for text in texts[:1] or texts:
        if _FOOD.search(text):
            return FOOD_AND_DRINK
        if _STAY.search(text):
            return ACCOMMODATION
    for text in texts[1:]:
        if _STAY.search(text):
            return ACCOMMODATION
        if _FOOD.search(text):
            return FOOD_AND_DRINK
    return ATTRACTIONS


# The old group is the only signal left when no word matched (same as the migration).
_GROUP_FALLBACK = {"CULTURE_AND_HERITAGE": "HERITAGE", "SHOPPING_AND_MARKET": "SHOPPING"}


def google_categories(place: dict[str, Any]) -> list[str]:
    """Every Google category of a scraped place: the primary one plus the ``categories`` list,
    which older output files keep only inside ``rawData``."""
    values: list[Any] = [place.get("category")]
    values.extend(place.get("categories") or [])
    raw = place.get("rawData")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if isinstance(raw, dict):
        values.extend(raw.get("categories") or [])
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def derive_sub_types(categories: Iterable[str], place_group: str | None) -> list[str]:
    """Sub-type codes for a place, in PlaceSubType declaration order, without duplicates."""
    texts = [str(category) for category in categories if category]
    group = (place_group or "").upper()
    kinds: list[str] = []
    for code, pattern, exclude, group_hint in _COMPILED:
        matched = group_hint is not None and group == group_hint
        for text in texts:
            if pattern.search(text) and not (exclude and exclude.search(text)):
                matched = True
                break
        if matched:
            kinds.append(code)
    if not kinds and group in _GROUP_FALLBACK:
        kinds.append(_GROUP_FALLBACK[group])
    return kinds
