"""Podsumowanie na Telegram — przegląd ofert bez otwierania dashboardu.

    python -m app.tools.digest                  # ostatnie 24 h
    python -m app.tools.digest --hours 12       # ostatnie 12 h
    python -m app.tools.digest --limit 12       # więcej pozycji
    python -m app.tools.digest --skip-empty     # nie wysyłaj, gdy nic nowego
    python -m app.tools.digest --dry-run        # wypisz na ekran zamiast wysyłać

Alerty przychodzą pojedynczo, gdy pojawi się coś pasującego. Podsumowanie robi
co innego: raz dziennie pokazuje CAŁY obraz — ile ofert przybyło, które są
najlepszymi okazjami i co pojawiło się bezpośrednio od właścicieli. To namiastka
przeglądania dashboardu, mieszcząca się w jednej wiadomości na telefonie.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from typing import List, Optional

from sqlalchemy import func, select

from ..config import settings
from ..db import init_db, session_scope
from ..matching import matches
from ..models import Listing, SavedFilter, utcnow
from ..notifiers.base import esc_html, fmt_area, fmt_pln
from ..notifiers.telegram import TelegramNotifier

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SELLER_SHORT = {"wlasciciel": "właściciel", "biuro": "biuro", "deweloper": "deweloper"}


def line_for(listing: Listing, mark: str = "") -> str:
    """Jedna oferta jako zwięzła linijka z klikalnym tytułem."""
    place = listing.estate or listing.region or "Wrocław"
    bits = [fmt_pln(listing.price)]
    if listing.price_per_m2:
        bits.append(f"{fmt_pln(listing.price_per_m2)}/m²")
    bits.append(fmt_area(listing.area))
    if listing.rooms:
        bits.append(f"{listing.rooms} pok.")

    head = f'{mark}<a href="{esc_html(listing.url)}">{esc_html(listing.title[:70])}</a>'
    meta = f"   {' · '.join(bits)}"
    tail = f"   📍 {esc_html(place)}"
    if listing.seller_type in SELLER_SHORT:
        tail += f" · {SELLER_SHORT[listing.seller_type]}"
    if listing.is_deal and listing.deal_score:
        tail += f" · <b>−{listing.deal_score:.0f}%</b>"
    return f"{head}\n{meta}\n{tail}"


def build_digest(session, hours: int, limit: int) -> tuple[str, int]:
    """Buduje treść podsumowania. Zwraca (tekst, liczba_nowych_ofert)."""
    since = utcnow() - dt.timedelta(hours=hours)

    fresh = list(
        session.scalars(
            select(Listing)
            .where(Listing.first_seen_at >= since)
            .where(Listing.is_active.is_(True))
            .where(Listing.is_duplicate.is_(False))
            .order_by(Listing.first_seen_at.desc())
        )
    )

    # Jeśli macie zapisane alerty, podsumowanie trzyma się ich kryteriów —
    # inaczej zalałoby Was ofertami, których i tak nie chcecie oglądać.
    filters = list(session.scalars(select(SavedFilter).where(SavedFilter.is_active.is_(True))))
    if filters:
        fresh = [l for l in fresh if any(matches(l, f.criteria) for f in filters)]

    total_active = session.scalar(
        select(func.count(Listing.id))
        .where(Listing.is_active.is_(True))
        .where(Listing.is_duplicate.is_(False))
    ) or 0

    okres = "ostatniej doby" if hours == 24 else f"ostatnich {hours} h"
    lines: List[str] = [f"📊 <b>Podsumowanie — {okres}</b>", ""]

    if not fresh:
        lines.append("Brak nowych ofert pasujących do Waszych kryteriów.")
        lines.append("")
        lines.append(f"<i>W bazie: {total_active} aktywnych ogłoszeń. Monitor działa.</i>")
        return "\n".join(lines), 0

    deals = sorted(
        [l for l in fresh if l.is_deal],
        key=lambda l: -(l.deal_score or 0),
    )
    owners = [l for l in fresh if l.seller_type == "wlasciciel" and l not in deals]
    rest = [l for l in fresh if l not in deals and l not in owners]

    lines.append(f"Nowych ofert: <b>{len(fresh)}</b>, w tym okazji: <b>{len(deals)}</b>")
    lines.append("")

    if deals:
        lines.append("🔥 <b>Okazje cenowe</b>")
        for listing in deals[:limit]:
            lines.append(line_for(listing))
            lines.append("")

    if owners:
        lines.append("🙋 <b>Bezpośrednio od właściciela</b>")
        for listing in owners[: max(3, limit // 2)]:
            lines.append(line_for(listing))
            lines.append("")

    shown = len(deals[:limit]) + len(owners[: max(3, limit // 2)])
    remaining = limit - shown
    if rest and remaining > 0:
        lines.append("🏠 <b>Pozostałe nowe</b>")
        for listing in rest[:remaining]:
            lines.append(line_for(listing))
            lines.append("")

    hidden = len(fresh) - shown - min(len(rest), max(0, remaining))

    # Telegram przyjmuje 4096 znaków. Ucinamy CAŁYMI ofertami, bo przycięcie
    # w połowie znacznika HTML rozwaliłoby formatowanie wiadomości.
    stopka_len = 120
    while len("\n".join(lines)) + stopka_len > 3900 and len(lines) > 4:
        removed = lines.pop()
        while lines and not lines[-1].startswith(("🔥", "🙋", "🏠")) and "</a>" not in removed:
            removed = lines.pop()
        if lines and lines[-1].startswith(("🔥", "🙋", "🏠")):
            lines.pop()
        hidden += 1

    if hidden > 0:
        lines.append(f"<i>…i jeszcze {hidden} — reszta w dashboardzie.</i>")

    lines.append("")
    lines.append(f"<i>W bazie łącznie: {total_active} aktywnych ogłoszeń.</i>")
    return "\n".join(lines), len(fresh)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Podsumowanie ofert na Telegram")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=8, help="ile ofert wypisać")
    parser.add_argument("--skip-empty", action="store_true", help="nie wysyłaj, gdy brak nowych")
    parser.add_argument("--dry-run", action="store_true", help="wypisz na ekran, nie wysyłaj")
    args = parser.parse_args()

    init_db()

    with session_scope() as session:
        text, count = build_digest(session, args.hours, args.limit)

    if count == 0 and args.skip_empty:
        print("Brak nowych ofert — pomijam wysyłkę (--skip-empty).")
        return 0

    if args.dry_run:
        print("\n" + "-" * 60)
        print(text)
        print("-" * 60 + f"\n\n({count} nowych ofert)")
        return 0

    notifier = TelegramNotifier()
    if not notifier.is_configured():
        print("=" * 64)
        print("  Telegram nie jest skonfigurowany — nie mam dokąd wysłać.")
        print("")
        print(f"  TELEGRAM_BOT_TOKEN: {'ustawiony' if settings.telegram_bot_token else 'PUSTY'}")
        print(f"  TELEGRAM_CHAT_ID:   {settings.telegram_chat_id or 'PUSTY'}")
        print("")
        print("  Lokalnie: uzupełnij te wartości w pliku .env")
        print("  W chmurze: Settings → Secrets and variables → Actions → Secrets")
        print("=" * 64)
        print("\nTreść, która miała pójść:\n")
        print(text)
        return 1

    await notifier.send_text(text)
    print(f"Wysłano podsumowanie: {count} nowych ofert.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
