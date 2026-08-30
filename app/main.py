"""FastAPI — API dashboardu + serwowanie frontendu."""
from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from . import stats as stats_mod
from .config import BASE_DIR, settings
from .db import get_db, init_db
from .districts import POPULAR_ESTATES, all_regions
from .matching import apply_keyword_filter, build_query
from .models import Favorite, Listing, Notification, SavedFilter, ScrapeRun, iso
from .normalize import (
    CONDITION_DEVELOPER, CONDITION_READY, CONDITION_RENOVATION, CONDITION_UNKNOWN,
)
from .notifiers import available_channels, notify
from .runner import rebuild_dedup_and_stats, run_cycle, upsert_listing
from .scheduler import start_scheduler, stop_scheduler
from .scrapers import available_scrapers
from .scrapers.facebook import parse_free_text_offer
from .schemas import (
    Criteria, FavoriteCreate, FilterCreate, FilterUpdate, ManualListing,
    ScrapeRequest, SearchRequest, SearchResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("api")

STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    log.info("API gotowe: http://%s:%s", settings.host, settings.port)
    yield
    stop_scheduler()


app = FastAPI(
    title="Monitor mieszkań — Wrocław",
    description="Agregacja ofert sprzedaży mieszkań z wielu portali + alerty",
    version="1.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------- prosta ochrona


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Opcjonalne hasło (ustaw APP_PASSWORD w .env). Bez niego API jest otwarte."""
    import os

    password = os.getenv("APP_PASSWORD", "")
    if not password or request.url.path.startswith("/health"):
        return await call_next(request)

    header = request.headers.get("Authorization", "")
    if header.startswith("Basic "):
        import base64

        try:
            decoded = base64.b64decode(header[6:]).decode()
            _user, _, given = decoded.partition(":")
            if secrets.compare_digest(given, password):
                return await call_next(request)
        except Exception:  # noqa: BLE001
            pass

    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Monitor mieszkan"'},
        content="Wymagane hasło",
    )


# ------------------------------------------------------------------ frontend

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse({"error": "brak static/index.html"}, status_code=404)
    return FileResponse(index_file)


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


# ----------------------------------------------------------------- metadane


@app.get("/api/meta")
async def meta(db: Session = Depends(get_db)) -> Dict[str, Any]:
    used_sources = [row[0] for row in db.execute(select(Listing.source).distinct())]
    used_estates = [
        row[0] for row in db.execute(select(Listing.estate).distinct()) if row[0]
    ]
    return {
        "regions": all_regions(),
        "estates": sorted(set(POPULAR_ESTATES) | set(used_estates)),
        "scrapers": [
            {"name": name, "label": cls.label, "requires_js": cls.requires_js}
            for name, cls in available_scrapers().items()
        ],
        "enabled_sources": settings.sources,
        "used_sources": sorted(used_sources),
        "conditions": [
            {"value": CONDITION_READY, "label": "Do zamieszkania"},
            {"value": CONDITION_DEVELOPER, "label": "Stan deweloperski"},
            {"value": CONDITION_RENOVATION, "label": "Do remontu"},
            {"value": CONDITION_UNKNOWN, "label": "Nieokreślony"},
        ],
        "seller_types": [
            {"value": "wlasciciel", "label": "Właściciel (bezpośrednio)"},
            {"value": "biuro", "label": "Biuro nieruchomości"},
            {"value": "deweloper", "label": "Deweloper"},
        ],
        "channels": available_channels(),
        "deal_discount_pct": settings.deal_discount_pct,
    }


@app.get("/api/stats")
async def get_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    total = db.scalar(select(func.count(Listing.id))) or 0
    active = db.scalar(
        select(func.count(Listing.id)).where(Listing.is_active.is_(True))
    ) or 0
    unique = db.scalar(
        select(func.count(Listing.id))
        .where(Listing.is_active.is_(True))
        .where(Listing.is_duplicate.is_(False))
    ) or 0
    deals = db.scalar(
        select(func.count(Listing.id))
        .where(Listing.is_active.is_(True))
        .where(Listing.is_deal.is_(True))
        .where(Listing.is_duplicate.is_(False))
    ) or 0
    by_source = [
        {"source": row[0], "count": row[1]}
        for row in db.execute(
            select(Listing.source, func.count(Listing.id))
            .where(Listing.is_active.is_(True))
            .group_by(Listing.source)
            .order_by(desc(func.count(Listing.id)))
        )
    ]
    last_runs = [
        r.as_dict()
        for r in db.scalars(select(ScrapeRun).order_by(desc(ScrapeRun.id)).limit(10))
    ]
    return {
        "total": total,
        "active": active,
        "unique": unique,
        "duplicates": active - unique,
        "deals": deals,
        "by_source": by_source,
        "last_runs": last_runs,
        "market": stats_mod.market_summary(db),
    }


# ----------------------------------------------------------------- oferty


def _search(db: Session, criteria: Dict[str, Any], limit: int, offset: int) -> SearchResponse:
    stmt = build_query(criteria)
    rows = list(db.scalars(stmt.limit(3000)))
    rows = apply_keyword_filter(rows, criteria)
    total = len(rows)
    page = rows[offset : offset + limit]
    return SearchResponse(
        total=total,
        items=[l.as_dict() for l in page],
        limit=limit,
        offset=offset,
    )


@app.post("/api/search", response_model=SearchResponse)
async def search(req: SearchRequest, db: Session = Depends(get_db)) -> SearchResponse:
    return _search(db, req.criteria.model_dump(), req.limit, req.offset)


@app.get("/api/listings", response_model=SearchResponse)
async def listings(
    limit: int = Query(60, le=300),
    offset: int = 0,
    only_deals: bool = False,
    db: Session = Depends(get_db),
) -> SearchResponse:
    criteria = Criteria(only_deals=only_deals).model_dump()
    return _search(db, criteria, limit, offset)


@app.get("/api/listings/{listing_id}")
async def listing_detail(listing_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(404, "Nie ma takiej oferty")

    data = listing.as_dict()
    median, source_label = stats_mod.reference_median(db, listing)
    data["median_price_per_m2"] = median
    data["median_source"] = source_label

    if listing.dup_group_id:
        siblings = db.scalars(
            select(Listing)
            .where(Listing.dup_group_id == listing.dup_group_id)
            .where(Listing.id != listing.id)
        )
        data["duplicates"] = [
            {"id": s.id, "source": s.source, "url": s.url, "price": s.price} for s in siblings
        ]
    else:
        data["duplicates"] = []
    return data


@app.post("/api/manual-listing")
async def manual_listing(payload: ManualListing, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Wklej post z grupy FB / forum — trafi do tej samej bazy co reszta."""
    raw = parse_free_text_offer(payload.text, payload.url, payload.source or "manual")
    if raw is None:
        raise HTTPException(400, "Nie udało się nic wyciągnąć z tego tekstu")
    listing, is_new = upsert_listing(db, raw)
    db.commit()
    return {"created": is_new, "listing": listing.as_dict()}


# ----------------------------------------------------------------- filtry


@app.get("/api/filters")
async def list_filters(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    return [f.as_dict() for f in db.scalars(select(SavedFilter).order_by(SavedFilter.id))]


@app.post("/api/filters")
async def create_filter(payload: FilterCreate, db: Session = Depends(get_db)) -> Dict[str, Any]:
    saved = SavedFilter(
        name=payload.name,
        criteria=payload.criteria.model_dump(),
        notify_telegram=payload.notify_telegram,
        notify_discord=payload.notify_discord,
        notify_email=payload.notify_email,
        is_active=payload.is_active,
    )
    db.add(saved)
    db.commit()
    return saved.as_dict()


@app.patch("/api/filters/{filter_id}")
async def update_filter(
    filter_id: int, payload: FilterUpdate, db: Session = Depends(get_db)
) -> Dict[str, Any]:
    saved = db.get(SavedFilter, filter_id)
    if saved is None:
        raise HTTPException(404, "Nie ma takiego filtru")
    data = payload.model_dump(exclude_unset=True)
    if "criteria" in data and data["criteria"] is not None:
        saved.criteria = data.pop("criteria")
    for key, value in data.items():
        if value is not None:
            setattr(saved, key, value)
    db.commit()
    return saved.as_dict()


@app.delete("/api/filters/{filter_id}")
async def delete_filter(filter_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    saved = db.get(SavedFilter, filter_id)
    if saved is None:
        raise HTTPException(404, "Nie ma takiego filtru")
    db.delete(saved)
    db.commit()
    return {"deleted": filter_id}


# -------------------------------------------------------------- ulubione


@app.get("/api/favorites")
async def list_favorites(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    out = []
    for fav in db.scalars(select(Favorite).order_by(desc(Favorite.created_at))):
        if fav.listing:
            item = fav.listing.as_dict()
            item["note"] = fav.note
            item["favorited_at"] = iso(fav.created_at)
            out.append(item)
    return out


@app.post("/api/favorites")
async def add_favorite(payload: FavoriteCreate, db: Session = Depends(get_db)) -> Dict[str, Any]:
    if db.get(Listing, payload.listing_id) is None:
        raise HTTPException(404, "Nie ma takiej oferty")
    existing = db.scalar(select(Favorite).where(Favorite.listing_id == payload.listing_id))
    if existing:
        existing.note = payload.note or existing.note
        db.commit()
        return {"status": "updated", "listing_id": payload.listing_id}
    db.add(Favorite(listing_id=payload.listing_id, note=payload.note))
    db.commit()
    return {"status": "added", "listing_id": payload.listing_id}


@app.delete("/api/favorites/{listing_id}")
async def remove_favorite(listing_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    fav = db.scalar(select(Favorite).where(Favorite.listing_id == listing_id))
    if fav is None:
        raise HTTPException(404, "Ta oferta nie jest w ulubionych")
    db.delete(fav)
    db.commit()
    return {"status": "removed", "listing_id": listing_id}


# ---------------------------------------------------------- powiadomienia


@app.get("/api/notifications")
async def notification_history(
    limit: int = Query(80, le=500), db: Session = Depends(get_db)
) -> List[Dict[str, Any]]:
    out = []
    for n in db.scalars(select(Notification).order_by(desc(Notification.sent_at)).limit(limit)):
        item = {
            "id": n.id,
            "channel": n.channel,
            "status": n.status,
            "error": n.error,
            "sent_at": iso(n.sent_at),
            "filter_name": n.saved_filter.name if n.saved_filter else None,
        }
        if n.listing:
            item["listing"] = n.listing.as_dict()
        out.append(item)
    return out


@app.post("/api/notifications/test")
async def test_notification(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Wysyła najnowszą ofertę na wszystkie skonfigurowane kanały (test konfiguracji)."""
    listing = db.scalar(select(Listing).order_by(desc(Listing.id)).limit(1))
    if listing is None:
        raise HTTPException(400, "Baza jest pusta — najpierw uruchom scrapowanie")
    channels = available_channels()
    if not any(channels.values()):
        raise HTTPException(400, "Żaden kanał nie jest skonfigurowany (sprawdź .env)")

    from .notifiers import get_notifier

    results = {}
    for name, configured in channels.items():
        if not configured:
            continue
        try:
            await get_notifier(name).send(listing)
            results[name] = "ok"
        except Exception as exc:  # noqa: BLE001
            results[name] = f"błąd: {exc}"
    return {"listing_id": listing.id, "results": results}


# ------------------------------------------------------------ operacyjne


@app.post("/api/scrape")
async def trigger_scrape(payload: ScrapeRequest) -> Dict[str, Any]:
    """Ręczne odpalenie cyklu (przydatne do testów i po zmianie filtrów)."""
    try:
        return await run_cycle(payload.sources or None, send_alerts=payload.send_alerts)
    except Exception as exc:  # noqa: BLE001
        # Bez tego przeglądarka pokazuje samo „Internal Server Error”, a treść
        # błędu zostaje w oknie terminala, gdzie nikt jej nie szuka.
        log.exception("Cykl scrapowania zakończony błędem")
        raise HTTPException(
            500, f"Scrapowanie nie powiodło się — {type(exc).__name__}: {exc}"
        ) from exc


@app.post("/api/rebuild")
async def rebuild() -> Dict[str, Any]:
    """Przelicza deduplikację, mediany i flagi okazji od nowa."""
    return await rebuild_dedup_and_stats()


@app.get("/api/runs")
async def runs(limit: int = 30, db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    return [
        r.as_dict()
        for r in db.scalars(select(ScrapeRun).order_by(desc(ScrapeRun.id)).limit(limit))
    ]
