"""Schematy Pydantic dla API."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Criteria(BaseModel):
    """Kryteria zakupowe — jeden model dla wyszukiwarki i dla alertów."""

    price_min: Optional[float] = None
    price_max: Optional[float] = None
    ppm_min: Optional[float] = Field(None, description="cena za m² od")
    ppm_max: Optional[float] = Field(None, description="cena za m² do")
    area_min: Optional[float] = None
    area_max: Optional[float] = None
    floor_min: Optional[int] = None
    floor_max: Optional[int] = None
    rooms: List[int] = Field(default_factory=list)
    market: Optional[str] = Field(None, description="pierwotny | wtorny | oba")
    seller_types: List[str] = Field(default_factory=list)
    regions: List[str] = Field(default_factory=list)
    estates: List[str] = Field(default_factory=list)
    conditions: List[str] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=list)
    keywords_include: List[str] = Field(default_factory=list)
    keywords_any: List[str] = Field(default_factory=list)
    keywords_exclude: List[str] = Field(default_factory=list)
    only_deals: bool = False
    hide_duplicates: bool = True
    only_active: bool = True
    sort: str = "newest"


class SearchRequest(BaseModel):
    criteria: Criteria = Field(default_factory=Criteria)
    limit: int = 60
    offset: int = 0


class SearchResponse(BaseModel):
    total: int
    items: List[Dict[str, Any]]
    limit: int
    offset: int


class FilterCreate(BaseModel):
    name: str
    criteria: Criteria = Field(default_factory=Criteria)
    notify_telegram: bool = True
    notify_discord: bool = False
    notify_email: bool = False
    is_active: bool = True


class FilterUpdate(BaseModel):
    name: Optional[str] = None
    criteria: Optional[Criteria] = None
    notify_telegram: Optional[bool] = None
    notify_discord: Optional[bool] = None
    notify_email: Optional[bool] = None
    is_active: Optional[bool] = None


class FavoriteCreate(BaseModel):
    listing_id: int
    note: str = ""


class ManualListing(BaseModel):
    """Import ręczny — wklejony post z Facebooka / forum / SMS-a."""

    text: str
    url: str = ""
    source: str = "manual"


class ScrapeRequest(BaseModel):
    sources: List[str] = Field(default_factory=list)
    send_alerts: bool = True
