"""Bazowa klasa scrapera + rejestr pluginów.

Dodanie nowego portalu = jeden plik w app/scrapers/ z klasą dziedziczącą po
BaseScraper i dekoratorem @register. Nic więcej nie trzeba nigdzie wpinać.

    from .base import BaseScraper, register

    @register
    class MojPortalScraper(BaseScraper):
        name = "moj_portal"
        label = "Mój Portal"
        base_url = "https://przyklad.pl"

        async def fetch(self, client): ...
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Type

from selectolax.parser import HTMLParser

from ..config import settings
from ..districts import location_verdict
from ..normalize import RawListing
from .http import HttpClient

log = logging.getLogger("scraper")

_REGISTRY: Dict[str, Type["BaseScraper"]] = {}


def register(cls: Type["BaseScraper"]) -> Type["BaseScraper"]:
    _REGISTRY[cls.name] = cls
    return cls


def get_scraper(name: str) -> Optional["BaseScraper"]:
    cls = _REGISTRY.get(name)
    return cls() if cls else None


def available_scrapers() -> Dict[str, Type["BaseScraper"]]:
    return dict(_REGISTRY)


class BaseScraper:
    """Kontrakt scrapera: fetch() -> lista RawListing."""

    name: str = "base"
    label: str = "Base"
    base_url: str = ""
    requires_js: bool = False          # True => potrzebny Playwright
    enabled_by_default: bool = True
    rejected_out_of_city: int = 0      # ile ofert odrzucił filtr miasta

    async def fetch(self, client: HttpClient) -> List[RawListing]:
        raise NotImplementedError

    async def run(self) -> List[RawListing]:
        async with HttpClient() as client:
            try:
                items = await self.fetch(client)
            except Exception as exc:  # noqa: BLE001
                log.exception("[%s] scraper wywalił się: %s", self.name, exc)
                raise

        out: List[RawListing] = []
        seen: set[str] = set()
        rejected: Dict[str, int] = {}

        for item in items:
            if not item or not item.source_id or item.source_id in seen:
                continue
            seen.add(item.source_id)
            item = item.finalize()

            if settings.strict_city_filter:
                haystack = " ".join(
                    filter(None, [item.location_raw, item.title, item.description, item.url])
                )
                ok, reason = location_verdict(haystack)
                if not ok:
                    rejected[reason] = rejected.get(reason, 0) + 1
                    continue

            out.append(item)

        self.rejected_out_of_city = sum(rejected.values())
        if rejected:
            details = "; ".join(f"{reason} ({count})" for reason, count in rejected.items())
            log.warning("[%s] odrzucono %s ofert spoza Wrocławia — %s",
                        self.name, self.rejected_out_of_city, details)
        log.info("[%s] pobrano %s ofert", self.name, len(out))
        return out

    # ------------------------------------------------------------ narzędzia

    @staticmethod
    def parse_html(html: str) -> HTMLParser:
        return HTMLParser(html)

    @staticmethod
    def next_data(html: str) -> Optional[Dict[str, Any]]:
        """Wyciąga JSON z <script id="__NEXT_DATA__"> (Otodom, Morizon, Gratka)."""
        m = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError as exc:
            log.warning("Nie udało się sparsować __NEXT_DATA__: %s", exc)
            return None

    @staticmethod
    def json_ld(html: str) -> List[Dict[str, Any]]:
        """Wyciąga wszystkie bloki application/ld+json — częsty fallback."""
        blocks: List[Dict[str, Any]] = []
        for m in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        ):
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(data, list):
                blocks.extend(d for d in data if isinstance(d, dict))
            elif isinstance(data, dict):
                blocks.append(data)
        return blocks

    @staticmethod
    def dig(data: Any, *path: Any, default: Any = None) -> Any:
        """Bezpieczne schodzenie po zagnieżdżonym JSON-ie: dig(d,'a',0,'b')"""
        cur = data
        for key in path:
            if cur is None:
                return default
            try:
                cur = cur[key]
            except (KeyError, IndexError, TypeError):
                return default
        return cur if cur is not None else default

    @staticmethod
    def first_text(node, *selectors: str) -> str:
        """Pierwszy pasujący selektor CSS -> tekst. Odporne na zmiany layoutu."""
        for sel in selectors:
            found = node.css_first(sel)
            if found is not None:
                text = found.text(strip=True)
                if text:
                    return text
        return ""

    @staticmethod
    def first_attr(node, attr: str, *selectors: str) -> Optional[str]:
        for sel in selectors:
            found = node.css_first(sel)
            if found is not None:
                value = found.attributes.get(attr)
                if value:
                    return value
        return None
