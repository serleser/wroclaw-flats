"""Jeden przebieg scrapowania + wysyłka alertów — bez uruchamiania serwera.

To jest wejście dla GitHub Actions (cron) i dla systemowego crona:

    python -m app.tools.run_once
    python -m app.tools.run_once --sources olx,otodom
    python -m app.tools.run_once --no-alerts      # tylko zbieranie danych

Kod wychodzi z kodem 0 nawet przy błędzie jednego portalu — jeden padnięty
scraper nie ma czerwienić całego workflow. Kod 1 tylko wtedy, gdy padły
wszystkie źródła (to znaczy, że coś jest naprawdę nie tak).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from ..db import init_db
from ..runner import run_cycle

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)-20s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run_once")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Jednorazowy cykl scrapowania")
    parser.add_argument("--sources", help="lista źródeł po przecinku (domyślnie: z .env)")
    parser.add_argument("--no-alerts", action="store_true", help="nie wysyłaj powiadomień")
    args = parser.parse_args()

    init_db()
    sources = [s.strip() for s in args.sources.split(",")] if args.sources else None
    summary = await run_cycle(sources, send_alerts=not args.no_alerts)

    print(json.dumps(summary, indent=2, ensure_ascii=False))

    per_source = summary.get("sources", {})
    if per_source and all(s.get("status") == "error" for s in per_source.values()):
        log.error("Wszystkie źródła zwróciły błąd")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
