"""Rynek pierwotny — strony wrocławskich deweloperów i agregatory inwestycji.

Deweloperzy nie mają wspólnego standardu, ale ~80% z nich publikuje listę
mieszkań w jednej z trzech form:

  1. JSON-LD (`application/ld+json` z typem Product/Offer/Apartment)  <- najłatwiej
  2. tabela/lista mieszkań w HTML z kolumnami: nr, metraż, pokoje, piętro, cena
  3. XHR do własnego API (np. /api/flats) — wtedy podajemy `api_url` w configu

Zamiast pisać osobny plik na każdego dewelopera, deklarujesz go w
`config/developers.json`, a ta klasa obsłuży wszystkie. Przykład wpisu:

    {
      "key": "archicom_ol",
      "name": "Archicom — Olimpia Port",
      "url": "https://przyklad-dewelopera.pl/mieszkania/",
      "mode": "jsonld",                 // jsonld | table | api
      "estate": "Popowice",
      "table": {                        // tylko dla mode=table
        "row": "table.flats tbody tr",
        "area": "td:nth-child(2)",
        "rooms": "td:nth-child(3)",
        "floor": "td:nth-child(4)",
        "price": "td:nth-child(5)",
        "link": "a"
      },
      "api_url": null                   // tylko dla mode=api
    }

Uruchomienie: dodaj `developers` do ENABLED_SOURCES w .env.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from ..config import BASE_DIR
from ..normalize import (
    MARKET_PRIMARY, RawListing, SELLER_DEVELOPER, clean_text, parse_area,
    parse_floor, parse_price, parse_rooms,
)
from .base import BaseScraper, register
from .http import HttpClient

log = logging.getLogger("scraper.developers")

CONFIG_PATH = BASE_DIR / "config" / "developers.json"


@register
class DevelopersScraper(BaseScraper):
    name = "developers"
    label = "Deweloperzy (rynek pierwotny)"
    base_url = ""
    enabled_by_default = False

    def _load_config(self) -> List[Dict[str, Any]]:
        if not CONFIG_PATH.exists():
            log.info("[developers] brak %s — pomijam", CONFIG_PATH)
            return []
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.error("[developers] błąd w developers.json: %s", exc)
            return []
        return [d for d in data.get("developers", []) if d.get("url") and d.get("enabled", True)]

    async def fetch(self, client: HttpClient) -> List[RawListing]:
        results: List[RawListing] = []
        for dev in self._load_config():
            try:
                results.extend(await self._fetch_one(client, dev))
            except Exception as exc:  # noqa: BLE001
                log.warning("[developers] %s: %s", dev.get("key"), exc)
        return results

    async def _fetch_one(self, client: HttpClient, dev: Dict[str, Any]) -> List[RawListing]:
        mode = dev.get("mode", "jsonld")
        if mode == "api" and dev.get("api_url"):
            data = await client.get_json(dev["api_url"], referer=dev["url"])
            return self._from_api(dev, data or [])

        html = await client.get_text(dev["url"])
        if not html:
            return []
        if mode == "table":
            return self._from_table(dev, html)
        return self._from_jsonld(dev, html)

    # ------------------------------------------------------------------

    def _base(self, dev: Dict[str, Any], value: Optional[str]) -> str:
        return urljoin(dev["url"], value or "")

    def _make(self, dev: Dict[str, Any], **kwargs) -> RawListing:
        return RawListing(
            source=f"dev:{dev['key']}",
            seller_type=SELLER_DEVELOPER,
            market=MARKET_PRIMARY,
            location_raw=dev.get("estate") or dev.get("location") or "Wrocław",
            extra={"developer": dev.get("name")},
            **kwargs,
        )

    def _from_jsonld(self, dev: Dict[str, Any], html: str) -> List[RawListing]:
        out: List[RawListing] = []
        for block in self.json_ld(html):
            types = block.get("@type")
            types = types if isinstance(types, list) else [types]
            if not any(t in ("Product", "Apartment", "Offer", "Residence") for t in types if t):
                continue
            offer = block.get("offers") or {}
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            price = parse_price(offer.get("price") or block.get("price"))
            url = self._base(dev, block.get("url") or offer.get("url"))
            name = clean_text(block.get("name"), 200)
            area = parse_area(
                self.dig(block, "floorSize", "value") or block.get("floorSize") or name
            )
            rooms = parse_rooms(block.get("numberOfRooms") or name)
            sku = str(block.get("sku") or block.get("productID") or name or url)
            if not (price or area):
                continue
            out.append(
                self._make(
                    dev,
                    source_id=f"{dev['key']}:{sku}"[:110],
                    url=url,
                    title=name or f"{dev.get('name')} — mieszkanie",
                    description=clean_text(block.get("description"), 1500),
                    price=price,
                    area=area,
                    rooms=rooms,
                    image_url=self._image_from_jsonld(block),
                )
            )
        return out

    @staticmethod
    def _image_from_jsonld(block: Dict[str, Any]) -> Optional[str]:
        img = block.get("image")
        if isinstance(img, list) and img:
            img = img[0]
        if isinstance(img, dict):
            img = img.get("url")
        return str(img) if img else None

    def _from_table(self, dev: Dict[str, Any], html: str) -> List[RawListing]:
        cfg = dev.get("table") or {}
        tree = self.parse_html(html)
        rows = tree.css(cfg.get("row", "table tr"))
        out: List[RawListing] = []
        for idx, row in enumerate(rows):
            price = parse_price(self.first_text(row, cfg.get("price", "td:last-child")))
            area = parse_area(self.first_text(row, cfg.get("area", "td:nth-child(2)")))
            if not (price and area):
                continue
            rooms = parse_rooms(self.first_text(row, cfg.get("rooms", "td:nth-child(3)")))
            floor = parse_floor(self.first_text(row, cfg.get("floor", "td:nth-child(4)")))
            link = self.first_attr(row, "href", cfg.get("link", "a")) or dev["url"]
            label = self.first_text(row, cfg.get("label", "td:first-child")) or f"m{idx+1}"
            out.append(
                self._make(
                    dev,
                    source_id=f"{dev['key']}:{label}"[:110],
                    url=self._base(dev, link),
                    title=f"{dev.get('name')} — {label} · {area:.0f} m² · {rooms or '?'} pok.",
                    description=clean_text(row.text(), 500),
                    price=price,
                    area=area,
                    rooms=rooms,
                    floor=floor,
                )
            )
        return out

    def _from_api(self, dev: Dict[str, Any], data: Any) -> List[RawListing]:
        items = data if isinstance(data, list) else (
            data.get("items") or data.get("flats") or data.get("data") or []
        )
        mapping = dev.get("map", {})
        out: List[RawListing] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            price = parse_price(item.get(mapping.get("price", "price")))
            area = parse_area(item.get(mapping.get("area", "area")))
            if not (price and area):
                continue
            ident = item.get(mapping.get("id", "id")) or item.get("number") or len(out)
            out.append(
                self._make(
                    dev,
                    source_id=f"{dev['key']}:{ident}"[:110],
                    url=self._base(dev, item.get(mapping.get("url", "url"))),
                    title=f"{dev.get('name')} — {ident} · {area:.0f} m²",
                    description=clean_text(json.dumps(item, ensure_ascii=False), 800),
                    price=price,
                    area=area,
                    rooms=parse_rooms(item.get(mapping.get("rooms", "rooms"))),
                    floor=parse_floor(str(item.get(mapping.get("floor", "floor")))),
                )
            )
        return out
