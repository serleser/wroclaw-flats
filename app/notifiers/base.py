"""Wspólny interfejs kanałów powiadomień + formatowanie treści oferty."""
from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Listing, SavedFilter

log = logging.getLogger("notifier")


def fmt_pln(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f} zł".replace(",", " ")


def fmt_area(value: Optional[float]) -> str:
    return f"{value:.1f} m²".replace(".", ",") if value else "—"


def esc_html(text: Optional[str]) -> str:
    """Escapowanie pod tryb HTML Telegrama.

    Bez tego tytuł zawierający znak < albo & wywala wysyłkę błędem
    „can't parse entities”, a powiadomienie przepada bez śladu.
    """
    if not text:
        return ""
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


MARKET_LABELS = {"pierwotny": "rynek pierwotny", "wtorny": "rynek wtórny"}
SELLER_LABELS = {
    "wlasciciel": "właściciel",
    "biuro": "biuro nieruchomości",
    "deweloper": "deweloper",
}
CONDITION_LABELS = {
    "do_zamieszkania": "do zamieszkania",
    "do_remontu": "do remontu",
    "stan_deweloperski": "stan deweloperski",
    "nieokreslony": "stan nieokreślony",
}


def build_message(listing: Listing, saved_filter: Optional[SavedFilter] = None) -> str:
    """Tekst powiadomienia (Markdown-friendly, wspólny dla wszystkich kanałów)."""
    lines: List[str] = []

    header = "🔥 OKAZJA" if listing.is_deal else "🏠 Nowa oferta"
    if saved_filter:
        header += f" · {saved_filter.name}"
    lines.append(header)
    lines.append("")
    lines.append(listing.title[:150])
    lines.append("")

    price_line = f"💰 {fmt_pln(listing.price)}"
    if listing.price_per_m2:
        price_line += f"  ({fmt_pln(listing.price_per_m2)}/m²)"
    lines.append(price_line)

    specs = [fmt_area(listing.area)]
    if listing.rooms:
        specs.append(f"{listing.rooms} pok.")
    if listing.floor is not None:
        specs.append("parter" if listing.floor == 0 else f"{listing.floor} piętro")
    lines.append("📐 " + " · ".join(specs))

    location = listing.estate or listing.region or listing.location_raw or "Wrocław"
    lines.append(f"📍 {location}")

    meta = []
    if listing.market:
        meta.append(MARKET_LABELS.get(listing.market, listing.market))
    if listing.seller_type:
        meta.append(SELLER_LABELS.get(listing.seller_type, listing.seller_type))
    if listing.condition and listing.condition != "nieokreslony":
        meta.append(CONDITION_LABELS.get(listing.condition, listing.condition))
    if meta:
        lines.append("🏷 " + " · ".join(meta))

    if listing.is_deal and listing.deal_score:
        lines.append(f"📉 {listing.deal_score:.0f}% poniżej mediany dla {location}")

    if listing.features:
        nice = {
            "balkon": "balkon", "taras": "taras", "ogrodek": "ogródek", "garaz": "garaż",
            "winda": "winda", "piwnica": "piwnica", "komorka": "komórka",
            "ksiega_wieczysta": "KW", "bez_prowizji": "bez prowizji",
        }
        tags = [nice[f] for f in listing.features if f in nice]
        if tags:
            lines.append("✅ " + ", ".join(tags))

    lines.append("")
    lines.append(f"🔗 {listing.url}")
    lines.append(f"_źródło: {listing.source}_")
    return "\n".join(lines)


def build_message_html(listing: Listing, saved_filter: Optional[SavedFilter] = None) -> str:
    """Ta sama treść co build_message, ale w HTML — bezpieczniejszym dla Telegrama.

    Markdown wywracał się na tytułach zawierających podkreślnik albo gwiazdkę,
    a takie na portalach się zdarzają.
    """
    lines: List[str] = []

    header = "🔥 <b>OKAZJA</b>" if listing.is_deal else "🏠 <b>Nowa oferta</b>"
    if saved_filter:
        header += f" · {esc_html(saved_filter.name)}"
    lines.append(header)
    lines.append("")
    lines.append(f"<b>{esc_html(listing.title[:150])}</b>")
    lines.append("")

    price_line = f"💰 <b>{fmt_pln(listing.price)}</b>"
    if listing.price_per_m2:
        price_line += f"  ({fmt_pln(listing.price_per_m2)}/m²)"
    lines.append(price_line)

    specs = [fmt_area(listing.area)]
    if listing.rooms:
        specs.append(f"{listing.rooms} pok.")
    if listing.floor is not None:
        specs.append("parter" if listing.floor == 0 else f"{listing.floor} piętro")
    lines.append("📐 " + " · ".join(specs))

    location = listing.estate or listing.region or listing.location_raw or "Wrocław"
    lines.append(f"📍 {esc_html(location)}")

    meta = []
    if listing.market:
        meta.append(MARKET_LABELS.get(listing.market, listing.market))
    if listing.seller_type:
        meta.append(SELLER_LABELS.get(listing.seller_type, listing.seller_type))
    if listing.condition and listing.condition != "nieokreslony":
        meta.append(CONDITION_LABELS.get(listing.condition, listing.condition))
    if meta:
        lines.append("🏷 " + esc_html(" · ".join(meta)))

    if listing.is_deal and listing.deal_score:
        lines.append(f"📉 {listing.deal_score:.0f}% poniżej mediany dla {esc_html(location)}")

    if listing.features:
        nice = {
            "balkon": "balkon", "taras": "taras", "ogrodek": "ogródek", "garaz": "garaż",
            "winda": "winda", "piwnica": "piwnica", "komorka": "komórka",
            "ksiega_wieczysta": "KW", "bez_prowizji": "bez prowizji",
        }
        tags = [nice[f] for f in listing.features if f in nice]
        if tags:
            lines.append("✅ " + ", ".join(tags))

    lines.append("")
    lines.append(f'<a href="{esc_html(listing.url)}">Otwórz ogłoszenie</a>  ·  <i>{esc_html(listing.source)}</i>')
    return "\n".join(lines)


class BaseNotifier:
    name = "base"

    def is_configured(self) -> bool:
        return False

    async def send(self, listing: Listing, saved_filter: Optional[SavedFilter] = None) -> None:
        raise NotImplementedError

    async def send_text(self, text: str) -> None:
        raise NotImplementedError
