"""Place group and sub-types (backend ``PlaceGroup`` / ``PlaceSubType``) from Google categories.

The scraper reads Google Maps in English (``hl=en``), so a place's categories are Google's
English display names: "Coffee shop", "Buddhist temple", "Nature preserve", "Tourist
attraction". The same vocabulary exists as Places API types ("coffee_shop", "buddhist_temple",
"nature_preserve"); underscores are read as spaces so both spellings match. The rules below
were written against the Places API type table (Culture, Entertainment and Recreation, Food and
Drink, Natural Features, Places of Worship, Shopping, Lodging) plus the common Vietnamese names
for places scraped in Vietnamese.

Whole-word matches only: substring matching is what files "Barber shop" under bars.
``V218__place_sub_types.sql`` backfilled existing rows with an earlier, smaller rule set; these
rules are a superset of it.

The backend drops kinds that do not fit the place group, and only fills a place that has no
sub-types yet, so this module never needs to know what an operator chose.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

# (code, pattern, exclude pattern, legacy place group that implies the kind on its own).
# Order is PlaceSubType declaration order.
_RULES: list[tuple[str, str, str | None, str | None]] = [
    # --- Attractions -----------------------------------------------------------------------
    ("NATURE",
     r"nature|nature preserve|nature reserve|national park|state park|wildlife park|wildlife refuge|"
     r"wildlife and safari park|geopark|mountain|mountain peak|mountain pass|waterfall|lake|lagoon|"
     r"forest|woods|cave|hiking area|hiking trail|island|river|stream|bay|valley|canyon|hot spring|"
     r"rice terrace|wetland|mangrove|sand dune|desert|volcano|"
     r"thác|núi|đèo|hang động|khu bảo tồn|vườn quốc gia|rừng",
     None, "NATURE_AND_OUTDOORS"),
    ("BEACH", r"beach|bãi biển", None, None),
    ("PARK",
     r"park|city park|garden|botanical garden|picnic ground|dog park|skateboard park|cycling park|công viên",
     r"(amusement|theme|water|national|state|wildlife|safari|industrial|business|technology|office|"
     r"science|car|rv|trailer|mobile home|holiday|caravan)\s+park|beer garden|garden center",
     None),
    ("VIEWPOINT",
     r"scenic spot|scenic point|observation deck|observation tower|viewpoint|vista point|lookout|"
     r"điểm ngắm cảnh",
     None, None),
    ("SPIRITUAL",
     r"temple|buddhist temple|hindu temple|pagoda|church|catholic church|cathedral|basilica|chapel|"
     r"shrine|shinto shrine|mosque|synagogue|monastery|abbey|convent|place of worship|"
     r"religious destination|chùa|đền|nhà thờ|miếu|thánh thất",
     None, None),
    ("MUSEUM",
     r"museum|art museum|history museum|art gallery|gallery|art center|planetarium|bảo tàng",
     None, None),
    ("HERITAGE",
     r"historical landmark|historical place|historic site|cultural landmark|heritage|heritage building|"
     r"monument|memorial|war memorial|citadel|palace|fortress|fort|castle|ruins|mausoleum|"
     r"archaeological site|di tích|lăng|thành cổ",
     None, None),
    ("LANDMARK",
     r"landmark|tower|bridge|square|plaza|fountain|sculpture|statue",
     r"(historical|cultural)\s+landmark|(office|cell|radio|transmission|water)\s+tower",
     None),
    ("SHOPPING",
     r"shopping mall|mall|outlet mall|shopping center|shopping centre|department store|souvenir store|"
     r"souvenir shop|gift shop|handicraft|handicrafts store|craft store|boutique|trung tâm thương mại",
     None, None),
    ("MARKET",
     r"market|night market|farmers market|flea market|floating market|wet market|chợ",
     r"stock market|market research",
     None),
    ("ENTERTAINMENT",
     r"entertainment|cinema|movie theater|theater|theatre|performing arts|performing arts theater|"
     r"water puppet theater|puppet theater|opera house|concert hall|live music venue|comedy club|"
     r"night club|nightclub|karaoke|bowling alley|escape room|casino|arcade|video arcade|"
     r"amusement center|go kart track|go karting venue|paintball center|laser tag center|"
     r"indoor playground|miniature golf course|circus|zoo|aquarium",
     None, None),
    ("THEME_PARK",
     r"amusement park|theme park|water park|roller coaster|ferris wheel|khu vui chơi",
     None, None),
    # --- Food and drink --------------------------------------------------------------------
    ("RESTAURANT",
     r"restaurant|bistro|diner|brasserie|steak house|steakhouse|buffet|gastropub|bar and grill|"
     r"fine dining|nhà hàng",
     None, None),
    ("STREET_FOOD",
     r"street food|food stall|food court|hawker|hawker stall|hawker centre|eatery|snack bar|"
     r"noodle shop|sandwich shop|banh mi|bánh mì|kebab shop|hot dog stand|takeout restaurant|"
     r"meal takeaway|quán ăn|quán ăn vặt",
     None, None),
    ("CAFE",
     r"cafe|café|coffee|coffee shop|coffee stand|coffee roastery|espresso bar|cat cafe|dog cafe|cà phê",
     r"internet cafe|internet café|coffee machine|coffee store|coffee wholesaler|coffee manufacturer",
     None),
    ("TEA",
     r"tea|tea house|teahouse|bubble tea|bubble tea store|milk tea|trà|trà sữa",
     r"(?<!bubble )(?<!milk )tea (store|wholesaler|manufacturer|exporter)",
     None),
    ("BAR",
     r"bar|pub|irish pub|beer|beer hall|beer garden|brewery|brewpub|gastropub|cocktail|cocktail bar|"
     r"wine bar|lounge|lounge bar|sports bar|hookah bar|izakaya|quán bar|quán nhậu",
     r"(snack|juice|espresso|salad|sushi|noodle|coffee|tea|dessert|oyster|smoothie|milk|nail|"
     r"brow|lash|candy)\s+bar|bar and grill|beer (store|distributor|wholesaler)",
     None),
    ("BAKERY",
     r"bakery|patisserie|pastry shop|cake shop|bagel shop|tiệm bánh",
     None, None),
    ("DESSERT",
     r"dessert|dessert shop|dessert restaurant|ice cream|ice cream shop|gelato|frozen yogurt|yogurt|"
     r"sweets|confectionery|candy store|chocolate shop|donut|donut shop|acai shop|chè|kem",
     None, None),
    ("SEAFOOD",
     r"seafood|seafood restaurant|oyster|oyster bar|crab|crab house|fish restaurant|fish and chips|"
     r"hải sản",
     None, None),
    ("VEGETARIAN", r"vegetarian|vegetarian restaurant|quán chay|chay", None, None),
    ("VEGAN", r"vegan|vegan restaurant", None, None),
    ("HALAL", r"halal|halal restaurant", None, None),
]


def _normalize(category: str) -> str:
    """Google's display name ("Bar & grill") or its Places API type ("bar_and_grill") in one
    comparable form."""
    text = category.replace("_", " ").replace("&", " and ")
    return re.sub(r"\s+", " ", text).strip()


def _words(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)


_COMPILED = [
    (code, _words(pattern), _words(exclude) if exclude else None, group_hint)
    for code, pattern, exclude, group_hint in _RULES
]

# Backend PlaceGroup since V219: everything that is not food or a stay is an attraction.
FOOD_AND_DRINK = "FOOD_AND_DRINK"
ACCOMMODATION = "ACCOMMODATION"
ATTRACTIONS = "ATTRACTIONS"
LEGACY_ATTRACTION_GROUPS = frozenset({"CULTURE_AND_HERITAGE", "NATURE_AND_OUTDOORS", "SHOPPING_AND_MARKET", "OTHER"})

_FOOD = _words(
    r"restaurant|cafe|café|coffee|food|eatery|bistro|diner|brasserie|buffet|steak house|noodle|pho|"
    r"bakery|patisserie|pastry|cake shop|bar|pub|beer|brewery|brewpub|gastropub|izakaya|tea house|"
    r"teahouse|bubble tea|juice|smoothie|dessert|ice cream|gelato|donut|snack|hawker|deli|"
    r"food court|quán|nhà hàng|cà phê|trà sữa")
# Shops and services that carry a food word but are not a place to eat or drink.
_NOT_FOOD = _words(
    r"internet cafe|internet café|food (store|bank|products supplier|manufacturer|processing company|"
    r"distributor|delivery|supplier)|health food store|pet food|coffee (machine|store|wholesaler|"
    r"manufacturer)|(?<!bubble )(?<!milk )tea (store|wholesaler|manufacturer)|beer (store|distributor|wholesaler)|"
    r"(nail|brow|lash|karaoke) bar|juice (supplier|manufacturer)")
_STAY = _words(
    r"hotel|resort|resort hotel|hostel|accommodation|lodging|motel|guest house|guesthouse|homestay|"
    r"inn|villa|bed and breakfast|farmstay|cottage|campground|apartment hotel|serviced apartment|"
    r"khách sạn|nhà nghỉ")

# A sightseeing place Google names only generically ("Tourist attraction") is still somewhere
# to see: it is filed as a landmark rather than left out of every filter.
_GENERIC_ATTRACTION = _words(
    r"tourist attraction|point of interest|attraction|visitor center|visitor centre|"
    r"điểm thu hút khách du lịch|khu du lịch")


def _is_food(text: str) -> bool:
    return bool(_FOOD.search(_NOT_FOOD.sub(" ", text)))


def current_group(group: str | None) -> str | None:
    """A stored or scraped group in today's three-value form; legacy sightseeing codes fold in."""
    code = (group or "").strip().upper()
    if not code:
        return None
    return ATTRACTIONS if code in LEGACY_ATTRACTION_GROUPS else code


def derive_place_group(categories: Iterable[str]) -> str:
    """FOOD_AND_DRINK, ACCOMMODATION or ATTRACTIONS from Google categories, primary one first.
    Whole words only: "Barber shop" is not a bar and "Community center" is not food."""
    texts = [_normalize(str(category)) for category in categories if category]
    for text in texts[:1] or texts:
        if _is_food(text):
            return FOOD_AND_DRINK
        if _STAY.search(text):
            return ACCOMMODATION
    for text in texts[1:]:
        if _STAY.search(text):
            return ACCOMMODATION
        if _is_food(text):
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
    texts = [_normalize(str(category)) for category in categories if category]
    group = (place_group or "").upper()
    kinds: list[str] = []
    for code, pattern, exclude, group_hint in _COMPILED:
        matched = group_hint is not None and group == group_hint
        for text in texts:
            if pattern.search(exclude.sub(" ", text) if exclude else text):
                matched = True
                break
        if matched:
            kinds.append(code)
    if not kinds and group in _GROUP_FALLBACK:
        kinds.append(_GROUP_FALLBACK[group])
    is_sightseeing = group == ATTRACTIONS or group in LEGACY_ATTRACTION_GROUPS
    if not kinds and is_sightseeing and any(_GENERIC_ATTRACTION.search(text) for text in texts):
        kinds.append("LANDMARK")
    return kinds
