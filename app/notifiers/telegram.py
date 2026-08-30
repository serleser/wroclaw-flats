"""Telegram — główny kanał alertów (push na telefon w kilka sekund).

Wysyłamy zdjęcie z podpisem (sendPhoto), a gdy oferta nie ma zdjęcia albo
Telegram odrzuci URL obrazka — zwykłą wiadomość (sendMessage).
Pod wiadomością dodajemy przyciski: "Otwórz ofertę" i "Do ulubionych".
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings
from ..models import Listing, SavedFilter
from .base import BaseNotifier, build_message

log = logging.getLogger("notifier.telegram")

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramNotifier(BaseNotifier):
    name = "telegram"

    def is_configured(self) -> bool:
        return bool(settings.telegram_bot_token and settings.telegram_chat_ids)

    # ------------------------------------------------------------------

    async def _call(self, method: str, payload: Dict[str, Any]) -> bool:
        url = API.format(token=settings.telegram_bot_token, method=method)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            log.error("Telegram: błąd sieci — %s", exc)
            return False

        if resp.status_code == 200 and resp.json().get("ok"):
            return True
        log.error("Telegram %s: %s — %s", method, resp.status_code, resp.text[:300])
        return False

    def _keyboard(self, listing: Listing) -> Dict[str, Any]:
        base = f"http://{settings.host}:{settings.port}"
        return {
            "inline_keyboard": [
                [
                    {"text": "🔗 Otwórz ofertę", "url": listing.url},
                    {"text": "⭐ Dashboard", "url": f"{base}/?listing={listing.id}"},
                ]
            ]
        }

    # ------------------------------------------------------------------

    async def send(self, listing: Listing, saved_filter: Optional[SavedFilter] = None) -> None:
        text = build_message(listing, saved_filter)
        errors: List[str] = []

        for chat_id in settings.telegram_chat_ids:
            sent = False
            if listing.image_url:
                sent = await self._call(
                    "sendPhoto",
                    {
                        "chat_id": chat_id,
                        "photo": listing.image_url,
                        "caption": text[:1024],
                        "parse_mode": "Markdown",
                        "reply_markup": self._keyboard(listing),
                    },
                )
            if not sent:
                sent = await self._call(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": text[:4096],
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": False,
                        "reply_markup": self._keyboard(listing),
                    },
                )
            if not sent:
                errors.append(chat_id)

        if errors:
            raise RuntimeError(f"Telegram: nie wysłano do {', '.join(errors)}")

    async def send_text(self, text: str) -> None:
        for chat_id in settings.telegram_chat_ids:
            await self._call(
                "sendMessage",
                {"chat_id": chat_id, "text": text[:4096], "parse_mode": "Markdown"},
            )
