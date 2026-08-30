"""Modele SQLAlchemy 2.0."""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def as_utc(value: Optional[dt.datetime]) -> Optional[dt.datetime]:
    """Sprowadza datę do postaci ze strefą UTC.

    SQLite nie przechowuje strefy czasowej, więc data odczytana z bazy wraca
    „naga”, a data właśnie utworzonego obiektu ma strefę. Porównanie takich
    dwóch dat w Pythonie kończy się TypeError — stąd ta funkcja wszędzie tam,
    gdzie daty są sortowane albo odejmowane.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def iso(value: Optional[dt.datetime]) -> Optional[str]:
    """Data w ISO 8601 zawsze ze strefą — żeby przeglądarka nie przesuwała godzin."""
    aware = as_utc(value)
    return aware.isoformat() if aware else None


class Base(DeclarativeBase):
    pass


class Listing(Base):
    """Pojedyncze ogłoszenie z konkretnego portalu."""

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_listing_source"),
        Index("ix_listing_price_m2", "price_per_m2"),
        Index("ix_listing_region", "region"),
        Index("ix_listing_first_seen", "first_seen_at"),
        Index("ix_listing_group", "dup_group_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # --- pochodzenie ---
    source: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text)

    # --- treść ---
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- parametry zakupowe ---
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_per_m2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    area: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rooms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    floor: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    year_built: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # --- lokalizacja ---
    location_raw: Mapped[str] = mapped_column(Text, default="")
    estate: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    region: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    # --- klasyfikacja ---
    market: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    seller_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    condition: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    features: Mapped[List[str]] = mapped_column(JSON, default=list)

    # --- cykl życia ---
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    posted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- historia ceny i deduplikacja ---
    price_history: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True, default="")
    dup_group_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- flagi wyliczane ---
    is_deal: Mapped[bool] = mapped_column(Boolean, default=False)
    deal_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # % poniżej mediany

    raw: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    favorite: Mapped[Optional["Favorite"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan", uselist=False
    )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "source_id": self.source_id,
            "url": self.url,
            "title": self.title,
            "description": self.description[:600],
            "image_url": self.image_url,
            "price": self.price,
            "price_per_m2": self.price_per_m2,
            "area": self.area,
            "rooms": self.rooms,
            "floor": self.floor,
            "location_raw": self.location_raw,
            "estate": self.estate,
            "region": self.region,
            "market": self.market,
            "seller_type": self.seller_type,
            "condition": self.condition,
            "features": self.features or [],
            "first_seen_at": iso(self.first_seen_at),
            "last_seen_at": iso(self.last_seen_at),
            "is_active": self.is_active,
            "is_deal": self.is_deal,
            "deal_score": self.deal_score,
            "is_duplicate": self.is_duplicate,
            "dup_group_id": self.dup_group_id,
            "price_history": self.price_history or [],
            "is_favorite": self.favorite is not None,
        }


class SavedFilter(Base):
    """Zapisany filtr zakupowy = definicja alertu."""

    __tablename__ = "saved_filters"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    criteria: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    notify_telegram: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_discord: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_email: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "criteria": self.criteria or {},
            "notify_telegram": self.notify_telegram,
            "notify_discord": self.notify_discord,
            "notify_email": self.notify_email,
            "is_active": self.is_active,
            "created_at": iso(self.created_at),
        }


class Notification(Base):
    """Historia wysłanych powiadomień (also: anty-duplikat wysyłki)."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("listing_id", "filter_id", name="uq_notification_once"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    filter_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("saved_filters.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(20), default="telegram")
    status: Mapped[str] = mapped_column(String(20), default="sent")  # sent / failed
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listing: Mapped["Listing"] = relationship()
    saved_filter: Mapped[Optional["SavedFilter"]] = relationship()


class Favorite(Base):
    __tablename__ = "favorites"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), unique=True
    )
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listing: Mapped["Listing"] = relationship(back_populates="favorite")


class DistrictStat(Base):
    """Mediana ceny za m2 per osiedle/rejon — baza do oznaczania okazji."""

    __tablename__ = "district_stats"
    __table_args__ = (UniqueConstraint("scope", "name", "market", name="uq_stat"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(10))       # 'estate' | 'region' | 'city'
    name: Mapped[str] = mapped_column(String(80))
    market: Mapped[str] = mapped_column(String(20), default="all")
    median_price_per_m2: Mapped[float] = mapped_column(Float)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScrapeRun(Base):
    """Log przebiegów scrapowania — do diagnostyki 'czy portal się nie zepsuł'."""

    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    found: Mapped[int] = mapped_column(Integer, default=0)
    new: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "found": self.found,
            "new": self.new,
            "updated": self.updated,
            "duplicates": self.duplicates,
            "status": self.status,
            "error": self.error,
        }
