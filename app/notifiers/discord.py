"""Discord webhook — kanał zapasowy / archiwum ofert dla dwóch osób.

Konfiguracja: serwer Discord -> ustawienia kanału -> Integracje -> Webhooki ->
Nowy webhook -> kopiuj URL do DISCORD_WEBHOOK_URL w .env. Bez rejestracji bota.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from ..config import settings
from ..models import Listing, SavedFilter
from .base import BaseNotifier, build_message, fmt_area, fmt_pln

log = logging.getLogger("notifier.discord")


class DiscordNotifier(BaseNotifier):
    name = "discord"

    def is_configured(self) -> bool:
        return bool(settings.discord_webhook_url)

    def _embed(self, listing: Listing, saved_filter: Optional[SavedFilter]) -> Dict[str, Any]:
        color = 0xE8590C if listing.is_deal else 0x2563EB
        fields = [
            {"name": "Cena", "value": fmt_pln(listing.price), "inline": True},
            {"name": "Cena/m²", "value": fmt_pln(listing.price_per_m2), "inline": True},
            {"name": "Metraż", "value": fmt_area(listing.area), "inline": True},
            {"name": "Pokoje", "value": str(listing.rooms or "—"), "inline": True},
            {
                "name": "Lokalizacja",
                "value": listing.estate or listing.region or listing.location_raw or "Wrocław",
                "inline": True,
            },
            {"name": "Źródło", "value": listing.source, "inline": True},
        ]
        if listing.is_deal and listing.deal_score:
            fields.append(
                {
                    "name": "Okazja",
                    "value": f"{listing.deal_score:.0f}% poniżej mediany",
                    "inline": False,
                }
            )
        embed: Dict[str, Any] = {
            "title": listing.title[:250],
            "url": listing.url,
            "color": color,
            "fields": fields,
            "footer": {"text": saved_filter.name if saved_filter else "Wrocław — monitor mieszkań"},
        }
        if listing.image_url:
            embed["thumbnail"] = {"url": listing.image_url}
        return embed

    async def send(self, listing: Listing, saved_filter: Optional[SavedFilter] = None) -> None:
        payload = {
            "content": "🔥 **OKAZJA**" if listing.is_deal else None,
            "embeds": [self._embed(listing, saved_filter)],
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(settings.discord_webhook_url, json=payload)
        if resp.status_code >= 300:
            raise RuntimeError(f"Discord: {resp.status_code} — {resp.text[:200]}")

    async def send_text(self, text: str) -> None:
        async with httpx.AsyncClient(timeout=20) as client:
            await client.post(settings.discord_webhook_url, json={"content": text[:1900]})
