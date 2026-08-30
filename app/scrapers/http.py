"""Klient HTTP z ochroną przed blokowaniem.

Mechanizmy:
  * podszywanie się pod przeglądarkę na poziomie TLS (curl_cffi) — jeśli jest
    zainstalowane. To dziś najważniejszy element: portale rozpoznają boty nie po
    nagłówkach, tylko po "odcisku palca" połączenia szyfrowanego, którego zwykłe
    biblioteki HTTP nie potrafią podrobić.
  * rotacja User-Agent (pula realnych przeglądarek desktop + mobile)
  * pełny, spójny zestaw nagłówków (Accept-Language: pl-PL, Sec-Ch-Ua itd.)
  * per-host throttling: losowa przerwa REQUEST_DELAY_MIN..MAX między requestami
    do TEGO SAMEGO hosta (różne hosty lecą równolegle)
  * retry z wykładniczym backoffem na 429 / 5xx / timeout
  * SZYBKIE poddanie się przy 403 — to twarda blokada, a nie przeciążenie;
    ponawianie jej przez minutę niczego nie daje poza traceniem czasu
  * opcjonalny proxy z .env

Instalacja podszywania się (zalecana, jeśli portal zwraca 403):

    pip install curl_cffi
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode, urlsplit

import httpx

from ..config import settings

log = logging.getLogger("scraper.http")

# --- opcjonalny backend podszywający się pod przeglądarkę -------------------
try:
    from curl_cffi.requests import AsyncSession as _CurlSession  # type: ignore

    HAS_CURL_CFFI = True
except ImportError:  # pragma: no cover
    _CurlSession = None  # type: ignore
    HAS_CURL_CFFI = False

# profile przeglądarek rozpoznawane przez curl_cffi
IMPERSONATE_PROFILES = ["chrome124", "chrome123", "chrome120", "edge101", "safari17_0"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

BLOCK_HINT = (
    "Portal odrzucił połączenie (403). To zabezpieczenie przed botami, nie przeciążenie serwera.\n"
    "         Co pomaga, po kolei:\n"
    "         1) pip install curl_cffi   — podszywa się pod przeglądarkę, wystarcza w większości przypadków\n"
    "         2) zwiększ REQUEST_DELAY_MIN / REQUEST_DELAY_MAX w pliku .env\n"
    "         3) jeśli nic nie działa, usuń to źródło z ENABLED_SOURCES i korzystaj z pozostałych"
)


class HostThrottle:
    """Wymusza minimalny odstęp między requestami do tego samego hosta."""

    def __init__(self) -> None:
        self._last: Dict[str, float] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def _lock(self, host: str) -> asyncio.Lock:
        if host not in self._locks:
            self._locks[host] = asyncio.Lock()
        return self._locks[host]

    async def wait(self, host: str) -> None:
        async with self._lock(host):
            delay = random.uniform(settings.request_delay_min, settings.request_delay_max)
            elapsed = time.monotonic() - self._last.get(host, 0.0)
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self._last[host] = time.monotonic()


throttle = HostThrottle()


class Response:
    """Wspólny kształt odpowiedzi niezależnie od użytego backendu."""

    __slots__ = ("status_code", "text", "headers", "url")

    def __init__(self, status_code: int, text: str, headers: Dict[str, str], url: str):
        self.status_code = status_code
        self.text = text
        self.headers = headers
        self.url = url

    def json(self) -> Any:
        import json

        return json.loads(self.text)


class HttpClient:
    """Klient z throttlingiem, retry i (opcjonalnie) podszywaniem się pod przeglądarkę."""

    def __init__(self, timeout: Optional[float] = None, force_httpx: bool = False) -> None:
        self._timeout = timeout or settings.http_timeout
        self._use_curl = HAS_CURL_CFFI and not force_httpx
        self._httpx: Optional[httpx.AsyncClient] = None
        self._curl: Any = None
        self._profile = random.choice(IMPERSONATE_PROFILES)

    async def __aenter__(self) -> "HttpClient":
        if self._use_curl:
            kwargs: Dict[str, Any] = {"impersonate": self._profile, "timeout": self._timeout}
            if settings.http_proxy:
                kwargs["proxies"] = {"http": settings.http_proxy, "https": settings.http_proxy}
            self._curl = _CurlSession(**kwargs)
            log.debug("Backend HTTP: curl_cffi (profil %s)", self._profile)
        else:
            kwargs = {
                "timeout": httpx.Timeout(self._timeout),
                "follow_redirects": True,
                "http2": True,
                "limits": httpx.Limits(max_connections=8, max_keepalive_connections=4),
            }
            if settings.http_proxy:
                kwargs["proxy"] = settings.http_proxy
            self._httpx = httpx.AsyncClient(**kwargs)
            log.debug("Backend HTTP: httpx (curl_cffi niezainstalowane)")
        return self

    async def __aexit__(self, *exc) -> None:
        if self._httpx is not None:
            await self._httpx.aclose()
        if self._curl is not None:
            try:
                await self._curl.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------

    def _headers(self, extra: Optional[Dict[str, str]] = None, referer: Optional[str] = None):
        headers = dict(BASE_HEADERS)
        # przy curl_cffi profil przeglądarki dostarcza własny, spójny User-Agent
        # i nagłówki Sec-Ch-Ua — nadpisywanie ich psułoby maskowanie
        if not self._use_curl:
            headers["User-Agent"] = random.choice(USER_AGENTS)
            headers["Accept-Encoding"] = "gzip, deflate, br"
            headers["Connection"] = "keep-alive"
        if referer:
            headers["Referer"] = referer
            headers["Sec-Fetch-Site"] = "same-origin"
        if extra:
            headers.update(extra)
        return headers

    async def _fetch(
        self, url: str, params: Optional[Dict[str, Any]], headers: Dict[str, str]
    ) -> Tuple[Optional[Response], Optional[Exception]]:
        try:
            if self._use_curl:
                full = f"{url}?{urlencode(params)}" if params else url
                raw = await self._curl.get(full, headers=headers, allow_redirects=True)
                text = raw.text if isinstance(raw.text, str) else raw.text.decode("utf-8", "replace")
                return Response(raw.status_code, text, dict(raw.headers), str(raw.url)), None
            raw = await self._httpx.get(url, params=params, headers=headers)
            return Response(raw.status_code, raw.text, dict(raw.headers), str(raw.url)), None
        except Exception as exc:  # noqa: BLE001
            return None, exc

    async def get(
        self,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        referer: Optional[str] = None,
        max_retries: int = 3,
    ) -> Optional[Response]:
        host = urlsplit(url).hostname or url

        for attempt in range(1, max_retries + 1):
            await throttle.wait(host)
            resp, error = await self._fetch(url, params, self._headers(headers, referer))

            if error is not None:
                log.warning("[%s] próba %s/%s — błąd sieci: %s", host, attempt, max_retries, error)
                await asyncio.sleep(2 ** attempt + random.random())
                continue

            if resp.status_code == 200:
                return resp

            # 403 = twarda blokada. Ponawianie jej nie ma sensu — dajemy jedną
            # dodatkową próbę (bywa, że pierwszy strzał leci bez ciasteczek),
            # a potem od razu mówimy, co zrobić.
            if resp.status_code == 403:
                if attempt == 1:
                    log.info("[%s] 403 — ponawiam raz z nowym połączeniem", host)
                    await asyncio.sleep(random.uniform(2, 4))
                    continue
                log.error("[%s] %s", host, BLOCK_HINT)
                if not HAS_CURL_CFFI:
                    log.error(
                        "[%s] Podpowiedź: curl_cffi NIE jest zainstalowane — "
                        "to najczęstsze lekarstwo na ten błąd.", host,
                    )
                return None

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else 15 * attempt
                log.warning("[%s] 429 — czekam %.0fs (limit zapytań)", host, wait)
                await asyncio.sleep(wait + random.uniform(0, 5))
                continue

            if 500 <= resp.status_code < 600:
                log.warning("[%s] %s — serwer portalu ma problem, ponawiam", host, resp.status_code)
                await asyncio.sleep(2 ** attempt)
                continue

            log.info("[%s] %s dla %s — pomijam", host, resp.status_code, url)
            return None

        log.error("[%s] nie udało się pobrać %s po %s próbach", host, url, max_retries)
        return None

    async def get_text(self, url: str, **kwargs) -> Optional[str]:
        resp = await self.get(url, **kwargs)
        return resp.text if resp is not None else None

    async def get_json(self, url: str, **kwargs) -> Optional[Any]:
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("Accept", "application/json, text/plain, */*")
        headers.setdefault("Sec-Fetch-Dest", "empty")
        headers.setdefault("Sec-Fetch-Mode", "cors")
        resp = await self.get(url, headers=headers, **kwargs)
        if resp is None:
            return None
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("Nie udało się sparsować JSON z %s: %s", url, exc)
            return None


def backend_name() -> str:
    return "curl_cffi (podszywanie się pod przeglądarkę)" if HAS_CURL_CFFI else "httpx (podstawowy)"
