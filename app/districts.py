"""Rozpoznawanie dzielnic i osiedli Wrocławia z tekstu lokalizacji.

Wrocław formalnie ma 5 dzielnic historycznych, ale portale ogłoszeniowe operują
na ~48 osiedlach. Mapujemy osiedle -> rejon (grupa), żeby filtry i statystyki
cen za m2 miały sens.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional

# rejon -> lista osiedli/aliasów (małymi literami, bez polskich znaków po normalizacji)
REGIONS: Dict[str, List[str]] = {
    "Stare Miasto": [
        "stare miasto", "starymiasto", "rynek", "szczepin", "przedmiescie swidnickie",
        "przedmiescie olawskie", "nadodrze poludnie", "centrum",
    ],
    "Śródmieście": [
        "srodmiescie", "nadodrze", "olbin", "plac grunwaldzki", "zacisze",
        "zalesie", "szczytniki", "kleczkow", "biskupin", "sepolno", "dabie",
    ],
    "Krzyki": [
        "krzyki", "borek", "gaj", "tarnogaj", "huby", "poludnie", "przedmiescie olawskie",
        "brochow", "jagodno", "wojszyce", "partynice", "oltaszyn", "klecina",
        "ksieze male", "ksieze wielkie", "bienkowice", "opatowice",
    ],
    "Fabryczna": [
        "fabryczna", "gadow", "gadow maly", "popowice", "pilczyce", "kozanow",
        "muchobor maly", "muchobor wielki", "grabiszyn", "grabiszynek", "oporow",
        "krzyki-partynice", "zerniki", "nowy dwor", "jerzmanowo", "jarnoltow",
        "strachowice", "marszowice", "lesnica", "zlotniki", "stabłowice",
        "stablowice", "maslice", "pracze odrzanskie", "ratyn", "mokra",
    ],
    "Psie Pole": [
        "psie pole", "karlowice", "rozanka", "kowale", "sokolniki", "swojczyce",
        "strachocin", "wojnow", "zakrzow", "polanowice", "poswietne", "lipa piotrowska",
        "widawa", "osobowice", "rędzin", "redzin", "pawlowice", "klokoczyce",
        "kielczow", "brzezia lonka", "zgorzelisko",
    ],
}

# najbardziej rozpoznawalne osiedla — pokazujemy je osobno w UI
POPULAR_ESTATES: List[str] = [
    "Krzyki", "Gaj", "Jagodno", "Huby", "Tarnogaj", "Borek", "Ołtaszyn", "Klecina",
    "Grabiszyn", "Oporów", "Muchobór Wielki", "Nowy Dwór", "Popowice", "Kozanów",
    "Pilczyce", "Gądów", "Maślice", "Stabłowice", "Leśnica", "Żerniki",
    "Nadodrze", "Ołbin", "Plac Grunwaldzki", "Biskupin", "Sępolno", "Dąbie",
    "Karłowice", "Różanka", "Sołtysowice", "Psie Pole", "Zakrzów", "Swojczyce",
    "Lipa Piotrowska", "Widawa", "Osobowice", "Brochów", "Księże Małe",
    "Stare Miasto", "Szczepin", "Śródmieście",
]


def strip_accents(text: str) -> str:
    """usuwa polskie znaki diakrytyczne -> porównywanie odporne na zapis"""
    nfkd = unicodedata.normalize("NFKD", text)
    out = "".join(c for c in nfkd if not unicodedata.combining(c))
    return out.replace("ł", "l").replace("Ł", "L")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(text or "").lower()).strip()


_VOWELS = "aeiouy"
_PATTERN_CACHE: Dict[str, "re.Pattern[str]"] = {}


def _inflected_pattern(name: str) -> "re.Pattern[str]":
    """Wzorzec dopasowujący nazwę mimo polskiej odmiany.

    „Jagodno” musi trafić w „na Jagodnie”, „Muchobór Wielki” w „Muchoborze
    Wielkim”. Ucinamy jedną końcową samogłoskę i dopuszczamy krótką końcówkę —
    to wystarcza dla nazw osiedli, a nie łapie przypadkowych słów.
    """
    cached = _PATTERN_CACHE.get(name)
    if cached is not None:
        return cached

    parts = []
    for word in normalize_text(name).split():
        stem = word
        if len(stem) >= 5 and stem[-1] in _VOWELS:
            stem = stem[:-1]
        suffix = 3 if len(stem) >= 5 else 2
        parts.append(rf"{re.escape(stem)}\w{{0,{suffix}}}")

    pattern = re.compile(r"\b" + r"[\s-]+".join(parts) + r"\b")
    _PATTERN_CACHE[name] = pattern
    return pattern


def detect_estate(location_text: str) -> Optional[str]:
    """Zwraca nazwę osiedla (ładnie sformatowaną) wykrytą w tekście lokalizacji."""
    norm = normalize_text(location_text)
    if not norm:
        return None
    best: Optional[str] = None
    for estate in POPULAR_ESTATES:
        if _inflected_pattern(estate).search(norm):
            # preferuj dłuższe dopasowanie ("Muchobór Wielki" > "Muchobór")
            if best is None or len(estate) > len(best):
                best = estate
    return best


def detect_region(location_text: str) -> Optional[str]:
    """Zwraca rejon (jedna z 5 grup) na podstawie tekstu lokalizacji."""
    norm = normalize_text(location_text)
    if not norm:
        return None
    best_region, best_len = None, 0
    for region, aliases in REGIONS.items():
        for alias in aliases:
            if _inflected_pattern(alias).search(norm) and len(alias) > best_len:
                best_region, best_len = region, len(alias)
    return best_region


def all_regions() -> List[str]:
    return list(REGIONS.keys())


# ---------------------------------------------------------------------------
#  Kontrola miasta — zabezpieczenie przed ofertami spoza Wrocławia
# ---------------------------------------------------------------------------
#
# Powód: zapytanie do portalu może zostać źle zrozumiane (np. po zmianie
# adresu wyszukiwarki) i zwrócić oferty z całej Polski. Mieszkanie z Bydgoszczy
# w cenie bydgoskiej, porównane do wrocławskiej mediany, wygląda jak okazja
# życia — i takie ogłoszenie potrafi wywołać fałszywy alert.
#
# Dlatego każda oferta musi POTWIERDZIĆ, że jest z Wrocławia. Brak potwierdzenia
# oznacza odrzucenie — lepiej stracić kilka ogłoszeń niż zaśmiecić bazę
# i zafałszować mediany dzielnic.

# miasta, które jednoznacznie dyskwalifikują ofertę (do czytelnego komunikatu)
OTHER_CITIES: List[str] = [
    "warszawa", "krakow", "lodz", "poznan", "gdansk", "gdynia", "szczecin",
    "bydgoszcz", "lublin", "bialystok", "katowice", "czestochowa", "radom",
    "sosnowiec", "torun", "kielce", "rzeszow", "gliwice", "zabrze", "olsztyn",
    "bielsko-biala", "bytom", "opole", "zielona gora", "plock", "elblag",
    "walbrzych", "legnica", "jelenia gora", "swidnica", "olesnica", "olawa",
    "trzebnica", "brzeg", "lubin", "glogow", "boleslawiec", "dzierzoniow",
    "klodzko", "strzelin", "milicz", "wolow", "sroda slaska", "nysa", "raciborz",
    "tychy", "rybnik", "gorzow", "koszalin", "slupsk", "piła", "pila", "kalisz",
    "konin", "leszno", "ostrow", "wloclawek", "grudziadz", "tarnow", "nowy sacz",
]

# miejscowości ościenne, które traktujemy jak aglomerację wrocławską
NEARBY_TOWNS: List[str] = [
    "siechnice", "smolec", "kobierzyce", "dlugoleka", "mirkow", "kielczow",
    "wysoka", "radwanice", "swieta katarzyna", "nadolice", "domaslaw",
    "tyniec maly", "magnice", "bielany", "sadkow", "krzeptow", "gniechowice",
]


def location_verdict(text: str) -> tuple[bool, str]:
    """Czy oferta jest z Wrocławia? Zwraca (czy_przyjąć, powód_odrzucenia)."""
    norm = normalize_text(text)
    if not norm:
        return False, "brak jakiejkolwiek informacji o lokalizacji"

    # 1. wprost napisane "Wrocław" w dowolnej odmianie (też: wrocławska, Wrocławiu)
    if re.search(r"wroclaw", norm):
        return True, ""

    # 2. rozpoznane osiedle wrocławskie i żadne inne miasto obok
    foreign = next((c for c in OTHER_CITIES if re.search(rf"\b{re.escape(c)}\b", norm)), None)
    if foreign:
        return False, f"oferta z innego miasta: {foreign}"

    if detect_estate(text) or detect_region(text):
        return True, ""

    # 3. miejscowość ościenna
    if any(re.search(rf"\b{re.escape(t)}\b", norm) for t in NEARBY_TOWNS):
        return True, ""

    return False, "nie potwierdzono, że oferta jest z Wrocławia"
