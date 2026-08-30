"""Dispatcher powiadomień: bierze ofertę + filtr i wysyła na skonfigurowane kanały.

Zabezpieczenia przed zalewem powiadomień:
  * UNIQUE(listing_id, filter_id) w tabeli notifications — jedna oferta = jeden alert
  * MAX_NOTIFICATIONS_PER_RUN — limit na cykl
  * SILENT_FIRST_RUN — pierwszy przebieg tylko wypełnia bazę
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Listing, Notification, SavedFilter
from .base import BaseNotifier, build_message  # noqa: F401
from .discord import DiscordNotifier
from .email import EmailNotifier
from .telegram import TelegramNotifier

log = logging.getLogger("notifier")

_NOTIFIERS: Dict[str, BaseNotifier] = {
    "telegram": TelegramNotifier(),
    "discord": DiscordNotifier(),
    "email": EmailNotifier(),
}


def available_channels() -> Dict[str, bool]:
    return {name: n.is_configured() for name, n in _NOTIFIERS.items()}


def get_notifier(channel: str) -> Optional[BaseNotifier]:
    return _NOTIFIERS.get(channel)


def _channels_for(saved_filter: Optional[SavedFilter]) -> List[str]:
    if saved_filter is None:
        return [name for name, n in _NOTIFIERS.items() if n.is_configured()]
    wanted = []
    if saved_filter.notify_telegram:
        wanted.append("telegram")
    if saved_filter.notify_discord:
        wanted.append("discord")
    if saved_filter.notify_email:
        wanted.append("email")
    return [c for c in wanted if _NOTIFIERS[c].is_configured()]


def already_notified(session: Session, listing_id: int, filter_id: Optional[int]) -> bool:
    stmt = select(Notification.id).where(
        Notification.listing_id == listing_id,
        Notification.filter_id == filter_id,
    )
    return session.scalar(stmt) is not None


async def notify(
    session: Session,
    listing: Listing,
    saved_filter: Optional[SavedFilter] = None,
) -> List[Tuple[str, bool]]:
    """Wysyła powiadomienie i zapisuje wynik w historii. Zwraca [(kanał, ok)]."""
    filter_id = saved_filter.id if saved_filter else None
    if already_notified(session, listing.id, filter_id):
        return []

    results: List[Tuple[str, bool]] = []
    for channel in _channels_for(saved_filter):
        notifier = _NOTIFIERS[channel]
        status, error = "sent", None
        try:
            await notifier.send(listing, saved_filter)
        except Exception as exc:  # noqa: BLE001
            status, error = "failed", str(exc)[:500]
            log.warning("[%s] nie udało się wysłać oferty %s: %s", channel, listing.id, exc)
        results.append((channel, status == "sent"))
        session.add(
            Notification(
                listing_id=listing.id,
                filter_id=filter_id,
                channel=channel,
                status=status,
                error=error,
            )
        )

    if not results:
        # brak skonfigurowanego kanału — i tak zapisz w historii, żeby było widać w UI
        session.add(
            Notification(
                listing_id=listing.id,
                filter_id=filter_id,
                channel="none",
                status="skipped",
                error="brak skonfigurowanego kanału powiadomień",
            )
        )
    session.flush()
    return results
