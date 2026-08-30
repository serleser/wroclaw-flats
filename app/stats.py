"""Statystyki rynkowe i wykrywanie okazji cenowych.

Okazja = cena za m2 istotnie poniżej MEDIANY dla tego samego osiedla
(fallback: rejon, potem całe miasto) w tym samym segmencie rynku.

Używamy mediany, nie średniej — jeden penthouse za 30 tys./m2 nie zaburzy wtedy
progu okazji dla całego osiedla.
"""
from __future__ import annotations

import datetime as dt
import statistics
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import DistrictStat, Listing, utcnow

MIN_SAMPLE = 5          # poniżej tylu ofert mediana osiedla jest niewiarygodna
LOOKBACK_DAYS = 120     # z jak starych ofert liczymy medianę


def _collect(session: Session) -> List[Listing]:
    since = utcnow() - dt.timedelta(days=LOOKBACK_DAYS)
    stmt = (
        select(Listing)
        .where(Listing.price_per_m2.is_not(None))
        .where(Listing.price_per_m2 > 3000)
        .where(Listing.price_per_m2 < 40000)
        .where(Listing.is_duplicate.is_(False))
        .where(Listing.first_seen_at >= since)
    )
    return list(session.scalars(stmt))


def recompute_stats(session: Session) -> Dict[str, int]:
    """Przelicza mediany cen za m2 i zapisuje do tabeli district_stats."""
    listings = _collect(session)
    buckets: Dict[Tuple[str, str, str], List[float]] = {}

    for l in listings:
        markets = ["all"] + ([l.market] if l.market else [])
        for market in markets:
            if l.estate:
                buckets.setdefault(("estate", l.estate, market), []).append(l.price_per_m2)
            if l.region:
                buckets.setdefault(("region", l.region, market), []).append(l.price_per_m2)
            buckets.setdefault(("city", "Wrocław", market), []).append(l.price_per_m2)

    existing = {
        (s.scope, s.name, s.market): s for s in session.scalars(select(DistrictStat))
    }

    written = 0
    for (scope, name, market), values in buckets.items():
        if len(values) < MIN_SAMPLE:
            continue
        median = round(statistics.median(values), 2)
        stat = existing.get((scope, name, market))
        if stat is None:
            stat = DistrictStat(scope=scope, name=name, market=market)
            session.add(stat)
        stat.median_price_per_m2 = median
        stat.sample_size = len(values)
        stat.updated_at = utcnow()
        written += 1

    session.flush()
    return {"buckets": written, "listings": len(listings)}


def _stat_lookup(session: Session) -> Dict[Tuple[str, str, str], DistrictStat]:
    return {(s.scope, s.name, s.market): s for s in session.scalars(select(DistrictStat))}


def reference_median(
    session: Session,
    listing: Listing,
    lookup: Optional[Dict[Tuple[str, str, str], DistrictStat]] = None,
) -> Tuple[Optional[float], str]:
    """Zwraca (mediana, opis źródła) dla danej oferty."""
    lookup = lookup if lookup is not None else _stat_lookup(session)
    market = listing.market or "all"
    candidates = [
        ("estate", listing.estate, market),
        ("estate", listing.estate, "all"),
        ("region", listing.region, market),
        ("region", listing.region, "all"),
        ("city", "Wrocław", market),
        ("city", "Wrocław", "all"),
    ]
    for scope, name, mkt in candidates:
        if not name:
            continue
        stat = lookup.get((scope, name, mkt))
        if stat and stat.sample_size >= MIN_SAMPLE:
            label = {"estate": "osiedle", "region": "rejon", "city": "miasto"}[scope]
            return stat.median_price_per_m2, f"{label}: {name}"
    return None, ""


def mark_deals(session: Session) -> int:
    """Ustawia is_deal / deal_score na wszystkich aktywnych ofertach."""
    lookup = _stat_lookup(session)
    threshold = settings.deal_discount_pct
    count = 0

    stmt = select(Listing).where(Listing.is_active.is_(True)).where(Listing.price_per_m2.is_not(None))
    for listing in session.scalars(stmt):
        median, _src = reference_median(session, listing, lookup)
        if not median:
            listing.is_deal, listing.deal_score = False, None
            continue
        discount = round((median - listing.price_per_m2) / median * 100, 1)
        listing.deal_score = discount
        listing.is_deal = discount >= threshold
        if listing.is_deal:
            count += 1

    session.flush()
    return count


def market_summary(session: Session) -> Dict[str, object]:
    """Podsumowanie do nagłówka dashboardu."""
    stats = list(session.scalars(select(DistrictStat).where(DistrictStat.market == "all")))
    city = next((s for s in stats if s.scope == "city"), None)
    estates = sorted(
        [
            {
                "name": s.name,
                "median_price_per_m2": s.median_price_per_m2,
                "sample_size": s.sample_size,
            }
            for s in stats
            if s.scope == "estate"
        ],
        key=lambda x: x["median_price_per_m2"],
    )
    regions = sorted(
        [
            {
                "name": s.name,
                "median_price_per_m2": s.median_price_per_m2,
                "sample_size": s.sample_size,
            }
            for s in stats
            if s.scope == "region"
        ],
        key=lambda x: x["median_price_per_m2"],
    )
    return {
        "city_median_price_per_m2": city.median_price_per_m2 if city else None,
        "city_sample_size": city.sample_size if city else 0,
        "estates": estates,
        "regions": regions,
    }
