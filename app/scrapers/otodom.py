"""Otodom — mieszkania na sprzedaż, Wrocław (rynek pierwotny + wtórny).

Otodom to aplikacja Next.js — cała lista wyników jest osadzona w stronie jako
JSON w <script id="__NEXT_DATA__">. Parsujemy ten JSON zamiast HTML-a: jest
dużo stabilniejszy niż klasy CSS (które Otodom generuje losowo).

Struktura bywa przesuwana między wersjami, więc próbujemy kilku ścieżek
(`_ITEM_PATHS`) zanim uznamy, że parser wymaga aktualizacji.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..config import settings
from ..normalize import (
    MARKET_PRIMARY, MARKET_SECONDARY, RawListing, SELLER_AGENCY, SELLER_DEVELOPER,
    SELLER_OWNER, clean_text, parse_area, parse_price,
)
from .base import BaseScraper, register
from .http import HttpClient

log = logging.getLogger("scraper.otodom")

SEARCH_URL = (
    "https://www.otodom.pl/pl/wyniki/sprzedaz/mieszkanie/"
    "dolnoslaskie/wroclaw/wroclaw/wroclaw"
)

ROOMS_MAP = {
    "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
    "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10, "MORE": 10,
}

_ITEM_PATHS = [
    ("props", "pageProps", "data", "searchAds", "items"),
    ("props", "pageProps", "data", "searchAdsResult", "items"),
    ("props", "pageProps", "data", "searchAds", "ads"),
    ("props", "pageProps", "adsData", "items"),
]


@register
class OtodomScraper(BaseScraper):
    name = "otodom"
    label = "Otodom"
    base_url = "https://www.otodom.pl"

    async def fetch(self, client: HttpClient) -> List[RawListing]:
        results: List[RawListing] = []

        for page in range(1, settings.max_pages_per_source + 1):
            params = {
                "limit": 72,
                "page": page,
                "by": "LATEST",
                "direction": "DESC",
                "viewType": "listing",
            }
            html = await client.get_text(
                SEARCH_URL, params=params, referer="https://www.otodom.pl/"
            )
            if not html:
                break

            data = self.next_data(html)
            items = self._extract_items(data)
            if not items:
                log.warning(
                    "[otodom] strona %s: brak ofert w __NEXT_DATA__ "
                    "(zmiana struktury albo blokada) — sprawdź: python -m app.tools.probe otodom --debug",
                    page,
                )
                break

            for item in items:
                listing = self._parse(item)
                if listing:
                    results.append(listing)

            if len(items) < 72:
                break

        return results

    # ------------------------------------------------------------------

    def _extract_items(self, data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not data:
            return []
        for path in _ITEM_PATHS:
            items = self.dig(data, *path, default=None)
            if isinstance(items, list) and items:
                return [i for i in items if isinstance(i, dict)]
        return []

    def _parse(self, item: Dict[str, Any]) -> Optional[RawListing]:
        offer_id = item.get("id") or item.get("adId")
        slug = item.get("slug")
        if not offer_id:
            return None

        url = (
            f"{self.base_url}/pl/oferta/{slug}"
            if slug
            else f"{self.base_url}/pl/oferta/{offer_id}"
        )

        price = parse_price(self.dig(item, "totalPrice", "value")) or parse_price(
            item.get("price")
        )
        price_per_m2 = parse_price(self.dig(item, "pricePerSquareMeter", "value"))
        area = parse_area(item.get("areaInSquareMeters"))

        rooms_raw = item.get("roomsNumber")
        rooms = ROOMS_MAP.get(str(rooms_raw).upper()) if rooms_raw else None
        if rooms is None and isinstance(rooms_raw, (int, float)):
            rooms = int(rooms_raw)

        market_raw = str(item.get("market") or "").upper()
        market = (
            MARKET_PRIMARY if market_raw == "PRIMARY"
            else MARKET_SECONDARY if market_raw == "SECONDARY"
            else None
        )

        # typ sprzedawcy
        if item.get("isPrivateOwner") or str(item.get("agency") or "") in ("", "None"):
            seller_type = SELLER_OWNER if item.get("isPrivateOwner") else None
        else:
            seller_type = SELLER_AGENCY
        if item.get("developmentId") or self.dig(item, "agency", "type") == "DEVELOPER":
            seller_type = SELLER_DEVELOPER
        if seller_type is None:
            seller_type = SELLER_AGENCY if item.get("agency") else SELLER_OWNER

        location = self._location(item)
        image_url = self._image(item)

        return RawListing(
            source=self.name,
            source_id=str(offer_id),
            url=url,
            title=clean_text(item.get("title"), 300),
            description=clean_text(item.get("description") or item.get("shortDescription"), 3000),
            price=price,
            price_per_m2=price_per_m2,
            area=area,
            rooms=rooms,
            location_raw=location,
            market=market,
            seller_type=seller_type,
            floor=self._floor(item),
            image_url=image_url,
            posted_at=item.get("dateCreated") or item.get("dateCreatedFirst"),
            extra={
                "agency": self.dig(item, "agency", "name"),
                "investment": self.dig(item, "investmentState"),
            },
        )

    def _location(self, item: Dict[str, Any]) -> str:
        parts = [
            self.dig(item, "location", "address", "street", "name"),
            self.dig(item, "location", "address", "subdistrict", "name"),
            self.dig(item, "location", "address", "district", "name"),
            self.dig(item, "location", "address", "city", "name"),
        ]
        text = ", ".join(p for p in parts if p)
        if text:
            return text
        # fallback: reverseGeocoding / locationLabel
        label = self.dig(item, "locationLabel", "value")
        if label:
            return str(label)
        nodes = self.dig(item, "location", "reverseGeocoding", "locations", default=[]) or []
        return ", ".join(
            str(n.get("fullName") or n.get("name")) for n in nodes if isinstance(n, dict)
        )[:300]

    def _image(self, item: Dict[str, Any]) -> Optional[str]:
        images = item.get("images") or []
        if images and isinstance(images[0], dict):
            return images[0].get("medium") or images[0].get("large") or images[0].get("small")
        thumbnail = item.get("thumbnailUrl") or item.get("image")
        return str(thumbnail) if thumbnail else None

    def _floor(self, item: Dict[str, Any]) -> Optional[int]:
        raw = item.get("floorNumber")
        if raw is None:
            return None
        text = str(raw).upper()
        if text in ("GROUND", "CELLAR"):
            return 0
        digits = "".join(ch for ch in text if ch.isdigit())
        return int(digits) if digits else None
