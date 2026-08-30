"""Konfigurowalny scraper HTML — baza dla portali bez wygodnego API.

Zamiast pisać od zera parser dla każdego portalu, deklarujesz listę selektorów
CSS (z fallbackami) i klasa robi resztę: paginację, wyciąganie kart, parsowanie
ceny/metrażu/pokoi, budowanie absolutnych URL-i.

Fallbacki są celowo redundantne — portale zmieniają klasy CSS co kilka miesięcy,
a lista alternatyw sprawia, że scraper przeżywa taki redesign zamiast zwracać 0.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional
from urllib.parse import urljoin

from selectolax.parser import Node

from ..config import settings
from ..normalize import (
    RawListing, clean_text, parse_area, parse_floor, parse_price, parse_rooms, price_from_text,
)
from .base import BaseScraper
from .http import HttpClient

log = logging.getLogger("scraper.html")


class HtmlListingScraper(BaseScraper):
    """Scraper listy ogłoszeń sterowany selektorami."""

    list_url: str = ""
    page_param: str = "page"
    first_page: int = 1
    extra_params: dict = {}

    # selektory — kolejność = priorytet
    card_selectors: List[str] = []
    link_selectors: List[str] = ["a"]
    title_selectors: List[str] = ["h2", "h3", "a"]
    price_selectors: List[str] = []
    ppm_selectors: List[str] = []
    area_selectors: List[str] = []
    rooms_selectors: List[str] = []
    location_selectors: List[str] = []
    image_selectors: List[str] = ["img"]
    description_selectors: List[str] = []

    # wzorzec URL-a oferty (do wyciągnięcia source_id)
    id_pattern: str = r"(\d{5,})"

    def page_url(self, page: int) -> tuple[str, dict]:
        """Adres i parametry dla danej strony wyników.

        Nadpisz w podklasie, gdy portal używa adresu, którego nie da się złożyć
        ze zwykłych par klucz=wartość (np. parametry pozycyjne po przecinkach).
        """
        params = dict(self.extra_params)
        if page > self.first_page:
            params[self.page_param] = page
        return self.list_url, params

    async def fetch(self, client: HttpClient) -> List[RawListing]:
        results: List[RawListing] = []

        for page in range(self.first_page, self.first_page + settings.max_pages_per_source):
            url, params = self.page_url(page)

            html = await client.get_text(url, params=params, referer=self.base_url)
            if not html:
                break

            tree = self.parse_html(html)
            cards = self._find_cards(tree)
            if not cards:
                log.warning(
                    "[%s] strona %s: nie znaleziono kart ofert — sprawdź selektory "
                    "(python -m app.tools.probe %s --debug)",
                    self.name, page, self.name,
                )
                break

            page_items = 0
            for card in cards:
                listing = self.parse_card(card)
                if listing:
                    results.append(listing)
                    page_items += 1

            log.debug("[%s] strona %s: %s ofert", self.name, page, page_items)
            if page_items == 0:
                break

        return results

    # ------------------------------------------------------------------

    def _find_cards(self, tree) -> List[Node]:
        for sel in self.card_selectors:
            nodes = tree.css(sel)
            if len(nodes) >= 3:          # 1-2 trafienia to zwykle przypadek
                return nodes
        return []

    def parse_card(self, card: Node) -> Optional[RawListing]:
        href = self.first_attr(card, "href", *self.link_selectors)
        if not href:
            return None
        url = urljoin(self.base_url, href)

        source_id = self.extract_id(url, card)
        if not source_id:
            return None

        title = self.first_text(card, *self.title_selectors) or clean_text(card.text(), 200)
        price_text = self.first_text(card, *self.price_selectors)
        ppm_text = self.first_text(card, *self.ppm_selectors)
        area_text = self.first_text(card, *self.area_selectors)
        rooms_text = self.first_text(card, *self.rooms_selectors)
        location = self.first_text(card, *self.location_selectors)
        description = self.first_text(card, *self.description_selectors)

        card_text = clean_text(card.text(), 1200)

        price = parse_price(price_text) or self._price_from_text(card_text)
        area = parse_area(area_text) or self._area_from_text(card_text)
        rooms = parse_rooms(rooms_text) or parse_rooms(title) or parse_rooms(card_text)
        ppm = parse_price(ppm_text)

        image = self.first_attr(card, "src", *self.image_selectors) or self.first_attr(
            card, "data-src", *self.image_selectors
        ) or self.first_attr(card, "data-lazy", *self.image_selectors)
        if image:
            image = urljoin(self.base_url, image)

        return RawListing(
            source=self.name,
            source_id=source_id,
            url=url,
            title=clean_text(title, 300),
            description=clean_text(description or card_text, 2000),
            price=price,
            price_per_m2=ppm,
            area=area,
            rooms=rooms,
            floor=parse_floor(card_text),
            # UWAGA: nigdy nie wstawiamy tu "Wrocław" jako wartości domyślnej.
            # Dopisanie miasta, którego portal nie podał, zamieniało oferty
            # z innych miast we „wrocławskie” i psuło mediany dzielnic.
            location_raw=clean_text(location or "", 250),
            image_url=image,
        )

    def extract_id(self, url: str, card: Node) -> Optional[str]:
        attr_id = card.attributes.get("data-id") or card.attributes.get("id")
        if attr_id and any(ch.isdigit() for ch in attr_id):
            return re.sub(r"\D", "", attr_id)[:20] or None
        m = re.search(self.id_pattern, url)
        if m:
            return m.group(1)
        # ostatnia deska ratunku: slug z URL-a
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        return slug[:100] or None

    # -------------------------------------------------- heurystyki tekstowe

    @staticmethod
    def _price_from_text(text: str) -> Optional[float]:
        return price_from_text(text)

    @staticmethod
    def _area_from_text(text: str) -> Optional[float]:
        m = re.search(r"(\d+[.,]?\d*)\s*(?:m2|m²|mkw)", text, re.IGNORECASE)
        return parse_area(m.group(1)) if m else None
