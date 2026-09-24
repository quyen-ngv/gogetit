"""Legacy 64-province crawl zones and configurable food search queries."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Region:
    code: str
    name: str
    priority: int
    tourist_areas: tuple[str, ...]


REGIONS: tuple[Region, ...] = (
    Region("hanoi", "Hà Nội", 100, ("Phố cổ Hà Nội", "Hồ Hoàn Kiếm", "Tây Hồ", "Ba Đình")),
    Region("hochiminh", "TP Hồ Chí Minh", 100, ("Quận 1", "Quận 3", "Chợ Lớn", "Thảo Điền")),
    Region("danang", "Đà Nẵng", 100, ("Mỹ Khê", "Sơn Trà", "Ngũ Hành Sơn", "Hải Châu")),
    Region("quangnam", "Quảng Nam", 100, ("Hội An", "Mỹ Sơn", "Cù Lao Chàm", "Tam Kỳ")),
    Region("thuathienhue", "Thừa Thiên Huế", 100, ("Đại Nội Huế", "Phố đi bộ Huế", "Lăng Cô", "Thuận An")),
    Region("quangninh", "Quảng Ninh", 100, ("Hạ Long", "Bãi Cháy", "Vân Đồn", "Cô Tô", "Yên Tử")),
    Region("khanhhoa", "Khánh Hòa", 100, ("Nha Trang", "Cam Ranh", "Ninh Hòa", "Dốc Lết")),
    Region("kiengiang", "Kiên Giang", 100, ("Phú Quốc", "Rạch Giá", "Hà Tiên", "Nam Du")),
    Region("lamdong", "Lâm Đồng", 100, ("Đà Lạt", "Bảo Lộc", "Hồ Tuyền Lâm", "Trại Mát")),
    Region("laocai", "Lào Cai", 100, ("Sa Pa", "Bắc Hà", "Y Tý", "thành phố Lào Cai")),
    Region("ninhbinh", "Ninh Bình", 100, ("Tràng An", "Tam Cốc", "Bái Đính", "Cúc Phương")),
    Region("baria-vungtau", "Bà Rịa - Vũng Tàu", 95, ("Vũng Tàu", "Hồ Tràm", "Long Hải", "Côn Đảo")),
    Region("binhthuan", "Bình Thuận", 95, ("Mũi Né", "Phan Thiết", "Phú Quý", "Bàu Trắng")),
    Region("quangbinh", "Quảng Bình", 95, ("Phong Nha", "Đồng Hới", "Suối Moọc", "Bảo Ninh")),
    Region("ninhthuan", "Ninh Thuận", 95, ("Phan Rang", "Vĩnh Hy", "Ninh Chữ", "Núi Chúa")),
    Region("laichau", "Lai Châu", 90, ("Tam Đường", "Sìn Hồ", "Tân Uyên", "đèo Ô Quy Hồ")),
    Region("hagiang", "Hà Giang", 95, ("Đồng Văn", "Mèo Vạc", "Quản Bạ", "Hoàng Su Phì")),
    Region("caobang", "Cao Bằng", 95, ("thác Bản Giốc", "Trùng Khánh", "Pác Bó", "thành phố Cao Bằng")),
    Region("sonla", "Sơn La", 90, ("Mộc Châu", "Tà Xùa", "thành phố Sơn La", "Quỳnh Nhai")),
    Region("dienbien", "Điện Biên", 85, ("Điện Biên Phủ", "Mường Phăng", "Tủa Chùa", "hồ Pá Khoang")),
    Region("hoabinh", "Hòa Bình", 85, ("Mai Châu", "hồ Hòa Bình", "Kim Bôi", "Lương Sơn")),
    Region("phutho", "Phú Thọ", 85, ("Đền Hùng", "Việt Trì", "Thanh Thủy", "Xuân Sơn")),
    Region("yenbai", "Yên Bái", 90, ("Mù Cang Chải", "Nghĩa Lộ", "hồ Thác Bà", "Trạm Tấu")),
    Region("haiphong", "Hải Phòng", 90, ("Cát Bà", "Đồ Sơn", "trung tâm Hải Phòng", "Lan Hạ")),
    Region("cantho", "Cần Thơ", 90, ("Ninh Kiều", "Cái Răng", "Phong Điền", "Bình Thủy")),
    Region("angiang", "An Giang", 90, ("Châu Đốc", "Núi Sam", "Núi Cấm", "Long Xuyên")),
    Region("camau", "Cà Mau", 90, ("Đất Mũi", "thành phố Cà Mau", "U Minh", "Sông Đốc")),
    Region("binhdinh", "Bình Định", 90, ("Quy Nhơn", "Kỳ Co", "Eo Gió", "Phù Cát")),
    Region("phuyen", "Phú Yên", 90, ("Tuy Hòa", "Gành Đá Đĩa", "Bãi Xép", "Sông Cầu")),
    Region("quangtri", "Quảng Trị", 80, ("Đông Hà", "Cửa Tùng", "Khe Sanh", "đảo Cồn Cỏ")),
    Region("thanhhoa", "Thanh Hóa", 85, ("Sầm Sơn", "Pù Luông", "thành phố Thanh Hóa", "Hải Tiến")),
    Region("nghean", "Nghệ An", 85, ("Cửa Lò", "Vinh", "Nam Đàn", "Con Cuông")),
    Region("hatinh", "Hà Tĩnh", 80, ("Thiên Cầm", "thành phố Hà Tĩnh", "Hương Sơn", "Kỳ Anh")),
    Region("quangngai", "Quảng Ngãi", 85, ("Lý Sơn", "Sa Huỳnh", "thành phố Quảng Ngãi", "Mỹ Khê Quảng Ngãi")),
    Region("daklak", "Đắk Lắk", 85, ("Buôn Ma Thuột", "Buôn Đôn", "hồ Lắk", "Krông Ana")),
    Region("daknong", "Đắk Nông", 80, ("Tà Đùng", "Gia Nghĩa", "Đray Sáp", "Nam Nung")),
    Region("gialai", "Gia Lai", 85, ("Pleiku", "Biển Hồ", "Chư Đăng Ya", "Ayun Pa")),
    Region("kontum", "Kon Tum", 85, ("Măng Đen", "thành phố Kon Tum", "Ngọc Hồi", "Đăk Tô")),
    Region("tayninh", "Tây Ninh", 85, ("Núi Bà Đen", "Tòa Thánh Tây Ninh", "hồ Dầu Tiếng", "Trảng Bàng")),
    Region("dongnai", "Đồng Nai", 80, ("Biên Hòa", "Nam Cát Tiên", "Long Khánh", "hồ Trị An")),
    Region("binhduong", "Bình Dương", 75, ("Thủ Dầu Một", "Dĩ An", "Thuận An", "Bến Cát")),
    Region("binhphuoc", "Bình Phước", 70, ("Đồng Xoài", "Bù Gia Mập", "Sóc Bom Bo", "Phước Long")),
    Region("tiengiang", "Tiền Giang", 80, ("Mỹ Tho", "Cái Bè", "Gò Công", "Thới Sơn")),
    Region("bentre", "Bến Tre", 80, ("thành phố Bến Tre", "Châu Thành", "Cồn Phụng", "Ba Tri")),
    Region("vinhlong", "Vĩnh Long", 75, ("thành phố Vĩnh Long", "An Bình", "Mang Thít", "Trà Ôn")),
    Region("travinh", "Trà Vinh", 75, ("thành phố Trà Vinh", "Ao Bà Om", "Cầu Kè", "Duyên Hải")),
    Region("soctrang", "Sóc Trăng", 75, ("thành phố Sóc Trăng", "Ngã Năm", "Vĩnh Châu", "Cù Lao Dung")),
    Region("baclieu", "Bạc Liêu", 75, ("thành phố Bạc Liêu", "Nhà Mát", "Gành Hào", "Hồng Dân")),
    Region("haugiang", "Hậu Giang", 70, ("Vị Thanh", "Ngã Bảy", "Phụng Hiệp", "Long Mỹ")),
    Region("dongthap", "Đồng Tháp", 80, ("Sa Đéc", "Cao Lãnh", "Tràm Chim", "Gáo Giồng")),
    Region("longan", "Long An", 70, ("Tân An", "Tân Lập", "Đức Hòa", "Cần Giuộc")),
    Region("langson", "Lạng Sơn", 80, ("thành phố Lạng Sơn", "Mẫu Sơn", "Đồng Đăng", "Bắc Sơn")),
    Region("backan", "Bắc Kạn", 80, ("hồ Ba Bể", "thành phố Bắc Kạn", "Chợ Đồn", "Na Rì")),
    Region("thainguyen", "Thái Nguyên", 75, ("thành phố Thái Nguyên", "hồ Núi Cốc", "Đại Từ", "Phổ Yên")),
    Region("tuyenquang", "Tuyên Quang", 75, ("Na Hang", "thành phố Tuyên Quang", "Tân Trào", "Lâm Bình")),
    Region("bacgiang", "Bắc Giang", 75, ("Tây Yên Tử", "thành phố Bắc Giang", "Lục Ngạn", "Sơn Động")),
    Region("bacninh", "Bắc Ninh", 70, ("thành phố Bắc Ninh", "Từ Sơn", "Đền Đô", "làng Diềm")),
    Region("haiduong", "Hải Dương", 70, ("thành phố Hải Dương", "Côn Sơn Kiếp Bạc", "Chí Linh", "Thanh Hà")),
    Region("hungyen", "Hưng Yên", 70, ("thành phố Hưng Yên", "Phố Hiến", "Văn Giang", "Mỹ Hào")),
    Region("hanam", "Hà Nam", 75, ("Phủ Lý", "Tam Chúc", "Địa Tạng Phi Lai", "Lý Nhân")),
    Region("namdinh", "Nam Định", 75, ("thành phố Nam Định", "Thịnh Long", "Xuân Thủy", "Phủ Dầy")),
    Region("thaibinh", "Thái Bình", 70, ("thành phố Thái Bình", "Cồn Vành", "Cồn Đen", "Tiền Hải")),
    Region("vinhphuc", "Vĩnh Phúc", 80, ("Tam Đảo", "Vĩnh Yên", "Đại Lải", "Tây Thiên")),
    Region("hatay", "Hà Tây", 80, ("Ba Vì", "Sơn Tây", "Chùa Hương", "làng cổ Đường Lâm")),
)


def selected_regions(region_codes: list[str] | None = None) -> list[Region]:
    allowed = {code.strip().lower() for code in region_codes or [] if code.strip()}
    regions = [region for region in REGIONS if not allowed or region.code in allowed]
    return sorted(regions, key=lambda region: (-region.priority, region.name))


SEARCH_CONFIG_PATH = Path(__file__).with_name("nationwide_search_config.json")
LOCATION_DEFAULT_QUERIES: tuple[str, ...] = (
    "quán ăn",
    "nhà hàng",
    "restaurant",
    "food",
    "cafe",
)


def load_search_config() -> dict:
    with SEARCH_CONFIG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _expand_query(template: str, region: Region, foods: list[str]) -> list[str]:
    template = " ".join(template.strip().split())
    if not template:
        return []
    areas = region.tourist_areas if "{area}" in template else ("",)
    food_values = tuple(foods) if "{food}" in template else ("",)
    queries: list[str] = []
    for area in areas:
        for food in food_values:
            query = template.format(region=region.name, area=area, food=food).strip(" ,")
            if not any(token in template for token in ("{region}", "{area}")):
                query = f"{query}, {region.name}"
            queries.append(query)
    return queries


def region_queries(
    region: Region,
    max_queries: int,
    *,
    custom_queries: list[str] | None = None,
    query_mode: str = "APPEND",
    include_regional_specialties: bool = True,
    include_tourist_areas: bool = True,
) -> list[str]:
    config = load_search_config()
    foods = list((config.get("regionalFoods") or {}).get(region.code) or [])
    defaults: list[str] = []
    if include_tourist_areas:
        for template in config.get("touristAreaQueryTemplates") or []:
            defaults.extend(_expand_query(str(template), region, foods))
    for template in config.get("provinceQueryTemplates") or []:
        defaults.extend(_expand_query(str(template), region, foods))
    if include_regional_specialties:
        for template in config.get("specialtyQueryTemplates") or []:
            defaults.extend(_expand_query(str(template), region, foods))

    custom: list[str] = []
    for template in custom_queries or []:
        custom.extend(_expand_query(template, region, foods))
    combined = custom if query_mode.upper() == "REPLACE" else custom + defaults
    return list(dict.fromkeys(query for query in combined if query))[:max_queries]


def location_queries(
    max_queries: int,
    *,
    custom_queries: list[str] | None = None,
    query_mode: str = "APPEND",
) -> list[str]:
    """Return food queries that are biased by a latitude/longitude search center."""
    custom = []
    for template in custom_queries or []:
        query = str(template).replace("{region}", "").replace("{area}", "").replace("{food}", "")
        query = " ".join(query.strip(" ,").split())
        if query:
            custom.append(query)
    defaults = list(LOCATION_DEFAULT_QUERIES)
    combined = custom if query_mode.upper() == "REPLACE" else custom + defaults
    return list(dict.fromkeys(query for query in combined if query))[:max_queries]
