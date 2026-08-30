"""Mniejsze i lokalne portale.

Wartość tych źródeł: mało kto je monitoruje, więc oferta potrafi tam wisieć
kilka dni zanim trafi na Otodom — albo nie trafić wcale. Zwłaszcza Sprzedawacz
i Adradar mają sporo ogłoszeń bezpośrednio od właścicieli.

Wszystkie dziedziczą po HtmlListingScraper — dodanie kolejnego portalu to
kilkanaście linijek selektorów, bez pisania logiki.
"""
from __future__ import annotations

from .base import register
from .generic_html import HtmlListingScraper


@register
class SprzedawaczScraper(HtmlListingScraper):
    name = "sprzedawacz"
    label = "Sprzedawacz"
    base_url = "https://sprzedajemy.pl"
    enabled_by_default = False

    list_url = "https://sprzedajemy.pl/wroclaw/nieruchomosci/mieszkania/sprzedam"
    page_param = "offset"
    extra_params = {"sort": "date"}

    card_selectors = ["li.element", "article.offer", "div.offer-item", "li[data-id]"]
    link_selectors = ["a.offerLink", "h2 a", "a[href*='/oferta/']", "a"]
    title_selectors = ["h2", "span.title", "a.offerLink"]
    price_selectors = ["span.price", "strong.price", "div.price"]
    area_selectors = ["span.area", "li.param-area"]
    rooms_selectors = ["span.rooms", "li.param-rooms"]
    location_selectors = ["span.city", "p.location", "span.location"]
    image_selectors = ["img"]
    id_pattern = r"(\d{7,})"


@register
class AdradarScraper(HtmlListingScraper):
    name = "adradar"
    label = "Adradar"
    base_url = "https://adradar.pl"
    enabled_by_default = False

    list_url = "https://adradar.pl/mieszkania/wroclaw"
    page_param = "page"
    extra_params = {"sort": "newest"}

    card_selectors = ["div.offer-card", "article.offer", "div[data-offer-id]", "article"]
    link_selectors = ["a.offer-link", "h2 a", "a"]
    title_selectors = ["h2", "h3", "span.offer-title"]
    price_selectors = ["span.offer-price", "div.price", "strong"]
    ppm_selectors = ["span.price-per-m", "span.ppm"]
    area_selectors = ["span.offer-area", "li.area"]
    rooms_selectors = ["span.offer-rooms", "li.rooms"]
    location_selectors = ["span.offer-location", "p.location"]
    image_selectors = ["img"]
    id_pattern = r"(\d{5,})"


@register
class NieruchomosciSzybkoScraper(HtmlListingScraper):
    """Szablon do skopiowania pod kolejny portal — zmień 4 pola i selektory."""

    name = "portal_szablon"
    label = "Szablon nowego portalu"
    base_url = "https://przyklad.pl"
    enabled_by_default = False

    list_url = "https://przyklad.pl/mieszkania/wroclaw/sprzedaz"
    card_selectors = ["article.listing-item"]
    link_selectors = ["a"]
    title_selectors = ["h2"]
    price_selectors = ["span.price"]
    area_selectors = ["span.area"]
    rooms_selectors = ["span.rooms"]
    location_selectors = ["span.location"]
