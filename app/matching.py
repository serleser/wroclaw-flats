"""Silnik filtrów zakupowych.

Jeden zestaw kryteriów (`criteria`) obsługuje dwie rzeczy naraz:
  * wyszukiwanie w dashboardzie  -> build_query()
  * dopasowanie alertu do nowej oferty -> matches()

Dzięki temu "zapisz ten filtr jako alert" działa 1:1 z tym, co widzisz na ekranie.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import Select, and_, func, or_, select

from .districts import normalize_text
from .models import Listing

# Kształt słownika kryteriów (wszystkie pola opcjonalne):
CRITERIA_KEYS = {
    "price_min", "price_max",
    "ppm_min", "ppm_max",              # cena za m2
    "area_min", "area_max",
    "rooms",                            # lista int, np. [2, 3]
    "market",                           # 'pierwotny' | 'wtorny' | 'oba'
    "seller_types",                     # ['wlasciciel','biuro','deweloper']
    "regions",                          # ['Krzyki', 'Fabryczna']
    "estates",                          # ['Jagodno', 'Gaj']
    "conditions",                       # ['do_zamieszkania', ...]
    "keywords_include",                 # ['balkon','garaz']  (wszystkie muszą wystąpić)
    "keywords_any",                     # ['winda','taras']   (wystarczy jedno)
    "keywords_exclude",                 # ['prowizja','poddasze']
    "sources",                          # ['olx','otodom']
    "only_deals",                       # bool
    "hide_duplicates",                  # bool (domyślnie True)
    "only_active",                      # bool (domyślnie True)
    "floor_min", "floor_max",
    "sort",                             # 'newest' | 'price_asc' | 'ppm_asc' | 'deal'
}


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [v for v in value if v not in (None, "")]
    return [value]


def _num(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ SQL part


def build_query(criteria: Dict[str, Any]) -> Select:
    """Buduje zapytanie SQL dla części kryteriów, które da się zrobić w bazie."""
    c = criteria or {}
    stmt = select(Listing)
    conds = []

    if c.get("only_active", True):
        conds.append(Listing.is_active.is_(True))
    if c.get("hide_duplicates", True):
        conds.append(Listing.is_duplicate.is_(False))

    price_min, price_max = _num(c.get("price_min")), _num(c.get("price_max"))
    if price_min is not None:
        conds.append(Listing.price >= price_min)
    if price_max is not None:
        conds.append(Listing.price <= price_max)

    ppm_min, ppm_max = _num(c.get("ppm_min")), _num(c.get("ppm_max"))
    if ppm_min is not None:
        conds.append(Listing.price_per_m2 >= ppm_min)
    if ppm_max is not None:
        conds.append(Listing.price_per_m2 <= ppm_max)

    area_min, area_max = _num(c.get("area_min")), _num(c.get("area_max"))
    if area_min is not None:
        conds.append(Listing.area >= area_min)
    if area_max is not None:
        conds.append(Listing.area <= area_max)

    floor_min, floor_max = _num(c.get("floor_min")), _num(c.get("floor_max"))
    if floor_min is not None:
        conds.append(Listing.floor >= floor_min)
    if floor_max is not None:
        conds.append(Listing.floor <= floor_max)

    rooms = [int(r) for r in _as_list(c.get("rooms")) if str(r).isdigit()]
    if rooms:
        # 5 w UI oznacza "5 i więcej"
        room_conds = [Listing.rooms == r for r in rooms if r < 5]
        if any(r >= 5 for r in rooms):
            room_conds.append(Listing.rooms >= 5)
        conds.append(or_(*room_conds))

    market = c.get("market")
    if market and market != "oba":
        conds.append(Listing.market == market)

    seller_types = _as_list(c.get("seller_types"))
    if seller_types:
        conds.append(Listing.seller_type.in_(seller_types))

    sources = _as_list(c.get("sources"))
    if sources:
        conds.append(Listing.source.in_(sources))

    conditions = _as_list(c.get("conditions"))
    if conditions:
        conds.append(Listing.condition.in_(conditions))

    regions = _as_list(c.get("regions"))
    estates = _as_list(c.get("estates"))
    if regions or estates:
        loc_conds = []
        if regions:
            loc_conds.append(Listing.region.in_(regions))
        if estates:
            loc_conds.append(Listing.estate.in_(estates))
            for e in estates:
                loc_conds.append(func.lower(Listing.location_raw).contains(str(e).lower()))
        conds.append(or_(*loc_conds))

    if c.get("only_deals"):
        conds.append(Listing.is_deal.is_(True))

    if conds:
        stmt = stmt.where(and_(*conds))

    sort = c.get("sort") or "newest"
    if sort == "price_asc":
        stmt = stmt.order_by(Listing.price.asc().nulls_last())
    elif sort == "price_desc":
        stmt = stmt.order_by(Listing.price.desc().nulls_last())
    elif sort == "ppm_asc":
        stmt = stmt.order_by(Listing.price_per_m2.asc().nulls_last())
    elif sort == "area_desc":
        stmt = stmt.order_by(Listing.area.desc().nulls_last())
    elif sort == "deal":
        stmt = stmt.order_by(Listing.deal_score.desc().nulls_last(), Listing.first_seen_at.desc())
    else:
        stmt = stmt.order_by(Listing.first_seen_at.desc())

    return stmt


# --------------------------------------------------------------- keyword part


def _haystack(listing: Listing) -> str:
    return normalize_text(
        " ".join(
            [
                listing.title or "",
                listing.description or "",
                listing.location_raw or "",
                " ".join(listing.features or []),
            ]
        )
    )


def keyword_ok(listing: Listing, criteria: Dict[str, Any]) -> bool:
    c = criteria or {}
    include = [normalize_text(k) for k in _as_list(c.get("keywords_include"))]
    any_of = [normalize_text(k) for k in _as_list(c.get("keywords_any"))]
    exclude = [normalize_text(k) for k in _as_list(c.get("keywords_exclude"))]
    if not (include or any_of or exclude):
        return True

    hay = _haystack(listing)
    if include and not all(k in hay for k in include):
        return False
    if any_of and not any(k in hay for k in any_of):
        return False
    if exclude and any(k in hay for k in exclude):
        return False
    return True


def apply_keyword_filter(listings: List[Listing], criteria: Dict[str, Any]) -> List[Listing]:
    return [l for l in listings if keyword_ok(l, criteria)]


# ---------------------------------------------------- dopasowanie pojedyncze


def matches(listing: Listing, criteria: Dict[str, Any]) -> bool:
    """Czy KONKRETNA oferta spełnia kryteria? Używane przy wysyłce alertów."""
    c = criteria or {}

    if c.get("only_active", True) and not listing.is_active:
        return False
    if c.get("hide_duplicates", True) and listing.is_duplicate:
        return False

    def in_range(value: Optional[float], lo_key: str, hi_key: str) -> bool:
        lo, hi = _num(c.get(lo_key)), _num(c.get(hi_key))
        if lo is None and hi is None:
            return True
        if value is None:
            return False            # brak danych = nie ryzykujemy fałszywego alertu
        if lo is not None and value < lo:
            return False
        if hi is not None and value > hi:
            return False
        return True

    if not in_range(listing.price, "price_min", "price_max"):
        return False
    if not in_range(listing.price_per_m2, "ppm_min", "ppm_max"):
        return False
    if not in_range(listing.area, "area_min", "area_max"):
        return False
    if not in_range(listing.floor, "floor_min", "floor_max"):
        return False

    rooms = [int(r) for r in _as_list(c.get("rooms")) if str(r).isdigit()]
    if rooms:
        if listing.rooms is None:
            return False
        if not (listing.rooms in rooms or (listing.rooms >= 5 and any(r >= 5 for r in rooms))):
            return False

    market = c.get("market")
    if market and market != "oba" and listing.market != market:
        return False

    seller_types = _as_list(c.get("seller_types"))
    if seller_types and listing.seller_type not in seller_types:
        return False

    sources = _as_list(c.get("sources"))
    if sources and listing.source not in sources:
        return False

    conditions = _as_list(c.get("conditions"))
    if conditions and listing.condition not in conditions:
        return False

    regions = _as_list(c.get("regions"))
    estates = _as_list(c.get("estates"))
    if regions or estates:
        loc = normalize_text(f"{listing.estate or ''} {listing.region or ''} {listing.location_raw}")
        hit = False
        if regions and listing.region in regions:
            hit = True
        if not hit and estates:
            if listing.estate in estates:
                hit = True
            elif any(normalize_text(e) in loc for e in estates):
                hit = True
        if not hit:
            return False

    if c.get("only_deals") and not listing.is_deal:
        return False

    return keyword_ok(listing, c)
