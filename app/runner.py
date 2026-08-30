"""Pipeline: scrapowanie -> zapis -> deduplikacja -> statystyki -> alerty.

Jeden cykl (`run_cycle`) wygląda tak:

  1. dla każdego aktywnego źródła: pobierz oferty (równolegle, ale z throttlingiem)
  2. upsert do bazy: nowa oferta / aktualizacja ceny (z historią) / odświeżenie last_seen
  3. dla nowych ofert: przypisz do grupy duplikatów
  4. oznacz jako nieaktywne oferty, których nie widzieliśmy od X dni
  5. przelicz mediany cen za m2 i flagę "okazja"
  6. dla każdego aktywnego filtru: znajdź nowe oferty, które pasują -> wyślij alert
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import dedup, stats
from .config import settings
from .db import session_scope
from .matching import matches
from .models import Listing, SavedFilter, ScrapeRun, utcnow
from .normalize import RawListing
from .notifiers import notify
from .scrapers import available_scrapers, get_scraper

log = logging.getLogger("runner")

STALE_AFTER_DAYS = 7


# ---------------------------------------------------------------- pomocnicze


def _parse_dt(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    text = str(value).replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = dt.datetime.fromisoformat(text) if fmt is None else dt.datetime.strptime(text, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def active_sources() -> List[str]:
    registry = available_scrapers()
    wanted = settings.sources
    unknown = [s for s in wanted if s not in registry]
    if unknown:
        log.warning("Nieznane źródła w ENABLED_SOURCES: %s", ", ".join(unknown))
    return [s for s in wanted if s in registry]


# ------------------------------------------------------------------- upsert


def upsert_listing(session: Session, raw: RawListing) -> tuple[Listing, bool]:
    """Zwraca (obiekt, czy_nowa)."""
    existing = session.scalar(
        select(Listing).where(
            Listing.source == raw.source, Listing.source_id == raw.source_id
        )
    )

    if existing is None:
        listing = Listing(
            source=raw.source,
            source_id=raw.source_id,
            url=raw.url,
            title=raw.title,
            description=raw.description,
            image_url=raw.image_url,
            price=raw.price,
            price_per_m2=raw.price_per_m2,
            area=raw.area,
            rooms=raw.rooms,
            floor=raw.floor,
            year_built=raw.year_built,
            location_raw=raw.location_raw,
            estate=raw.estate,
            region=raw.region,
            market=raw.market,
            seller_type=raw.seller_type,
            condition=raw.condition,
            features=raw.features,
            posted_at=_parse_dt(raw.posted_at),
            price_history=[{"date": utcnow().isoformat(), "price": raw.price}] if raw.price else [],
            raw=raw.extra,
        )
        session.add(listing)
        session.flush()
        return listing, True

    # --- aktualizacja istniejącej ---
    existing.last_seen_at = utcnow()
    existing.is_active = True

    if raw.price and existing.price and abs(raw.price - existing.price) > 1:
        history = list(existing.price_history or [])
        history.append({"date": utcnow().isoformat(), "price": raw.price})
        existing.price_history = history[-30:]
        log.info(
            "[%s] zmiana ceny %s: %.0f -> %.0f zł",
            existing.source, existing.source_id, existing.price, raw.price,
        )

    for field in (
        "url", "title", "description", "image_url", "price", "price_per_m2", "area",
        "rooms", "floor", "location_raw", "estate", "region", "market",
        "seller_type", "condition", "features",
    ):
        value = getattr(raw, field)
        if value not in (None, "", []):
            setattr(existing, field, value)

    return existing, False


def deactivate_stale(session: Session, days: int = STALE_AFTER_DAYS) -> int:
    cutoff = utcnow() - dt.timedelta(days=days)
    stale = list(
        session.scalars(
            select(Listing).where(Listing.is_active.is_(True)).where(Listing.last_seen_at < cutoff)
        )
    )
    for listing in stale:
        listing.is_active = False
    return len(stale)


# ------------------------------------------------------------------ scraping


#: ile ofert filtr miasta odrzucił w ostatnim cyklu, per źródło
rejected_by_source: Dict[str, int] = {}


async def scrape_source(source: str) -> List[RawListing]:
    scraper = get_scraper(source)
    if scraper is None:
        log.warning("Brak scrapera o nazwie %s", source)
        return []
    items = await scraper.run()
    rejected_by_source[source] = getattr(scraper, "rejected_out_of_city", 0)
    return items


async def run_scrapers(sources: Sequence[str]) -> Dict[str, Any]:
    """Uruchamia scrapery równolegle (throttling per-host i tak je rozsuwa)."""
    tasks = {src: asyncio.create_task(scrape_source(src)) for src in sources}
    out: Dict[str, Any] = {}
    for src, task in tasks.items():
        try:
            out[src] = await task
        except Exception as exc:  # noqa: BLE001
            out[src] = exc
    return out


# --------------------------------------------------------------------- cykl


async def run_cycle(
    sources: Optional[Sequence[str]] = None,
    send_alerts: bool = True,
) -> Dict[str, Any]:
    sources = list(sources) if sources else active_sources()
    if not sources:
        return {"error": "brak aktywnych źródeł (sprawdź ENABLED_SOURCES w .env)"}

    log.info("=== start cyklu: %s ===", ", ".join(sources))
    started = utcnow()
    scraped = await run_scrapers(sources)

    summary: Dict[str, Any] = {
        "started_at": started.isoformat(),
        "sources": {},
        "new": 0,
        "updated": 0,
        "duplicates": 0,
        "alerts_sent": 0,
    }
    new_ids: List[int] = []

    with session_scope() as session:
        first_run = session.scalar(select(Listing.id).limit(1)) is None

        for source in sources:
            result = scraped.get(source)
            run = ScrapeRun(source=source, started_at=started)
            session.add(run)

            if isinstance(result, Exception):
                run.status, run.error, run.finished_at = "error", str(result)[:900], utcnow()
                summary["sources"][source] = {"status": "error", "error": str(result)[:300]}
                continue

            items: List[RawListing] = result or []
            src_new = src_updated = src_dups = 0

            for raw in items:
                # SAVEPOINT: jedna wadliwa oferta nie może wycofać całej partii
                # (zwykły rollback unieważniłby też wszystko zapisane wcześniej)
                try:
                    with session.begin_nested():
                        listing, is_new = upsert_listing(session, raw)
                        if is_new:
                            is_dup = dedup.attach_to_group(session, listing)
                        else:
                            is_dup = False
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "[%s] pomijam ofertę %s — %s: %s",
                        source, raw.source_id, type(exc).__name__, exc,
                    )
                    continue

                if is_new:
                    src_new += 1
                    src_dups += 1 if is_dup else 0
                    new_ids.append(listing.id)
                else:
                    src_updated += 1

            session.flush()
            run.found, run.new, run.updated, run.duplicates = len(items), src_new, src_updated, src_dups
            run.finished_at, run.status = utcnow(), "ok"
            summary["sources"][source] = {
                "status": "ok", "found": len(items), "new": src_new,
                "updated": src_updated, "duplicates": src_dups,
                "odrzucone_spoza_wroclawia": rejected_by_source.get(source, 0),
            }
            summary["new"] += src_new
            summary["updated"] += src_updated
            summary["duplicates"] += src_dups

        summary["deactivated"] = deactivate_stale(session)
        stats.recompute_stats(session)
        summary["deals"] = stats.mark_deals(session)

    # --- alerty (osobna sesja: chcemy mieć już policzone flagi is_deal) ---
    if send_alerts and new_ids:
        if first_run and settings.silent_first_run:
            log.info("Pierwszy przebieg — pomijam wysyłkę %s alertów (SILENT_FIRST_RUN=1)", len(new_ids))
            summary["alerts_skipped_first_run"] = len(new_ids)
        else:
            summary["alerts_sent"] = await dispatch_alerts(new_ids)

    log.info(
        "=== koniec cyklu: +%s nowych, %s zaktualizowanych, %s duplikatów, %s alertów ===",
        summary["new"], summary["updated"], summary["duplicates"], summary["alerts_sent"],
    )
    return summary


async def dispatch_alerts(listing_ids: Sequence[int]) -> int:
    """Dla każdego aktywnego filtru wysyła alerty o pasujących nowych ofertach."""
    sent = 0
    with session_scope() as session:
        filters = list(
            session.scalars(select(SavedFilter).where(SavedFilter.is_active.is_(True)))
        )
        if not filters:
            log.info("Brak aktywnych filtrów — nie ma czego wysyłać")
            return 0

        listings = list(session.scalars(select(Listing).where(Listing.id.in_(list(listing_ids)))))
        # najpierw okazje, potem najtańsze za m2 — gdyby limit obciął listę
        listings.sort(key=lambda l: (not l.is_deal, l.price_per_m2 or 1e9))

        for listing in listings:
            if sent >= settings.max_notifications_per_run:
                log.warning("Limit %s powiadomień na cykl osiągnięty", settings.max_notifications_per_run)
                break
            for saved_filter in filters:
                if not matches(listing, saved_filter.criteria):
                    continue
                results = await notify(session, listing, saved_filter)
                if any(ok for _, ok in results):
                    sent += 1
                break   # jedna oferta = jeden alert, nawet jeśli pasuje do kilku filtrów

    return sent


async def rebuild_dedup_and_stats() -> Dict[str, Any]:
    """Przelicza deduplikację i statystyki od zera (np. po zmianie progu)."""
    with session_scope() as session:
        listings = list(session.scalars(select(Listing).where(Listing.is_active.is_(True))))
        for listing in listings:
            listing.dup_group_id, listing.is_duplicate = None, False
            listing.fingerprint = dedup.make_fingerprint(
                listing.region, listing.area, listing.rooms
            )
        session.flush()

        dups = 0
        for listing in listings:
            if dedup.attach_to_group(session, listing):
                dups += 1

        stat_info = stats.recompute_stats(session)
        deals = stats.mark_deals(session)

    return {"listings": len(listings), "duplicates": dups, "deals": deals, **stat_info}
