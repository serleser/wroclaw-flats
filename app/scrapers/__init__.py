"""Rejestr scraperów — moduły importują się same, żeby zadziałał @register."""
from __future__ import annotations

from .base import BaseScraper, available_scrapers, get_scraper, register  # noqa: F401
from .http import HttpClient  # noqa: F401

# kolejność importów = kolejność w rejestrze
from . import olx            # noqa: F401,E402
from . import otodom         # noqa: F401,E402
from . import nieruchomosci_online  # noqa: F401,E402
from . import morizon        # noqa: F401,E402
from . import small_portals  # noqa: F401,E402
from . import developers     # noqa: F401,E402
from . import facebook       # noqa: F401,E402

__all__ = [
    "BaseScraper",
    "HttpClient",
    "available_scrapers",
    "get_scraper",
    "register",
]
