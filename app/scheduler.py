"""Scheduler (APScheduler) — używany, gdy aplikacja chodzi jako proces ciągły.

W trybie "GitHub Actions" scheduler jest zbędny (cron robi to za nas) —
wtedy ustaw SCHEDULER_ENABLED=0.

Harmonogram jest lekko rozjeżdżany (jitter), żeby nie uderzać w portale
dokładnie co równe 15 minut — to jeden z sygnałów, po których wykrywa się boty.
"""
from __future__ import annotations

import logging
import random
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import settings
from .runner import run_cycle

log = logging.getLogger("scheduler")

_scheduler: Optional[AsyncIOScheduler] = None


async def _job() -> None:
    try:
        await run_cycle()
    except Exception as exc:  # noqa: BLE001
        log.exception("Cykl scrapowania zakończony błędem: %s", exc)


def start_scheduler() -> Optional[AsyncIOScheduler]:
    global _scheduler
    if not settings.scheduler_enabled:
        log.info("Scheduler wyłączony (SCHEDULER_ENABLED=0)")
        return None
    if _scheduler is not None:
        return _scheduler

    _scheduler = AsyncIOScheduler(timezone="Europe/Warsaw")
    _scheduler.add_job(
        _job,
        trigger=IntervalTrigger(
            minutes=settings.scrape_interval_minutes,
            jitter=int(settings.scrape_interval_minutes * 20),  # ±20% rozrzutu
        ),
        id="scrape_cycle",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    _scheduler.start()
    log.info("Scheduler wystartował — cykl co ~%s min", settings.scrape_interval_minutes)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
