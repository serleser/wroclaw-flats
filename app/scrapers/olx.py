"""OLX — mieszkania na sprzedaż, Wrocław.

OLX serwuje listę ofert publicznym endpointem JSON, którego używa własny frontend:

    https://www.olx.pl/api/v1/offers/?category_id=...&city_id=...&sort_by=created_at:desc

To najstabilniejsze i najtańsze źródło (bez HTML, bez Playwrighta) i daje nam
gotowe pola: cena, metraż, pokoje, rynek, typ sprzedawcy (business=True => biuro).

UWAGA: identyfikatory kategorii/miasta bywają zmieniane przez OLX. Jeśli scraper
zwraca 0 ofert, zweryfikuj je narzędziem:  python -m app.tools.probe olx --debug
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..normalize import (
    MARKET_PRIMARY, MARKET_SECONDARY, RawListing, SELLER_AGENCY, SELLER_OWNER,
    clean_text, parse_area, parse_price, parse_rooms,
)
from ..config import settings
from .base import BaseScraper, register
from .http import HttpClient

log = logging.getLogger("scraper.olx")

API_URL = "https://www.olx.pl/api/v1/offers/"
HTML_URL = "https://www.olx.pl/nieruchomosci/mieszkania/sprzedaz/wroclaw/"

# Nieruchomości > Mieszkania > Sprzedaż
CATEGORY_ID = 1307
# Wrocław (region dolnośląskie = 1)
CITY_ID = 19701
REGION_ID = 1

ROOMS_MAP = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six_or_more": 6}


@register
class OlxScraper(BaseScraper):
    name = "olx"
    label = "OLX"
    base_url = "https://www.olx.pl"

    async def fetch(self, client: HttpClient) -> List[RawListing]:
        results = await self._fetch_api(client)
        if results:
            return results

        # API bywa zablokowane ostrzej niż zwykłe strony — spróbujmy odczytać
        # dane osadzone w HTML-u listingu (ten sam model danych, inna droga).
        log.info("[olx] API nie odpowiedziało — próbuję odczytać stronę listingu")
        return await self._fetch_html(client)

    # ------------------------------------------------------------------

    async def _fetch_api(self, client: HttpClient) -> List[RawListing]:
        results: List[RawListing] = []
        limit = 50

        for page in range(settings.max_pages_per_source):
            params = {
                "offset": page * limit,
                "limit": limit,
                "category_id": CATEGORY_ID,
                "city_id": CITY_ID,
                "region_id": REGION_ID,
                "currency": "PLN",
                "sort_by": "created_at:desc",
                "filter_refiners": "spell_checker",
            }
            data = await client.get_json(
                API_URL,
                params=params,
                referer="https://www.olx.pl/nieruchomosci/mieszkania/sprzedaz/wroclaw/",
            )
            items = self.dig(data, "data", default=[])
            if not items:
                log.info("[olx] strona %s pusta — kończę", page)
                break

            for item in items:
                listing = self._parse(item)
                if listing:
                    results.append(listing)

            if len(items) < limit:
                break

        return results

    async def _fetch_html(self, client: HttpClient) -> List[RawListing]:
        """Zapasowa droga: dane osadzone w stronie listingu."""
        results: List[RawListing] = []

        for page in range(1, settings.max_pages_per_source + 1):
            params = {"search[order]": "created_at:desc"}
            if page > 1:
                params["page"] = page

            html = await client.get_text(HTML_URL, params=params, referer="https://www.olx.pl/")
            if not html:
                break

            items = self._ads_from_html(html)
            if not items:
                log.warning(
                    "[olx] nie znalazłem ofert w treści strony — "
                    "sprawdź: python -m app.tools.probe olx --debug"
                )
                break

            for item in items:
                try:
                    listing = self._parse(item)
                except Exception:  # noqa: BLE001
                    listing = None
                if listing:
                    results.append(listing)

        return results

    def _ads_from_html(self, html: str) -> List[Dict[str, Any]]:
        """Wyciąga listę ofert z window.__PRERENDERED_STATE__ albo __NEXT_DATA__."""
        data: Optional[Any] = None

        match = re.search(r'window\.__PRERENDERED_STATE__\s*=\s*("(?:[^"\\]|\\.)*")', html)
        if match:
            try:
                data = json.loads(json.loads(match.group(1)))
            except (json.JSONDecodeError, TypeError) as exc:
                log.debug("[olx] __PRERENDERED_STATE__ nieczytelne: %s", exc)

        if data is None:
            data = self.next_data(html)
        if data is None:
            return []

        found = self._largest_ad_list(data)
        log.info("[olx] odczytano %s ofert ze strony listingu", len(found))
        return found

    def _largest_ad_list(self, node: Any, depth: int = 0) -> List[Dict[str, Any]]:
        """Szuka w drzewie JSON najdłuższej listy wyglądającej na oferty.

        Odporne na przenoszenie danych między wersjami strony — nie zakładamy
        konkretnej ścieżki, tylko rozpoznajemy kształt obiektów.
        """
        if depth > 8:
            return []

        best: List[Dict[str, Any]] = []

        if isinstance(node, list):
            ads = [
                x for x in node
                if isinstance(x, dict) and "id" in x and ("url" in x or "offerUrl" in x)
            ]
            if len(ads) >= 3 and len(ads) > len(best):
                best = ads
            for child in node[:60]:
                candidate = self._largest_ad_list(child, depth + 1)
                if len(candidate) > len(best):
                    best = candidate
        elif isinstance(node, dict):
            for child in node.values():
                candidate = self._largest_ad_list(child, depth + 1)
                if len(candidate) > len(best):
                    best = candidate

        return best

    # ------------------------------------------------------------------

    def _parse(self, item: Dict[str, Any]) -> Optional[RawListing]:
        offer_id = item.get("id")
        url = item.get("url")
        if not offer_id or not url:
            return None

        params = self._params(item)

        price = parse_price(self.dig(params, "price", "value"))
        area = parse_area(self.dig(params, "m", "value")) or parse_area(
            self.dig(params, "m", "label")
        )
        rooms = self._rooms(params)
        price_per_m2 = parse_price(self.dig(params, "price_per_m", "value"))

        market_raw = str(self.dig(params, "market", "key", default="")).lower()
        market = MARKET_PRIMARY if market_raw == "primary" else (
            MARKET_SECONDARY if market_raw == "secondary" else None
        )

        is_business = bool(item.get("business")) or bool(
            self.dig(item, "user", "is_business", default=False)
        )
        seller_type = SELLER_AGENCY if is_business else SELLER_OWNER

        location_parts = [
            self.dig(item, "location", "city", "name"),
            self.dig(item, "location", "district", "name"),
        ]
        location = ", ".join(p for p in location_parts if p)

        photos = item.get("photos") or []
        image_url = None
        if photos:
            raw_link = photos[0].get("link") or photos[0].get("filename") or ""
            image_url = raw_link.replace("{width}", "640").replace("{height}", "480")

        floor = None
        floor_key = str(self.dig(params, "floor_select", "key", default=""))
        if floor_key.startswith("floor_"):
            suffix = floor_key.replace("floor_", "")
            floor = 0 if suffix == "0" else (int(suffix) if suffix.isdigit() else None)

        return RawListing(
            source=self.name,
            source_id=str(offer_id),
            url=url,
            title=clean_text(item.get("title"), 300),
            description=clean_text(item.get("description"), 3000),
            price=price,
            area=area,
            rooms=rooms,
            price_per_m2=price_per_m2,
            location_raw=location,
            market=market,
            seller_type=seller_type,
            floor=floor,
            image_url=image_url,
            posted_at=item.get("created_time") or item.get("last_refresh_time"),
            extra={"promoted": bool(item.get("promotion", {}).get("top_ad"))},
        )

    @staticmethod
    def _params(item: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for p in item.get("params") or []:
            key = p.get("key")
            if not key:
                continue
            value = p.get("value") or {}
            out[key] = {
                "value": value.get("value") if isinstance(value, dict) else value,
                "label": value.get("label") if isinstance(value, dict) else None,
                "key": value.get("key") if isinstance(value, dict) else None,
            }
        return out

    @staticmethod
    def _rooms(params: Dict[str, Dict[str, Any]]) -> Optional[int]:
        node = params.get("rooms") or {}
        key = str(node.get("key") or "").lower()
        if key in ROOMS_MAP:
            return ROOMS_MAP[key]
        return parse_rooms(node.get("label") or node.get("value"))
