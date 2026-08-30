# 🏠 Monitor mieszkań — Wrocław

Aplikacja do automatycznego szukania mieszkania na sprzedaż we Wrocławiu: zbiera oferty
z kilku portali naraz, skleja duplikaty tej samej nieruchomości, liczy medianę ceny za m²
dla każdego osiedla i wysyła na Telegram alert, gdy pojawi się coś, co pasuje do Waszych kryteriów.

**Docelowy tryb pracy:** scraper chodzi w chmurze na GitHub Actions (za darmo, bez włączonego
komputera), dane trzyma darmowy Postgres na Neonie, alerty lecą na telefon. Dashboard
odpalasz u siebie wtedy, kiedy chcesz przejrzeć oferty — widzi te same dane z chmury.

---

## Spis treści

1. [Architektura](#1-architektura)
2. [Szybki start lokalnie (5 minut)](#2-szybki-start-lokalnie-5-minut)
3. [Telegram — konfiguracja bota](#3-telegram--konfiguracja-bota)
4. [Wdrożenie za darmo: GitHub Actions + Neon](#4-wdrożenie-za-darmo-github-actions--neon)
5. [Jak działa deduplikacja](#5-jak-działa-deduplikacja)
6. [Jak wykrywane są okazje](#6-jak-wykrywane-są-okazje)
7. [Dodawanie nowych źródeł](#7-dodawanie-nowych-źródeł)
8. [Ochrona przed blokowaniem](#8-ochrona-przed-blokowaniem)
9. [Diagnostyka: gdy portal przestanie działać](#9-diagnostyka-gdy-portal-przestanie-działać)
10. [API](#10-api)
11. [Kwestie prawne i ograniczenia](#11-kwestie-prawne-i-ograniczenia)

---

## 1. Architektura

```
                    ┌─────────────────────────────────────────────┐
   GitHub Actions   │  cron co 30 min → python -m app.tools.run_once│
   (chmura, 0 zł)   └───────────────────────┬─────────────────────┘
                                            │
                    ┌───────────────────────▼─────────────────────┐
                    │              PIPELINE (app/runner.py)        │
                    │                                              │
   ┌────────────────┼──────────────┐                               │
   │  1. SCRAPERY   │              │  2. UPSERT + historia ceny    │
   │  (pluginy)     │              │  3. DEDUPLIKACJA              │
   │                │              │  4. MEDIANY / OKAZJE          │
   │  olx           │              │  5. DOPASOWANIE FILTRÓW       │
   │  otodom        │              │  6. ALERTY                    │
   │  nieruchomosci │              └───────────────┬───────────────┘
   │  morizon       │                              │
   │  gratka        │              ┌───────────────▼───────────────┐
   │  sprzedawacz   │              │   PostgreSQL (Neon) / SQLite  │
   │  adradar       │              │   listings · saved_filters    │
   │  developers    │              │   notifications · favorites   │
   │  facebook*     │              │   district_stats · runs       │
   └────────────────┘              └───────┬───────────────┬───────┘
                                           │               │
                          ┌────────────────▼───┐   ┌───────▼──────────┐
                          │  POWIADOMIENIA     │   │  DASHBOARD       │
                          │  Telegram (główny) │   │  FastAPI + HTML  │
                          │  Discord, e-mail   │   │  odpalany lokalnie│
                          └────────────────────┘   └──────────────────┘
```

### Struktura plików

```
wroclaw-flats/
├── run.py                      # start dashboardu (API + frontend)
├── requirements.txt
├── .env.example                # skopiuj do .env i uzupełnij
├── config/developers.json      # konfiguracja stron deweloperów
├── .github/workflows/
│   ├── scrape.yml              # cron w chmurze (co 30 min)
│   └── keepalive.yml           # żeby GitHub nie wyłączył crona po 60 dniach
├── app/
│   ├── config.py               # ustawienia z .env
│   ├── models.py               # schemat bazy (SQLAlchemy)
│   ├── db.py
│   ├── normalize.py            # parsowanie cen, metrażu, pokoi, cech
│   ├── districts.py            # rozpoznawanie osiedli i rejonów Wrocławia
│   ├── dedup.py                # sklejanie tej samej nieruchomości z kilku portali
│   ├── stats.py                # mediany cen za m², wykrywanie okazji
│   ├── matching.py             # silnik filtrów (wyszukiwarka + alerty)
│   ├── runner.py               # pipeline całego cyklu
│   ├── scheduler.py            # APScheduler (tryb ciągły, opcjonalny)
│   ├── main.py                 # FastAPI: API + serwowanie dashboardu
│   ├── scrapers/
│   │   ├── base.py             # klasa bazowa + rejestr pluginów
│   │   ├── http.py             # klient HTTP: rotacja UA, throttling, retry
│   │   ├── generic_html.py     # scraper sterowany selektorami CSS
│   │   ├── olx.py              # OLX (publiczne API JSON)
│   │   ├── otodom.py           # Otodom (__NEXT_DATA__)
│   │   ├── nieruchomosci_online.py
│   │   ├── morizon.py          # Morizon + Gratka
│   │   ├── small_portals.py    # Sprzedawacz, Adradar + szablon
│   │   ├── developers.py       # rynek pierwotny (konfigurowalny)
│   │   └── facebook.py         # grupy FB — import ręczny + tryb półautomatyczny
│   ├── notifiers/
│   │   ├── telegram.py · discord.py · email.py
│   └── tools/
│       ├── run_once.py         # jeden cykl (dla crona/Actions)
│       ├── probe.py            # test pojedynczego scrapera
│       └── seed_demo.py        # dane demo do obejrzenia UI
└── static/index.html           # cały dashboard (bez frameworka, działa offline)
```

### Stack

| Warstwa | Wybór | Dlaczego |
|---|---|---|
| Backend | Python 3.11+ / FastAPI | asyncio pasuje do równoległego scrapowania; automatyczna dokumentacja API |
| Baza | SQLite lokalnie / PostgreSQL (Neon) w chmurze | jeden `DATABASE_URL` przełącza tryb, zero zmian w kodzie |
| HTTP | httpx (HTTP/2, async) | wolniejszy w setupie niż `requests`, ale pozwala trzymać sesję i throttling per host |
| Parser | selectolax | ~10× szybszy od BeautifulSoup, ważne przy 5 portalach × 3 strony co 30 min |
| Frontend | HTML + własny CSS + vanilla JS | żadnego builda, żadnego CDN — dashboard działa też bez internetu |
| Scheduler | GitHub Actions (chmura) lub APScheduler (tryb ciągły) | |

---

## 2. Szybki start lokalnie (5 minut)

```bash
# 1. zależności
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. konfiguracja
cp .env.example .env                 # Windows: copy .env.example .env

# 3. dane demo (żeby zobaczyć, jak to wygląda, zanim ruszysz scrapery)
python -m app.tools.seed_demo

# 4. start
python run.py
```

Otwórz **http://127.0.0.1:8000**

Gdy chcesz przejść na prawdziwe dane:

```bash
python -m app.tools.seed_demo --clear --count 0   # wyczyść demo
python -m app.tools.probe olx                     # sprawdź, czy źródło odpowiada
python -m app.tools.run_once --no-alerts          # pierwszy prawdziwy przebieg
python run.py
```

> Pierwszy przebieg celowo **nie wysyła alertów** (`SILENT_FIRST_RUN=1` w `.env`) —
> inaczej dostalibyście naraz kilkaset powiadomień o ofertach, które wiszą od tygodni.

---

## 3. Telegram — konfiguracja bota

1. W Telegramie napisz do **@BotFather** → `/newbot` → nazwa → dostajesz **token**.
2. Napisz cokolwiek do swojego nowego bota (bot nie może zagadać pierwszy).
3. Otwórz w przeglądarce:
   `https://api.telegram.org/bot<TWÓJ_TOKEN>/getUpdates`
   i odczytaj `"chat":{"id":123456789}` — to **chat_id**.
4. Wpisz oba do `.env`:

```ini
TELEGRAM_BOT_TOKEN=8123456:AAH...
TELEGRAM_CHAT_ID=123456789
```

**Dla dwóch osób:** niech żona też napisze do bota, odczytaj jej `chat_id` i wpisz oba
po przecinku — alerty pójdą do obojga:

```ini
TELEGRAM_CHAT_ID=123456789,987654321
```

Test: w dashboardzie zakładka **Moje alerty → „wyślij testowe powiadomienie"**.

---

## 4. Wdrożenie za darmo: GitHub Actions + Neon

Efekt: scraper chodzi co 30 minut niezależnie od Twojego komputera, alerty lecą na telefon.
Koszt: **0 zł**.

### 4.1. Baza na Neonie (2 min)

1. Załóż konto na **neon.tech** (darmowy plan: 0,5 GB — starczy na kilkadziesiąt tysięcy ofert).
2. Utwórz projekt, region **Frankfurt (eu-central-1)**.
3. Skopiuj **connection string** i zamień prefiks na sterownik, którego używamy:

```
# Neon daje:      postgresql://user:haslo@ep-xxx.eu-central-1.aws.neon.tech/neondb?sslmode=require
# Ty wpisujesz:   postgresql+psycopg://user:haslo@ep-xxx.eu-central-1.aws.neon.tech/neondb?sslmode=require
```

> Darmowy Neon usypia bazę po ~5 minutach bezczynności i budzi ją przy pierwszym
> połączeniu (1–2 s). Dla nas bez znaczenia — łączymy się co pół godziny.

### 4.2. Repozytorium na GitHubie

```bash
cd wroclaw-flats
git init
git add .
git commit -m "Monitor mieszkań — Wrocław"
git branch -M main
git remote add origin https://github.com/TWOJ_LOGIN/wroclaw-flats.git
git push -u origin main
```

**Repozytorium musi być publiczne**, żeby GitHub Actions było darmowe i bez limitu minut.
Sekrety (token bota, hasło do bazy) **nie trafiają do repo** — `.gitignore` blokuje `.env`,
a klucze wpisujesz w ustawieniach GitHuba.

### 4.3. Sekrety

**Settings → Secrets and variables → Actions → New repository secret:**

| Nazwa | Wartość |
|---|---|
| `DATABASE_URL` | connection string z Neona (z `+psycopg`) |
| `TELEGRAM_BOT_TOKEN` | token od BotFathera |
| `TELEGRAM_CHAT_ID` | `123456789,987654321` |
| `DISCORD_WEBHOOK_URL` | opcjonalnie |

W zakładce **Variables** (obok Secrets) możesz też ustawić `ENABLED_SOURCES`,
`MAX_PAGES_PER_SOURCE` czy `MAX_NOTIFICATIONS_PER_RUN` bez ruszania kodu.

### 4.4. Pierwsze uruchomienie

**Actions → „Scrapowanie ofert" → Run workflow** → w polu `send_alerts` ustaw `false`.
Ten przebieg zapełni bazę bez zalewania Was powiadomieniami. Kolejne (co 30 min)
pójdą już z alertami.

Po pierwszym udanym przebiegu ustaw w **Variables** `SILENT_FIRST_RUN` na `0`.

### 4.5. Dashboard na Twoim komputerze

```bash
# .env lokalnie — ten sam DATABASE_URL co w sekretach GitHuba
DATABASE_URL=postgresql+psycopg://user:haslo@ep-xxx...neon.tech/neondb?sslmode=require
SCHEDULER_ENABLED=0        # scrapuje chmura, lokalnie tylko przeglądasz

python run.py
```

Filtry i alerty zapisane w dashboardzie lądują w tej samej bazie, więc chmurowy
scraper od razu ich używa. Nie musisz nic re-deployować.

### 4.6. O czym warto wiedzieć

- **GitHub potrafi opóźnić** zaplanowany workflow o kilka–kilkanaście minut przy dużym
  obciążeniu platformy. Przy szukaniu mieszkania to bez znaczenia; przy polowaniu
  na oferty, które znikają w 15 minut — jest.
- **Po 60 dniach bez commita GitHub wyłącza cron.** Dlatego dorzuciłem
  `keepalive.yml`, który raz w tygodniu robi pusty commit.
- **Limit czasu** jednego przebiegu ustawiony na 20 min (`timeout-minutes`).

---

## 5. Jak działa deduplikacja

Problem: to samo mieszkanie wystawia właściciel na OLX, dwa biura na Otodom i jedno
na Morizon. W wynikach widzicie cztery „różne" oferty i tracicie czas.

Rozwiązanie dwuetapowe (`app/dedup.py`):

**Etap 1 — blokowanie (tanie).** Każda oferta dostaje `fingerprint = hash(rejon, metraż
zaokrąglony do 1 m², liczba pokoi)`. Porównujemy tylko oferty z tego samego bloku
i bloków sąsiednich (±1 m²). Bez tego przy 20 tys. ofert trzeba by zrobić 200 mln
porównań na każdy cykl.

**Etap 2 — scoring (drogi, ale tylko dla kandydatów).** Ważone podobieństwo:

| Cecha | Waga |
|---|---|
| metraż (tolerancja 6%) | 0,25 |
| cena (tolerancja 10%) | 0,25 |
| tytuł + opis (Jaccard na tokenach + SequenceMatcher) | 0,25 |
| lokalizacja / osiedle | 0,15 |
| piętro | 0,10 |
| identyczna nazwa pliku zdjęcia | +0,35 (praktycznie przesądza) |

Powyżej progu `DEDUP_THRESHOLD` (domyślnie 0,82) oferty trafiają do jednej grupy.
W grupie „reprezentantem" zostaje **oferta od właściciela** (bo bez prowizji), a jak jej
nie ma — najtańsza, a przy remisie najstarsza. Reszta jest oznaczana jako duplikat
i ukrywana (przełącznik „Ukryj duplikaty ofert").

W szczegółach oferty widzisz sekcję **„Ta sama nieruchomość na innych portalach"**
z cenami — czasem to samo mieszkanie różni się o 20 tys. zł między biurami.

Próg możesz zmienić w `.env` i przeliczyć wszystko od nowa:
zakładka **Źródła → „przelicz deduplikację i mediany"**.

---

## 6. Jak wykrywane są okazje

Świadomie **mediana, nie średnia** — jeden penthouse za 30 tys./m² zawyżyłby średnią
dla całego osiedla i przestalibyście widzieć prawdziwe okazje.

Dla każdej oferty aplikacja szuka punktu odniesienia w kolejności:

1. mediana **osiedla** w tym samym segmencie rynku (pierwotny/wtórny),
2. mediana osiedla ogółem,
3. mediana **rejonu**,
4. mediana **miasta**.

Bierze pierwszą, dla której jest co najmniej **5 ofert** (`MIN_SAMPLE` w `stats.py`) —
mediana z dwóch ogłoszeń to nie jest mediana.

Oferta ≥ `DEAL_DISCOUNT_PCT` (domyślnie 12%) poniżej tej mediany dostaje plakietkę
**„okazja −X%"** i priorytet w kolejce powiadomień.

⚠️ Realistycznie: część „okazji" to parter przy torach, mieszkanie z lokatorem
albo udział w nieruchomości. Filtr `keywords_exclude` (np. `udział, służebność, licytacja`)
odsiewa większość takich przypadków.

---

## 7. Dodawanie nowych źródeł

Każdy scraper to plugin. Rejestruje się sam — nie ma centralnej listy do aktualizowania.

**Portal z klasycznym HTML-em** — same selektory, zero logiki:

```python
# app/scrapers/moj_portal.py
from .base import register
from .generic_html import HtmlListingScraper

@register
class MojPortalScraper(HtmlListingScraper):
    name = "moj_portal"
    label = "Mój Portal"
    base_url = "https://przyklad.pl"
    list_url = "https://przyklad.pl/mieszkania/wroclaw/sprzedaz"

    card_selectors     = ["article.listing-item", "div.offer-card"]
    link_selectors     = ["a.offer-link", "h2 a"]
    title_selectors    = ["h2.offer-title"]
    price_selectors    = ["span.price"]
    area_selectors     = ["span.area"]
    rooms_selectors    = ["span.rooms"]
    location_selectors = ["span.location"]
```

Dopisz `from . import moj_portal` w `app/scrapers/__init__.py` i `moj_portal`
do `ENABLED_SOURCES`. Selektory podaje się listą, bo portale zmieniają klasy CSS —
pierwszy działający wygrywa, więc redesign nie zabija scrapera od razu.

**Portal z API/JSON** — nadpisz `fetch()`, wzór w `app/scrapers/olx.py`.

**Deweloperzy (rynek pierwotny)** — nie piszesz kodu, tylko wpis w `config/developers.json`
(tryby: `jsonld`, `table`, `api`). Szczegóły w komentarzu na górze `app/scrapers/developers.py`.

**Facebook / Marketplace** — patrz sekcja 11.

---

## 8. Ochrona przed blokowaniem

Wszystko siedzi w `app/scrapers/http.py`:

| Mechanizm | Szczegóły |
|---|---|
| Rotacja User-Agent | pula 7 realnych przeglądarek (desktop + mobile), losowana per request |
| Pełne nagłówki | `Accept-Language: pl-PL`, `Sec-Fetch-*`, `Referer` — brak tych nagłówków to najprostszy sposób wykrycia bota |
| Throttling per host | losowa przerwa 1,5–4 s **między requestami do tego samego hosta**; różne portale lecą równolegle |
| Retry z backoffem | 429/403 → czekamy zgodnie z `Retry-After` albo 15 s × numer próby; 5xx → backoff wykładniczy |
| HTTP/2 + keep-alive | seria świeżych połączeń TLS jest bardziej podejrzana niż jedna sesja |
| Jitter w harmonogramie | APScheduler rozjeżdża start o ±20% — uderzanie równo co 15:00 to sygnał bota |
| Limit stron | `MAX_PAGES_PER_SOURCE=3` (~150 najnowszych ofert na portal) — przy cyklu co 30 min to w zupełności wystarcza |
| Proxy | opcjonalny `HTTP_PROXY` w `.env` |

W GitHub Actions opóźnienia są celowo większe (2–5 s), bo requesty lecą z puli IP
Azure, którą portale traktują podejrzliwiej niż domowe łącze.

**Jeśli mimo to zaczną Cię blokować:** zwiększ `REQUEST_DELAY_*`, zmniejsz
`MAX_PAGES_PER_SOURCE`, wydłuż cron do 60 minut. Uparte omijanie blokad kończy się
banem na stałe — lepiej scrapować rzadziej i stabilnie.

---

## 9. Diagnostyka: gdy portal przestanie działać

Portale robią redesign średnio co kilka miesięcy i wtedy scraper zwraca 0 ofert.
Nie musisz zgadywać, co się stało:

```bash
python -m app.tools.probe                    # lista scraperów
python -m app.tools.probe otodom             # ile ofert, jakie pola
python -m app.tools.probe otodom --debug     # + zrzut HTML do data/debug/
```

`--debug` zapisuje surową odpowiedź i sam sprawdza, czy w treści nie ma słów
`captcha` / `cloudflare` / `access denied` — od razu wiesz, czy to redesign,
czy blokada. Na końcu dostajesz metrykę kompletności:

```
Kompletność (cena + metraż): 47/50 = 94%
```

Spadek poniżej ~80% oznacza, że selektory zaczęły łapać nie to, co trzeba.

W dashboardzie zakładka **Źródła** pokazuje ostatnie 10 przebiegów każdego portalu
ze statusem, liczbą znalezionych i nowych ofert — jeśli któryś od trzech dni zwraca 0,
rzuca się w oczy.

---

## 10. API

Dokumentacja interaktywna: **http://127.0.0.1:8000/docs**

| Metoda | Endpoint | Opis |
|---|---|---|
| `POST` | `/api/search` | wyszukiwanie po pełnych kryteriach |
| `GET` | `/api/listings/{id}` | szczegóły + duplikaty + mediana + historia ceny |
| `GET` | `/api/meta` | rejony, osiedla, źródła, stan kanałów powiadomień |
| `GET` | `/api/stats` | liczniki, mediany, ostatnie przebiegi |
| `GET/POST/PATCH/DELETE` | `/api/filters` | zapisane filtry = alerty |
| `GET/POST/DELETE` | `/api/favorites` | ulubione |
| `GET` | `/api/notifications` | historia powiadomień |
| `POST` | `/api/notifications/test` | test konfiguracji kanałów |
| `POST` | `/api/manual-listing` | import ręczny (wklejony post z FB) |
| `POST` | `/api/scrape` | ręczne odpalenie cyklu |
| `POST` | `/api/rebuild` | przeliczenie deduplikacji i median |

Dashboard możesz zabezpieczyć hasłem — ustaw `APP_PASSWORD` w `.env`
(HTTP Basic Auth, dowolny login).

---

## 11. Kwestie prawne i ograniczenia

**Portale ogłoszeniowe.** Aplikacja czyta publicznie dostępne listingi w tempie
wolniejszym niż człowiek przeglądający oferty i nie omija żadnych zabezpieczeń.
Regulaminy części portali mimo to zakazują automatycznego pobierania danych —
to Wasza decyzja i ryzyko (realnie: blokada IP, nie sprawa sądowa). Dane trzymajcie
na własny użytek, nie redystrybuujcie.

**Facebook / Marketplace.** Nie ma tu scrapera logującego się na Wasze konto —
to łamie regulamin Meta i kończy się blokadą konta. Zamiast tego są dwie ścieżki:

- **Import ręczny (zalecany):** widzisz post w grupie → kopiujesz treść →
  w dashboardzie „＋ Wklej ofertę z FB / forum". Aplikacja wyciągnie cenę, metraż,
  pokoje, osiedle i cechy (balkon, garaż, KW, bez prowizji), a oferta trafi do tej samej
  bazy — z deduplikacją i porównaniem do mediany osiedla.
- **Tryb półautomatyczny** (`app/scrapers/facebook.py`, domyślnie wyłączony):
  Playwright z Waszym własnym profilem przeglądarki, logujecie się ręcznie raz.
  Na własną odpowiedzialność.

Praktycznie: w grupach „bez pośredników" dobre oferty znikają w 30 minut, więc alert
z portali + wklejenie z telefonu działa lepiej niż walka z blokadami.

**To nie jest doradztwo inwestycyjne.** Plakietka „okazja" znaczy tylko tyle,
że cena za m² odstaje od mediany osiedla — nie mówi nic o stanie prawnym, wadach
ukrytych ani o tym, czy sąsiad gra na perkusji. Zanim wpłacicie zadatek: księga
wieczysta, zaświadczenie o niezaleganiu z czynszem, rzut, a najlepiej rzeczoznawca.

**Znane ograniczenia MVP:**

- Selektory portali są sprawdzalne narzędziem `probe`, ale portale się zmieniają —
  policzcie na drobne poprawki co kilka miesięcy (sekcja 9).
- Identyfikatory kategorii/miasta OLX (`CATEGORY_ID`, `CITY_ID` w `app/scrapers/olx.py`)
  bywają zmieniane — jeśli OLX zwróci 0 ofert, to pierwsze miejsce do sprawdzenia.
- Deduplikacja opiera się na tekście i liczbach, nie na porównywaniu zdjęć —
  przy ogłoszeniach z zupełnie innym opisem i innymi zdjęciami może przepuścić duplikat.
- Brak mapy i geokodowania — lokalizacja jest rozpoznawana z tekstu (osiedle + rejon).

---

Powodzenia w poszukiwaniach 🏠
