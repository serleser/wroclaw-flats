"""Centralna konfiguracja aplikacji (czytana z .env)."""
from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- DB ---
    database_url: str = "sqlite:///./data/flats.db"

    # --- Serwer ---
    host: str = "127.0.0.1"
    port: int = 8000

    # --- Scheduler ---
    scrape_interval_minutes: int = 15
    scheduler_enabled: bool = True
    run_on_startup: bool = False

    # --- Źródła ---
    enabled_sources: str = "olx,otodom,nieruchomosci_online,morizon"

    # --- Anty-blokowanie ---
    request_delay_min: float = 1.5
    request_delay_max: float = 4.0
    max_pages_per_source: int = 3
    http_timeout: float = 25.0
    http_proxy: str = ""

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Discord ---
    discord_webhook_url: str = ""

    # --- E-mail ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""

    # --- Bezpieczniki powiadomień ---
    max_notifications_per_run: int = 15
    silent_first_run: bool = True

    # --- Dedup / okazje ---
    dedup_threshold: float = 0.82
    deal_discount_pct: float = 12.0

    # --- Filtr miasta ---
    # Odrzuca oferty, które nie potwierdzają, że są z Wrocławia. Chroni przed
    # sytuacją, w której portal zwróci wyniki z całej Polski, a tańsze miasto
    # zostanie oznaczone jako "okazja" względem wrocławskiej mediany.
    strict_city_filter: bool = True

    @field_validator("database_url")
    @classmethod
    def _use_modern_postgres_driver(cls, value: str) -> str:
        """Dopisuje sterownik do adresu Postgresa, jeśli go brakuje.

        Neon (i większość hostingów) podaje adres w postaci `postgresql://...`.
        SQLAlchemy sięga wtedy po sterownik psycopg2, którego nie instalujemy —
        i start kończy się błędem „No module named 'psycopg2'”. Zamiast wymagać,
        żeby każdy pamiętał o ręcznej podmianie, robimy to tutaj raz.
        """
        text = value.strip().strip('"').strip("'")
        for prefix in ("postgresql://", "postgres://"):
            if text.startswith(prefix):
                return "postgresql+psycopg://" + text[len(prefix):]
        if text.startswith("postgresql+psycopg2://"):
            return "postgresql+psycopg://" + text[len("postgresql+psycopg2://"):]
        return text

    @property
    def sources(self) -> List[str]:
        return [s.strip() for s in self.enabled_sources.split(",") if s.strip()]

    @property
    def telegram_chat_ids(self) -> List[str]:
        return [c.strip() for c in self.telegram_chat_id.split(",") if c.strip()]

    @property
    def smtp_recipients(self) -> List[str]:
        return [c.strip() for c in self.smtp_to.split(",") if c.strip()]


settings = Settings()

# upewnij się, że katalog na bazę istnieje
(BASE_DIR / "data").mkdir(exist_ok=True)
