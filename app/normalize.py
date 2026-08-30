"""Normalizacja surowych danych z portali do jednolitego formatu RawListing.

Każdy scraper zwraca `RawListing` — dalej pipeline (dedup, matching, alerty)
nie wie już, z jakiego portalu pochodzi oferta.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .districts import detect_estate, detect_region, normalize_text

# ------------------------------------------------------------------ słowniki

MARKET_PRIMARY = "pierwotny"
MARKET_SECONDARY = "wtorny"

SELLER_OWNER = "wlasciciel"
SELLER_AGENCY = "biuro"
SELLER_DEVELOPER = "deweloper"

CONDITION_READY = "do_zamieszkania"
CONDITION_RENOVATION = "do_remontu"
CONDITION_DEVELOPER = "stan_deweloperski"
CONDITION_UNKNOWN = "nieokreslony"

_CONDITION_PATTERNS = [
    (CONDITION_DEVELOPER, r"stan\s*dewelopersk|do\s*wykonczenia|stan\s*surowy"),
    (CONDITION_RENOVATION, r"do\s*remontu|do\s*odswiezenia|wymaga\s*remontu|do\s*generalnego"),
    (CONDITION_READY, r"do\s*zamieszkania|gotowe\s*do\s*wprowadzenia|po\s*remoncie|wysoki\s*standard|bardzo\s*dobry\s*stan"),
]

# cechy wyłapywane z tytułu+opisu -> wygodne do filtrów słów kluczowych
FEATURE_PATTERNS = {
    "balkon": r"\bbalkon",
    "taras": r"\btaras",
    "ogrodek": r"\bogrod(ek|kiem|ku)?\b",
    "garaz": r"\bgara(z|ż)|miejsce\s*postojowe|hala\s*gara",
    "winda": r"\bwind(a|y|ą)\b",
    "piwnica": r"\bpiwnic",
    "komorka": r"komork\w*\s*lokatorsk",
    "ksiega_wieczysta": r"\bksi(e|ę)g\w*\s*wieczyst|\bkw\b",
    "bez_prowizji": r"bez\s*prowizji|0\s*%\s*prowizji|brak\s*prowizji",
    "prowizja": r"\bprowizj",
    "parter": r"\bparter\b",
    "poddasze": r"\bpoddasz",
}


@dataclass
class RawListing:
    """Ujednolicona oferta zwracana przez każdy scraper."""

    source: str                      # 'olx', 'otodom', ...
    source_id: str                   # id oferty w obrębie portalu
    url: str
    title: str
    price: Optional[float] = None            # PLN, cena całkowita
    area: Optional[float] = None             # m2
    rooms: Optional[int] = None
    price_per_m2: Optional[float] = None
    location_raw: str = ""
    estate: Optional[str] = None             # osiedle
    region: Optional[str] = None             # rejon (grupa dzielnic)
    market: Optional[str] = None             # pierwotny / wtorny
    seller_type: Optional[str] = None        # wlasciciel / biuro / deweloper
    condition: Optional[str] = None
    floor: Optional[int] = None
    year_built: Optional[int] = None
    description: str = ""
    image_url: Optional[str] = None
    posted_at: Optional[str] = None          # ISO string, jeśli portal podaje
    features: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def finalize(self) -> "RawListing":
        """Domyka brakujące pola na podstawie tego, co udało się wyciągnąć."""
        text = f"{self.title} {self.description} {self.location_raw}"

        if self.price and self.area and not self.price_per_m2:
            self.price_per_m2 = round(self.price / self.area, 2)
        if self.price_per_m2 and self.area and not self.price:
            self.price = round(self.price_per_m2 * self.area, 2)

        if not self.rooms:
            self.rooms = parse_rooms(text)
        if not self.area:
            self.area = parse_area(text)
        if not self.estate:
            self.estate = detect_estate(self.location_raw) or detect_estate(self.title)
        if not self.region:
            self.region = detect_region(self.location_raw) or detect_region(
                self.estate or ""
            ) or detect_region(self.title)
        if not self.market:
            self.market = guess_market(text, self.seller_type)
        if not self.seller_type:
            self.seller_type = guess_seller_type(text)
        elif self.seller_type == SELLER_AGENCY and re.search(
            r"\bdeweloper|od\s*dewelopera", normalize_text(text)
        ):
            # portale oznaczają dewelopera jako "firmę" — doprecyzowujemy z treści,
            # bo filtr "Typ sprzedawcy: Deweloper" ma wtedy realny sens
            self.seller_type = SELLER_DEVELOPER
        if not self.condition:
            self.condition = guess_condition(text)
        if not self.features:
            self.features = extract_features(text)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------ parsery

_NUM_RE = re.compile(r"(\d[\d\s .,]*)")


def parse_number(value: Any) -> Optional[float]:
    """'749 000 zł' -> 749000.0 ; '54,30 m²' -> 54.3"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _NUM_RE.search(str(value))
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace(" ", "")
    # 1.234.567,89 / 1,234,567.89 / 54,3
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        # przecinek jako separator dziesiętny tylko jeśli po nim <= 2 cyfry
        head, _, tail = raw.rpartition(",")
        raw = f"{head}.{tail}" if len(tail) <= 2 else raw.replace(",", "")
    elif raw.count(".") == 1 and len(raw.split(".")[1]) == 3:
        raw = raw.replace(".", "")  # 749.000 -> 749000
    else:
        raw = raw.replace(".", "") if raw.count(".") > 1 else raw
    try:
        return float(raw)
    except ValueError:
        return None


def parse_price(value: Any) -> Optional[float]:
    price = parse_number(value)
    if price is None:
        return None
    # odsiej ewidentne śmieci ("Zapytaj o cenę" -> 0, ceny za m2 wpisane jako cena)
    if price < 30_000:
        return None
    return price


def price_from_text(text: str) -> Optional[float]:
    """Wyciąga cenę CAŁKOWITĄ ze swobodnego tekstu (post z FB, opis, karta HTML).

    Nie bierze pierwszej lepszej liczby (bo to zwykle metraż) — szuka liczby
    przy słowie 'zł'/'PLN'/'cena' i odrzuca wartości wyglądające na cenę za m².
    """
    if not text:
        return None
    candidates: list[float] = []

    # (wzorzec, mnożnik)
    patterns = [
        (r"(\d[\d\s.,]{0,8}?)\s*(?:tys(?:\.|iące|iecy)?|k)\s*(?:zł|zl|pln)?\b", 1000),
        (r"cena\D{0,15}?(\d[\d\s.,]{3,})", 1),
        (r"(\d[\d\s.,]{3,})\s*(?:zł|zl|pln)\b", 1),
    ]
    for pattern, multiplier in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            # pomiń, jeśli tuż za liczbą stoi "/m2" — to cena za metr, nie całkowita
            tail = text[m.end() : m.end() + 8].lower()
            if "/m" in tail or "za m" in tail:
                continue
            value = parse_number(m.group(1))
            if value is None:
                continue
            value *= multiplier
            if 50_000 <= value <= 20_000_000:
                candidates.append(value)

    if not candidates:
        return None
    # przy kilku kandydatach największa liczba to niemal zawsze cena całkowita
    return max(candidates)


def parse_area(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value)
    m = re.search(r"(\d+[.,]?\d*)\s*(?:m2|m²|m\b|mkw)", text, re.IGNORECASE)
    area = parse_number(m.group(1)) if m else parse_number(text)
    if area is None:
        return None
    return area if 8 <= area <= 400 else None


def parse_rooms(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and 1 <= value <= 10:
        return int(value)
    text = normalize_text(str(value))
    m = re.search(r"(\d+)\s*(?:-|\s)?\s*pok", text)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 10 else None
    words = {
        "kawalerka": 1, "jednopokojow": 1, "dwupokojow": 2, "trzypokojow": 3,
        "czteropokojow": 4, "pieciopokojow": 5,
    }
    for w, n in words.items():
        if w in text:
            return n
    return None


def parse_floor(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = normalize_text(str(value))
    if "parter" in text:
        return 0
    m = re.search(r"(\d+)\s*(?:pi(e|ę)tro|/)", text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def guess_market(text: str, seller_type: Optional[str] = None) -> str:
    t = normalize_text(text)
    if seller_type == SELLER_DEVELOPER:
        return MARKET_PRIMARY
    if re.search(r"rynek\s*pierwotny|nowa\s*inwestycj|od\s*dewelopera|stan\s*dewelopersk|nowe\s*mieszkani", t):
        return MARKET_PRIMARY
    return MARKET_SECONDARY


def guess_seller_type(text: str) -> str:
    t = normalize_text(text)
    if re.search(r"\bdeweloper|inwestycj\w*\s*dewelopersk", t):
        return SELLER_DEVELOPER
    if re.search(r"bezposrednio\s*od\s*wlascic|od\s*wlascic|osoba\s*prywatna|bez\s*posrednik", t):
        return SELLER_OWNER
    if re.search(r"biuro\s*nieruchom|posrednik|agencj|prowizj|nasza\s*oferta", t):
        return SELLER_AGENCY
    return SELLER_AGENCY  # bezpieczne domyślne: większość ogłoszeń to biura


def guess_condition(text: str) -> str:
    t = normalize_text(text)
    for cond, pattern in _CONDITION_PATTERNS:
        if re.search(pattern, t):
            return cond
    return CONDITION_UNKNOWN


def extract_features(text: str) -> List[str]:
    t = normalize_text(text)
    found = [name for name, pattern in FEATURE_PATTERNS.items() if re.search(pattern, t)]
    if "bez_prowizji" in found and "prowizja" in found:
        found.remove("prowizja")
    return found


def clean_text(value: Optional[str], limit: int = 4000) -> str:
    if not value:
        return ""
    txt = re.sub(r"<[^>]+>", " ", str(value))
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:limit]
