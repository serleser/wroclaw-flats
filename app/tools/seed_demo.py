"""Wypełnia bazę realistycznymi danymi demo — do obejrzenia UI bez scrapowania.

    python -m app.tools.seed_demo            # ~120 ofert
    python -m app.tools.seed_demo --clear    # najpierw czyści bazę

Dane są generowane losowo, ale z realnymi widełkami cen za m² dla wrocławskich
osiedli (stan: 2025/2026), żeby wykrywanie okazji miało sens.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys

from sqlalchemy import delete

from .. import dedup, stats
from ..db import init_db, session_scope
from ..models import DistrictStat, Favorite, Listing, Notification, SavedFilter, ScrapeRun
from ..normalize import RawListing
from ..runner import upsert_listing

# osiedle -> (mediana ceny za m2, odchylenie)
ESTATE_PRICES = {
    "Stare Miasto": (16800, 2600), "Śródmieście": (15200, 2200), "Nadodrze": (13400, 1900),
    "Ołbin": (13100, 1800), "Plac Grunwaldzki": (15600, 2100), "Biskupin": (16200, 2400),
    "Sępolno": (15400, 2000), "Krzyki": (14200, 1900), "Gaj": (12900, 1600),
    "Jagodno": (12400, 1500), "Huby": (12700, 1600), "Tarnogaj": (12600, 1500),
    "Borek": (16000, 2300), "Ołtaszyn": (13800, 1800), "Klecina": (13200, 1600),
    "Grabiszyn": (13000, 1700), "Oporów": (12800, 1500), "Muchobór Wielki": (12200, 1400),
    "Nowy Dwór": (11600, 1300), "Popowice": (12900, 1600), "Kozanów": (11400, 1300),
    "Pilczyce": (12100, 1400), "Gądów": (12500, 1500), "Maślice": (11900, 1400),
    "Stabłowice": (11500, 1300), "Leśnica": (11200, 1200), "Żerniki": (12300, 1400),
    "Karłowice": (14100, 1900), "Różanka": (12200, 1400), "Psie Pole": (11300, 1300),
    "Zakrzów": (11000, 1200), "Swojczyce": (11800, 1400), "Lipa Piotrowska": (11700, 1300),
    "Brochów": (10800, 1200), "Szczepin": (13600, 1800),
}

SOURCES = ["olx", "otodom", "nieruchomosci_online", "morizon", "gratka"]
CONDITIONS = ["do_zamieszkania", "do_remontu", "stan_deweloperski", "nieokreslony"]
FEATURES_POOL = ["balkon", "garaz", "winda", "piwnica", "taras", "ogrodek", "komorka"]

TITLE_TEMPLATES = [
    "{rooms}-pokojowe {area} m² na {estate} — {highlight}",
    "Przestronne {rooms} pokoje, {estate}, {area} m²",
    "{estate} | {area} m² | {rooms} pok. | {highlight}",
    "Mieszkanie {area} m² {estate} — {highlight}",
]
HIGHLIGHTS = [
    "do zamieszkania od zaraz", "po generalnym remoncie", "stan deweloperski",
    "do remontu, świetna cena", "z balkonem i miejscem postojowym",
    "cicha okolica, blisko tramwaju", "bez prowizji", "z windą i piwnicą",
]


def make_listing(idx: int) -> RawListing:
    estate = random.choice(list(ESTATE_PRICES))
    median, spread = ESTATE_PRICES[estate]

    area = round(random.uniform(28, 96), 1)
    rooms = max(1, min(5, round(area / 22 + random.uniform(-0.6, 0.6))))
    # co 12. oferta to celowa okazja (15-25% poniżej mediany)
    if idx % 12 == 0:
        ppm = median * random.uniform(0.75, 0.85)
    else:
        ppm = random.gauss(median, spread)
    ppm = max(7000, round(ppm, 0))
    price = round(ppm * area, -2)

    source = random.choice(SOURCES)
    market = "pierwotny" if random.random() < 0.22 else "wtorny"
    seller = (
        "deweloper" if market == "pierwotny" and random.random() < 0.7
        else random.choices(["wlasciciel", "biuro"], weights=[0.28, 0.72])[0]
    )
    condition = (
        "stan_deweloperski" if market == "pierwotny" else random.choice(CONDITIONS)
    )
    highlight = random.choice(HIGHLIGHTS)
    title = random.choice(TITLE_TEMPLATES).format(
        rooms=rooms, area=f"{area:.0f}", estate=estate, highlight=highlight
    )
    features = random.sample(FEATURES_POOL, k=random.randint(1, 4))
    if seller == "wlasciciel":
        features.append("bez_prowizji")

    description = (
        f"{title}. Mieszkanie położone na osiedlu {estate} we Wrocławiu. "
        f"Powierzchnia {area} m², {rooms} pokoje, {random.randint(0,8)} piętro. "
        f"{highlight.capitalize()}. "
        + ("Sprzedaż bezpośrednio od właściciela, bez prowizji. " if seller == "wlasciciel" else "")
        + ("Księga wieczysta uregulowana. " if random.random() < 0.5 else "")
        + f"Dostępne od {random.choice(['zaraz', 'lipca', 'września'])}."
    )

    return RawListing(
        source=source,
        source_id=f"demo-{idx}",
        url=f"https://przyklad.pl/{source}/oferta-{idx}",
        title=title,
        description=description,
        price=price,
        area=area,
        rooms=rooms,
        price_per_m2=round(ppm, 2),
        location_raw=f"Wrocław, {estate}",
        estate=estate,
        market=market,
        seller_type=seller,
        condition=condition,
        floor=random.randint(0, 9),
        features=features,
        image_url=f"https://picsum.photos/seed/flat{idx}/640/480",
    ).finalize()


def make_duplicate(base: RawListing, idx: int) -> RawListing:
    """Ta sama nieruchomość wystawiona przez inne biuro na innym portalu."""
    other = random.choice([s for s in SOURCES if s != base.source])
    return RawListing(
        source=other,
        source_id=f"demo-dup-{idx}",
        url=f"https://przyklad.pl/{other}/oferta-dup-{idx}",
        title=base.title.replace("Mieszkanie", "Na sprzedaż mieszkanie"),
        description=base.description + " Oferta biura partnerskiego.",
        price=round((base.price or 0) * random.uniform(0.99, 1.03), -2),
        area=base.area,
        rooms=base.rooms,
        location_raw=base.location_raw,
        estate=base.estate,
        market=base.market,
        seller_type="biuro",
        condition=base.condition,
        floor=base.floor,
        features=base.features,
        image_url=base.image_url,
    ).finalize()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Dane demo")
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args()

    init_db()
    random.seed(42)

    with session_scope() as session:
        if args.clear:
            for model in (Notification, Favorite, ScrapeRun, DistrictStat, SavedFilter, Listing):
                session.execute(delete(model))
            session.flush()
            print("🗑  Baza wyczyszczona")

        created = 0
        for idx in range(args.count):
            raw = make_listing(idx)
            listing, is_new = upsert_listing(session, raw)
            if is_new:
                dedup.attach_to_group(session, listing)
                created += 1
            # co 8. oferta dostaje bliźniaka na innym portalu
            if idx % 8 == 0:
                dup_raw = make_duplicate(raw, idx)
                dup, dup_new = upsert_listing(session, dup_raw)
                if dup_new:
                    dedup.attach_to_group(session, dup)
                    created += 1

        session.flush()
        stat_info = stats.recompute_stats(session)
        deals = stats.mark_deals(session)

        if not session.query(SavedFilter).count():
            session.add(
                SavedFilter(
                    name="Nasze kryteria (demo)",
                    criteria={
                        "price_max": 850_000,
                        "ppm_max": 14_000,
                        "area_min": 45,
                        "rooms": [2, 3],
                        "market": "oba",
                        "seller_types": ["wlasciciel", "biuro"],
                        "regions": ["Krzyki", "Fabryczna"],
                        "keywords_exclude": ["poddasze"],
                        "hide_duplicates": True,
                        "sort": "deal",
                    },
                )
            )

    dups = 0
    with session_scope() as session:
        dups = session.query(Listing).filter(Listing.is_duplicate.is_(True)).count()

    print(f"✅ Dodano {created} ofert")
    print(f"🔁 Wykrytych duplikatów: {dups}")
    print(f"📊 Policzonych median: {stat_info['buckets']}")
    print(f"🔥 Ofert oznaczonych jako okazja: {deals}")
    print("\nUruchom teraz:  python run.py   i wejdź na http://127.0.0.1:8000")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
