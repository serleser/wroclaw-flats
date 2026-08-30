"""Morizon i Gratka — jedna grupa, dwa serwisy, bardzo podobny layout.

Morizon ma sporo ofert od mniejszych biur i sporo duplikatów z Otodom —
i właśnie dlatego jest dobrym testem naszej deduplikacji.

Obie klasy najpierw próbują __NEXT_DATA__ (gdy portal jest na Next.js),
a jeśli go nie ma — spadają do parsowania HTML po selektorach.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..config import settings
from ..normalize import RawListing, clean_text, parse_area, parse_price, parse_rooms
from .base import register
from .generic_html import HtmlListingScraper
from .http import HttpClient

log = logging.getLogger("scraper.morizon")


class _MorizonFamilyScraper(HtmlListingScraper):
    """Wspólna logika: spróbuj JSON-a z Next.js, w razie czego parsuj HTML."""

    next_data_paths: List[tuple] = [
        ("props", "pageProps", "adverts"),
        ("props", "pageProps", "data", "adverts"),
        ("props", "pageProps", "listing", "items"),
        ("props", "pageProps", "searchResult", "items"),
    ]

    async def fetch(self, client: HttpClient) -> List[RawListing]:
        results: List[RawListing] = []

        for page in range(self.first_page, self.first_page + settings.max_pages_per_source):
            params = dict(self.extra_params)
            if page > self.first_page:
                params[self.page_param] = page

            html = await client.get_text(self.list_url, params=params, referer=self.base_url)
            if not html:
                break

            items = self._items_from_next_data(html)
            if items:
                for item in items:
                    listing = self._parse_json_item(item)
                    if listing:
                        results.append(listing)
                continue

            # --- fallback: HTML ---
            tree = self.parse_html(html)
            cards = self._find_cards(tree)
            if not cards:
                log.warning("[%s] brak ofert na stronie %s (ani JSON, ani HTML)", self.name, page)
                break
            for card in cards:
                listing = self.parse_card(card)
                if listing:
                    results.append(listing)

        return results

    def _items_from_next_data(self, html: str) -> List[Dict[str, Any]]:
        data = self.next_data(html)
        if not data:
            return []
        for path in self.next_data_paths:
            items = self.dig(data, *path, default=None)
            if isinstance(items, list) and items:
                return [i for i in items if isinstance(i, dict)]
        return []

    def _parse_json_item(self, item: Dict[str, Any]) -> Optional[RawListing]:
        offer_id = item.get("id") or item.get("advertId") or item.get("uuid")
        url = item.get("url") or item.get("link") or item.get("detailUrl")
        if not offer_id or not url:
            return None
        if not str(url).startswith("http"):
            url = self.base_url.rstrip("/") + "/" + str(url).lstrip("/")

        price = parse_price(
            self.dig(item, "price", "value") or item.get("price") or item.get("priceTotal")
        )
        ppm = parse_price(
            self.dig(item, "pricePerMeter", "value")
            or item.get("pricePerMeter")
            or item.get("priceM2")
        )
        area = parse_area(item.get("area") or self.dig(item, "area", "value") or item.get("size"))
        rooms = parse_rooms(item.get("rooms") or item.get("roomsNumber"))

        location_parts = [
            self.dig(item, "location", "street"),
            self.dig(item, "location", "district"),
            self.dig(item, "location", "city"),
            item.get("locationString"),
        ]
        location = ", ".join(str(p) for p in location_parts if p) or "Wrocław"

        images = item.get("photos") or item.get("images") or []
        image = None
        if images:
            first = images[0]
            image = first if isinstance(first, str) else (
                first.get("url") or first.get("medium") or first.get("thumbnail")
            )

        return RawListing(
            source=self.name,
            source_id=str(offer_id),
            url=str(url),
            title=clean_text(item.get("title") or item.get("name"), 300),
            description=clean_text(item.get("description") or item.get("shortDescription"), 2000),
            price=price,
            price_per_m2=ppm,
            area=area,
            rooms=rooms,
            location_raw=clean_text(location, 250),
            image_url=image,
            posted_at=item.get("dateCreated") or item.get("createdAt"),
        )


@register
class MorizonScraper(_MorizonFamilyScraper):
    name = "morizon"
    label = "Morizon"
    base_url = "https://www.morizon.pl"

    list_url = "https://www.morizon.pl/mieszkania/wroclaw/"
    page_param = "page"
    extra_params = {"ps[sort]": "date_desc"}

    card_selectors = [
        "div.listingBox",
        "section.listing article",
        "div[data-testid='listing-item']",
        "article.card",
        "div.offer-item",
    ]
    link_selectors = ["a.property_link", "h2 a", "a[href*='/oferta/']", "a"]
    title_selectors = ["h2", "span.single-result__title", "a.property_link"]
    price_selectors = ["p.single-result__price", "span.price", "div.price", "strong"]
    ppm_selectors = ["span.single-result__price--currency", "span.price-m2", "li.price-m2"]
    area_selectors = ["li.single-result__params--area", "span.area", "li:nth-child(1)"]
    rooms_selectors = ["li.single-result__params--rooms", "span.rooms"]
    location_selectors = ["h2 span", "p.single-result__category", "span.location"]
    image_selectors = ["img"]
    id_pattern = r"(?:mzn|oferta[/-])(\w{6,})"


@register
class GratkaScraper(_MorizonFamilyScraper):
    name = "gratka"
    label = "Gratka"
    base_url = "https://gratka.pl"

    list_url = "https://gratka.pl/nieruchomosci/mieszkania/wroclaw/sprzedaz"
    page_param = "page"
    extra_params = {"sort": "newest"}

    card_selectors = [
        "article.teaserUnified",
        "div[data-testid='listing-item']",
        "article.listing__teaser",
        "article",
    ]
    link_selectors = ["a.teaserLink", "a[href*='/mieszkanie']", "h2 a", "a"]
    title_selectors = ["h2.teaserUnified__title", "h2", "h3"]
    price_selectors = ["p.teaserUnified__price", "span.price", "p.price"]
    ppm_selectors = ["span.teaserUnified__additionalPrice", "span.price-m2"]
    area_selectors = ["li.teaserUnified__paramsItem", "span.area"]
    rooms_selectors = ["li.teaserUnified__paramsItem", "span.rooms"]
    location_selectors = ["p.teaserUnified__location", "span.location"]
    image_selectors = ["img"]
    id_pattern = r"(\d{7,})"
