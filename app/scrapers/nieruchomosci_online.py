"""Nieruchomosci-online.pl — mieszkania na sprzedaż, Wrocław.

Klasyczny HTML (bez SPA), więc korzystamy z HtmlListingScraper i deklarujemy
tylko selektory. Portal ma bardzo dobre pokrycie mniejszych biur wrocławskich,
których nie ma na OLX.
"""
from __future__ import annotations

from .base import register
from .generic_html import HtmlListingScraper


@register
class NieruchomosciOnlineScraper(HtmlListingScraper):
    name = "nieruchomosci_online"
    label = "Nieruchomości-online"
    base_url = "https://www.nieruchomosci-online.pl"

    # Ten portal używa parametrów POZYCYJNYCH, oddzielonych przecinkami:
    #   szukaj.html?3,mieszkanie,sprzedaz,,Wrocław
    # Nie da się tego złożyć ze zwykłych par klucz=wartość, dlatego budujemy
    # adres ręcznie w page_url(). Wcześniejsza wersja wysyłała zwykłe parametry,
    # które portal ignorował — i zwracał oferty z całej Polski.
    list_url = "https://www.nieruchomosci-online.pl/szukaj.html?3,mieszkanie,sprzedaz,,Wroc%C5%82aw"
    page_param = "p"
    extra_params = {}

    def page_url(self, page: int) -> tuple[str, dict]:
        url = self.list_url
        if page > self.first_page:
            url = f"{url}&p={page}"
        return url, {}

    card_selectors = [
        "div.tile-outer",
        "div.tile",
        "article.tile",
        "div[data-testid='listing-item']",
        "li.tile",
    ]
    link_selectors = ["a.tile-title-link", "h2 a", "h3 a", "a.title", "a[href*='.html']"]
    title_selectors = ["a.tile-title-link", "h2", "h3", "span.title"]
    price_selectors = ["p.title-a", "span.tile-price", "div.price", "p.price", "strong.price"]
    ppm_selectors = ["span.title-b", "span.price-per-meter", "p.price-m2"]
    area_selectors = ["span.area", "li.area", "span.tile-area", "p.area"]
    rooms_selectors = ["span.rooms", "li.rooms", "span.tile-rooms"]
    location_selectors = [
        "p.name-list", "span.tile-location", "p.location", "span.address", "h2 span",
    ]
    description_selectors = ["p.tile-desc", "div.description", "p.desc"]
    image_selectors = ["img.tile-photo", "img"]

    id_pattern = r"(\d{6,})"
