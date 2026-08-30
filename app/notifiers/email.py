"""E-mail (SMTP) — dobry na dzienny digest, słaby na alerty w czasie rzeczywistym.

Gmail: włącz 2FA i wygeneruj "hasło aplikacji", wpisz je w SMTP_PASSWORD.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import List, Optional

from ..config import settings
from ..models import Listing, SavedFilter
from .base import BaseNotifier, build_message, fmt_area, fmt_pln

log = logging.getLogger("notifier.email")


class EmailNotifier(BaseNotifier):
    name = "email"

    def is_configured(self) -> bool:
        return bool(settings.smtp_host and settings.smtp_from and settings.smtp_recipients)

    def _html(self, listing: Listing) -> str:
        img = (
            f'<img src="{listing.image_url}" alt="" '
            f'style="width:100%;max-width:520px;border-radius:12px;margin-bottom:12px">'
            if listing.image_url
            else ""
        )
        badge = (
            '<span style="background:#fee2e2;color:#b91c1c;padding:2px 8px;'
            'border-radius:999px;font-size:12px">OKAZJA</span>'
            if listing.is_deal
            else ""
        )
        return f"""
        <div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:560px">
          {badge}
          <h2 style="margin:8px 0">{listing.title[:160]}</h2>
          {img}
          <table style="border-collapse:collapse;font-size:15px">
            <tr><td style="padding:4px 12px 4px 0;color:#666">Cena</td>
                <td><b>{fmt_pln(listing.price)}</b></td></tr>
            <tr><td style="padding:4px 12px 4px 0;color:#666">Cena/m²</td>
                <td>{fmt_pln(listing.price_per_m2)}</td></tr>
            <tr><td style="padding:4px 12px 4px 0;color:#666">Metraż</td>
                <td>{fmt_area(listing.area)} · {listing.rooms or '—'} pok.</td></tr>
            <tr><td style="padding:4px 12px 4px 0;color:#666">Lokalizacja</td>
                <td>{listing.estate or listing.region or listing.location_raw}</td></tr>
            <tr><td style="padding:4px 12px 4px 0;color:#666">Źródło</td>
                <td>{listing.source}</td></tr>
          </table>
          <p><a href="{listing.url}"
                style="display:inline-block;margin-top:16px;background:#2563eb;color:#fff;
                       padding:10px 18px;border-radius:8px;text-decoration:none">
             Zobacz ofertę</a></p>
        </div>
        """

    def _send_sync(self, subject: str, text: str, html: Optional[str] = None) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = ", ".join(settings.smtp_recipients)
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=25) as server:
            server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)

    async def send(self, listing: Listing, saved_filter: Optional[SavedFilter] = None) -> None:
        prefix = "🔥 OKAZJA" if listing.is_deal else "Nowa oferta"
        location = listing.estate or listing.region or "Wrocław"
        subject = f"{prefix}: {fmt_pln(listing.price)} · {fmt_area(listing.area)} · {location}"
        await asyncio.to_thread(
            self._send_sync, subject, build_message(listing, saved_filter), self._html(listing)
        )

    async def send_text(self, text: str) -> None:
        await asyncio.to_thread(self._send_sync, "Monitor mieszkań — Wrocław", text)

    async def send_digest(self, listings: List[Listing]) -> None:
        """Zbiorcze podsumowanie — mniej spamu niż mail per oferta."""
        if not listings:
            return
        html = "".join(self._html(l) + "<hr style='border:none;border-top:1px solid #eee'>" for l in listings)
        text = "\n\n".join(build_message(l) for l in listings)
        await asyncio.to_thread(
            self._send_sync,
            f"Monitor mieszkań: {len(listings)} nowych ofert",
            text,
            html,
        )
