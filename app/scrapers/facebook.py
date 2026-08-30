"""Facebook — grupy wrocławskie / Marketplace.  ⚠️ WYŁĄCZONE DOMYŚLNIE.

Uczciwie: automatyczne pobieranie treści z Facebooka łamie regulamin Meta,
wymaga zalogowanej sesji i kończy się blokadą konta. Dlatego NIE dostarczam
tu gotowego scrapera logującego się na Twoje konto.

Zamiast tego masz dwie realne, bezpieczne ścieżki:

  A) IMPORT RĘCZNY (zalecane)
     Widzisz w grupie ciekawe ogłoszenie -> wklejasz link (albo cały post)
     w dashboardzie / wysyłasz do bota. Aplikacja parsuje tekst, wyciąga cenę,
     metraż i lokalizację i wrzuca do tej samej bazy, co reszta źródeł —
     więc działa deduplikacja, ulubione i porównanie z medianą dzielnicy.
     Endpoint: POST /api/manual-listing   (patrz main.py)

  B) TRYB PÓŁAUTOMATYCZNY (na własną odpowiedzialność)
     Playwright ze WŁASNYM, trwałym profilem przeglądarki, w którym logujesz
     się ręcznie raz. Skrypt tylko odświeża grupę i czyta widoczne posty,
     w ludzkim tempie. Szkielet: `FacebookGroupScraper.fetch()` niżej.
     Włączenie: pip install playwright && playwright install chromium,
     potem dopisz `facebook_group` do ENABLED_SOURCES.

Praktyczna uwaga: w grupach typu "Mieszkania Wrocław bez pośredników"
najcenniejsze oferty i tak znikają w 30 minut — alert z portali + import
ręczny z telefonu działa w praktyce lepiej niż walka z blokadami.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from ..normalize import (
    RawListing, SELLER_OWNER, clean_text, parse_area, parse_rooms, price_from_text,
)
from .base import BaseScraper, register
from .http import HttpClient

log = logging.getLogger("scraper.facebook")

# Grupy do obserwowania w trybie półautomatycznym (URL-e publiczne)
GROUP_URLS: List[str] = [
    # "https://www.facebook.com/groups/<id-grupy>",
]


def parse_free_text_offer(text: str, url: str = "", source: str = "manual") -> Optional[RawListing]:
    """Parsuje wklejony post/ogłoszenie z dowolnego miejsca (FB, forum, SMS).

    Używane zarówno przez import ręczny (POST /api/manual-listing),
    jak i przez tryb półautomatyczny.
    """
    text = clean_text(text, 4000)
    if not text:
        return None

    price = price_from_text(text)
    area = parse_area(text)
    rooms = parse_rooms(text)

    # id z linku, a jak nie ma linku — skrót hasha treści
    ident = None
    if url:
        m = re.search(r"(\d{8,})", url)
        ident = m.group(1) if m else url.rstrip("/").rsplit("/", 1)[-1][:60]
    if not ident:
        import hashlib
        ident = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]

    title = text.split(". ")[0][:120] or "Ogłoszenie (import ręczny)"

    return RawListing(
        source=source,
        source_id=str(ident),
        url=url or "",
        title=title,
        description=text,
        price=price,
        area=area,
        rooms=rooms,
        location_raw=text,          # detect_estate wyłapie osiedle z treści
        seller_type=SELLER_OWNER,   # w grupach dominuje sprzedaż bezpośrednia
    ).finalize()


@register
class FacebookGroupScraper(BaseScraper):
    name = "facebook_group"
    label = "Facebook (grupy) — półautomatycznie"
    base_url = "https://www.facebook.com"
    requires_js = True
    enabled_by_default = False

    async def fetch(self, client: HttpClient) -> List[RawListing]:
        if not GROUP_URLS:
            log.info("[facebook_group] brak skonfigurowanych grup — pomijam")
            return []

        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ImportError:
            log.warning(
                "[facebook_group] Playwright nie jest zainstalowany. "
                "pip install playwright && playwright install chromium"
            )
            return []

        results: List[RawListing] = []
        async with async_playwright() as pw:
            # profil trwały => logujesz się RĘCZNIE raz, potem sesja zostaje
            context = await pw.chromium.launch_persistent_context(
                user_data_dir="./data/fb_profile",
                headless=False,          # świadomie: chcesz widzieć, co się dzieje
                locale="pl-PL",
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()
            for group_url in GROUP_URLS:
                try:
                    await page.goto(group_url, wait_until="domcontentloaded", timeout=45_000)
                    await page.wait_for_timeout(4000)
                    for _ in range(3):                      # powolne scrollowanie
                        await page.mouse.wheel(0, 2500)
                        await page.wait_for_timeout(2500)

                    posts = await page.locator("div[role='article']").all_inner_texts()
                    for post_text in posts[:40]:
                        listing = parse_free_text_offer(
                            post_text, url=group_url, source=self.name
                        )
                        if listing and listing.price:
                            results.append(listing)
                except Exception as exc:  # noqa: BLE001
                    log.warning("[facebook_group] %s: %s", group_url, exc)
            await context.close()

        return results
