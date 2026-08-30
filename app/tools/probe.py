"""Diagnostyka scraperów — sprawdź jedno źródło bez zapisu do bazy.

    python -m app.tools.probe                 # lista dostępnych scraperów
    python -m app.tools.probe olx             # pokaż 5 pierwszych ofert
    python -m app.tools.probe otodom --debug  # + surowa odpowiedź do data/debug/

Używaj tego, gdy portal zmieni layout i scraper zacznie zwracać 0 ofert:
zapisany plik HTML/JSON pokaże, czy to redesign, czy blokada (captcha).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ..config import BASE_DIR
from ..scrapers import available_scrapers, get_scraper
from ..scrapers.http import HAS_CURL_CFFI, HttpClient, backend_name

DEBUG_DIR = BASE_DIR / "data" / "debug"


def _print_registry() -> None:
    print("\nDostępne scrapery:\n")
    for name, cls in available_scrapers().items():
        flags = []
        if cls.requires_js:
            flags.append("wymaga Playwright")
        if not cls.enabled_by_default:
            flags.append("wyłączony domyślnie")
        suffix = f"  ({', '.join(flags)})" if flags else ""
        print(f"  {name:<24} {cls.label}{suffix}")
    print()


async def _dump_raw(name: str) -> None:
    scraper = get_scraper(name)
    if scraper is None:
        return
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    url = getattr(scraper, "list_url", None) or getattr(scraper, "base_url", None)
    if not url:
        print("Ten scraper nie ma prostego URL-a listy — pomijam zrzut.")
        return

    async with HttpClient() as client:
        params = getattr(scraper, "extra_params", {}) or {}
        resp = await client.get(url, params=params)
        if resp is None:
            print(f"❌ Nie udało się pobrać {url} (blokada albo timeout)")
            return
        path = DEBUG_DIR / f"{name}.html"
        path.write_text(resp.text, encoding="utf-8")
        print(f"💾 Surowa odpowiedź ({len(resp.text)} znaków) -> {path}")
        low = resp.text.lower()
        for marker in ("captcha", "cloudflare", "access denied", "jestes robotem", "px-captcha"):
            if marker in low:
                print(f"⚠️  W treści znaleziono '{marker}' — to wygląda na blokadę bota.")
                break


async def main() -> int:
    parser = argparse.ArgumentParser(description="Test pojedynczego scrapera")
    parser.add_argument("source", nargs="?", help="nazwa scrapera")
    parser.add_argument("--debug", action="store_true", help="zapisz surową odpowiedź")
    parser.add_argument("--limit", type=int, default=5, help="ile ofert wypisać")
    args = parser.parse_args()

    if not args.source:
        _print_registry()
        return 0

    scraper = get_scraper(args.source)
    if scraper is None:
        print(f"❌ Nie znam scrapera '{args.source}'")
        _print_registry()
        return 1

    print(f"▶ Testuję: {scraper.label} ({scraper.name})")
    print(f"  Sposób łączenia: {backend_name()}\n")
    items = await scraper.run()
    print(f"\n✔ Pobrano {len(items)} ofert\n")

    for item in items[: args.limit]:
        d = item.to_dict()
        print(json.dumps(
            {k: v for k, v in d.items() if k not in ("description", "extra")},
            indent=2, ensure_ascii=False,
        ))
        print("-" * 60)

    filled = sum(1 for i in items if i.price and i.area)
    if items:
        pct = filled / len(items) * 100
        print(f"Kompletność (cena + metraż): {filled}/{len(items)} = {pct:.0f}%")
        if pct < 80:
            print("⚠️  Poniżej 80% — portal prawdopodobnie zmienił układ strony.")
    else:
        print("=" * 62)
        print("Zero ofert. Co dalej:")
        if not HAS_CURL_CFFI:
            print("  • Zainstaluj podszywanie się pod przeglądarkę — to naprawia")
            print("    większość blokad (403):")
            print("        pip install curl_cffi")
            print("    a potem uruchom ten test jeszcze raz.")
        else:
            print("  • curl_cffi jest zainstalowane, więc blokada jest twardsza.")
            print("    Zwiększ REQUEST_DELAY_MIN/MAX w .env albo odpuść to źródło.")
        print("  • Uruchom z --debug, żeby zapisać surową odpowiedź strony.")
        print("  • Do czasu naprawy usuń to źródło z ENABLED_SOURCES w .env —")
        print("    pozostałe portale będą działać normalnie.")
        print("=" * 62)

    if args.debug:
        await _dump_raw(args.source)

    return 0 if items else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
