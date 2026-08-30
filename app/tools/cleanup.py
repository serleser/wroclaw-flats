"""Porządki w bazie — usuwa oferty, które nie są z Wrocławia.

    python -m app.tools.cleanup            # tylko pokazuje, co by usunął
    python -m app.tools.cleanup --usun     # faktycznie usuwa i przelicza mediany

Potrzebne, gdy do bazy zdążyły trafić ogłoszenia spoza Wrocławia — zanim
zadziałał filtr miasta. Takie oferty są podwójnie szkodliwe: same w sobie są
bezużyteczne, a dodatkowo zaniżają mediany dzielnic, przez co część prawdziwych
wrocławskich ofert przestaje być oznaczana jako okazja.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from .. import stats
from ..db import init_db, session_scope
from ..districts import location_verdict
from ..models import Listing

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def haystack(listing: Listing) -> str:
    return " ".join(
        filter(None, [listing.location_raw, listing.title, listing.description, listing.url])
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Usuwa oferty spoza Wrocławia")
    parser.add_argument("--usun", action="store_true", help="faktycznie usuń (bez tego tylko podgląd)")
    args = parser.parse_args()

    init_db()

    with session_scope() as session:
        listings = list(session.scalars(select(Listing)))
        suspects = []
        for listing in listings:
            ok, reason = location_verdict(haystack(listing))
            if not ok:
                suspects.append((listing, reason))

        print(f"\nOfert w bazie: {len(listings)}")
        print(f"Nie potwierdzają Wrocławia: {len(suspects)}\n")

        if not suspects:
            print("Baza jest czysta — nie ma czego usuwać.\n")
            return 0

        for listing, reason in suspects[:40]:
            price = f"{listing.price:,.0f} zł".replace(",", " ") if listing.price else "—"
            place = listing.location_raw or "(brak lokalizacji)"
            print(f"  [{listing.source}] {price:>12} | {place[:40]:<40} | {reason}")
            print(f"      {listing.title[:88]}")
        if len(suspects) > 40:
            print(f"  … i jeszcze {len(suspects) - 40}")

        if not args.usun:
            print("\nTo był tylko podgląd. Żeby usunąć te oferty, uruchom:")
            print("    python -m app.tools.cleanup --usun\n")
            return 0

        for listing, _ in suspects:
            session.delete(listing)
        session.flush()

        info = stats.recompute_stats(session)
        deals = stats.mark_deals(session)

    print(f"\n✅ Usunięto {len(suspects)} ofert")
    print(f"📊 Przeliczono mediany: {info['buckets']}")
    print(f"🔥 Ofert oznaczonych jako okazja: {deals}\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
