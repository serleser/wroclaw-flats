#!/usr/bin/env python3
"""Start aplikacji: API + dashboard (+ scheduler, jeśli włączony w .env).

    python run.py
"""
from __future__ import annotations

import uvicorn

from app.config import settings

if __name__ == "__main__":
    print(f"\n  🏠  Monitor mieszkań — Wrocław")
    print(f"  →  http://{settings.host}:{settings.port}\n")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
