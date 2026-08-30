"""Deduplikacja ofert między portalami.

Problem: to samo mieszkanie wrzuca 3 biura + właściciel na 5 portali.
Rozwiązanie dwustopniowe:

1. BLOK (tani filtr): fingerprint = hash(rejon, metraż zaokrąglony do 1 m2, pokoje).
   Porównujemy tylko oferty z tego samego bloku + bloków sąsiednich (±1 m2),
   więc nie robimy porównania każdy-z-każdym (O(n^2) na 50k ofert = zabójstwo).

2. SCORING (drogi filtr): dla kandydatów liczymy ważone podobieństwo:
   - metraż (waga 0.25)
   - cena (0.25)
   - piętro (0.10)
   - lokalizacja/osiedle (0.15)
   - podobieństwo opisu i tytułu (0.25)
   Powyżej progu DEDUP_THRESHOLD -> ta sama grupa (dup_group_id).

W grupie "reprezentantem" (is_duplicate=False) zostaje oferta od właściciela,
a jeśli nie ma — najstarsza / najtańsza. Reszta jest oznaczana jako duplikat
i domyślnie ukrywana w UI (można pokazać przełącznikiem).
"""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from typing import Iterable, List, Optional, Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import settings
from .districts import normalize_text
from .models import Listing, as_utc
from .normalize import SELLER_OWNER

# --------------------------------------------------------------- fingerprint


def make_fingerprint(
    region: Optional[str], area: Optional[float], rooms: Optional[int], offset: int = 0
) -> str:
    """Klucz blokujący. `offset` pozwala wygenerować klucz sąsiedni (±1 m2)."""
    area_key = int(round(area)) + offset if area else -1
    payload = f"{normalize_text(region or '?')}|{area_key}|{rooms or '?'}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]


def candidate_fingerprints(
    region: Optional[str], area: Optional[float], rooms: Optional[int]
) -> List[str]:
    return [make_fingerprint(region, area, rooms, off) for off in (-1, 0, 1)]


# ------------------------------------------------------------------ scoring

_STOPWORDS = {
    "mieszkanie", "sprzedam", "sprzedaz", "wroclaw", "pokoje", "pokoi", "pokoj",
    "oferta", "nieruchomosc", "m2", "zl", "na", "do", "w", "z", "i", "o", "od",
    "the", "ul", "ulica", "os", "osiedle",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", normalize_text(text))
    return {w for w in words if w not in _STOPWORDS}


def text_similarity(a: str, b: str) -> float:
    """Kombinacja Jaccarda na tokenach i SequenceMatcher — tanie i skuteczne."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    seq = SequenceMatcher(None, normalize_text(a)[:400], normalize_text(b)[:400]).ratio()
    return 0.6 * jaccard + 0.4 * seq


def _numeric_similarity(a: Optional[float], b: Optional[float], tolerance: float) -> Optional[float]:
    """1.0 = identyczne, 0.0 = różnica >= tolerance (względna)."""
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    diff = abs(a - b) / max(a, b)
    return max(0.0, 1.0 - diff / tolerance)


def similarity(a: Listing, b: Listing) -> float:
    """Ważone podobieństwo dwóch ofert (0..1)."""
    parts: list[tuple[float, float]] = []  # (waga, wartość)

    s_area = _numeric_similarity(a.area, b.area, tolerance=0.06)
    if s_area is not None:
        parts.append((0.25, s_area))

    s_price = _numeric_similarity(a.price, b.price, tolerance=0.10)
    if s_price is not None:
        parts.append((0.25, s_price))

    if a.floor is not None and b.floor is not None:
        parts.append((0.10, 1.0 if a.floor == b.floor else 0.0))

    if a.rooms and b.rooms:
        parts.append((0.10, 1.0 if a.rooms == b.rooms else 0.0))

    loc_a = f"{a.estate or ''} {a.location_raw}"
    loc_b = f"{b.estate or ''} {b.location_raw}"
    if loc_a.strip() and loc_b.strip():
        estate_match = 1.0 if (a.estate and a.estate == b.estate) else text_similarity(loc_a, loc_b)
        parts.append((0.15, estate_match))

    txt = text_similarity(f"{a.title} {a.description}", f"{b.title} {b.description}")
    parts.append((0.25, txt))

    # to samo zdjęcie na dwóch portalach = niemal pewny duplikat
    if a.image_url and b.image_url:
        name_a = a.image_url.rsplit("/", 1)[-1].split("?")[0]
        name_b = b.image_url.rsplit("/", 1)[-1].split("?")[0]
        if len(name_a) > 8 and name_a == name_b:
            parts.append((0.35, 1.0))

    total_weight = sum(w for w, _ in parts)
    if total_weight == 0:
        return 0.0
    return sum(w * v for w, v in parts) / total_weight


# --------------------------------------------------------------- grupowanie


def find_duplicate_group(session: Session, listing: Listing) -> Optional[str]:
    """Szuka istniejącej grupy duplikatów dla nowej oferty. Zwraca dup_group_id."""
    fps = candidate_fingerprints(listing.region, listing.area, listing.rooms)
    stmt = (
        select(Listing)
        .where(Listing.fingerprint.in_(fps))
        .where(Listing.id != listing.id)
        .where(Listing.is_active.is_(True))
        .limit(200)
    )
    best_score, best_other = 0.0, None
    for other in session.scalars(stmt):
        if other.source == listing.source and other.source_id == listing.source_id:
            continue
        score = similarity(listing, other)
        if score > best_score:
            best_score, best_other = score, other

    if best_other is not None and best_score >= settings.dedup_threshold:
        return best_other.dup_group_id or f"grp_{best_other.id}"
    return None


def attach_to_group(session: Session, listing: Listing) -> bool:
    """Przypisuje ofertę do grupy duplikatów. Zwraca True, jeśli to duplikat."""
    listing.fingerprint = make_fingerprint(listing.region, listing.area, listing.rooms)
    group_id = find_duplicate_group(session, listing)

    if group_id is None:
        listing.dup_group_id = None
        listing.is_duplicate = False
        return False

    listing.dup_group_id = group_id
    session.flush()

    members: Sequence[Listing] = list(
        session.scalars(select(Listing).where(Listing.dup_group_id == group_id))
    )
    # dołóż "założyciela" grupy, jeśli jeszcze nie ma group_id
    try:
        founder_id = int(group_id.replace("grp_", ""))
    except ValueError:
        founder_id = None
    if founder_id:
        founder = session.get(Listing, founder_id)
        if founder is not None:
            founder.dup_group_id = group_id
            if founder not in members:
                members = list(members) + [founder]

    _elect_representative(members)
    return listing.is_duplicate


def _elect_representative(members: Iterable[Listing]) -> None:
    """Wybiera jedną ofertę jako 'główną' w grupie duplikatów."""
    members = [m for m in members if m is not None]
    if not members:
        return

    def rank(item: Listing) -> tuple:
        # Uwaga: klucz sortowania musi być jednorodny. Oferta świeżo utworzona ma
        # datę ze strefą czasową, a odczytana z SQLite — bez. Porównanie takiej
        # pary rzuca TypeError, dlatego sprowadzamy datę do liczby sekund.
        seen = as_utc(item.first_seen_at)
        seen_key = seen.timestamp() if seen else float("inf")
        return (
            0 if item.seller_type == SELLER_OWNER else 1,   # najpierw właściciel
            item.price if item.price else float("inf"),     # potem najtańsza
            seen_key,                                       # potem najstarsza
            item.id or 0,                                   # rozstrzygnięcie remisu
        )

    representative = sorted(members, key=rank)[0]
    for m in members:
        m.is_duplicate = m.id != representative.id
