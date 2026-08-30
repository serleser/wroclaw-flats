#!/usr/bin/env python3
"""Start aplikacji: API + dashboard (+ scheduler, jeśli włączony w .env).

    python run.py
"""
from __future__ import annotations

import sys

import uvicorn

from app.config import settings

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


if __name__ == "__main__":
    try:
        from app.db import describe_database
        db_info = describe_database()
    except RuntimeError as exc:
        # czytelny komunikat zamiast ściany tracebacku
        print("=" * 68)
        print(exc)
        print("=" * 68)
        sys.exit(1)

    print("\n  Monitor mieszkań — Wrocław")
    print(f"  Baza: {db_info}")
    print(f"  Adres: http://{settings.host}:{settings.port}")
    print("  Zatrzymanie: Ctrl+C\n")

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
