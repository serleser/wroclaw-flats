"""Kreator konfiguracji Telegrama — znajduje chat id i zapisuje ustawienia.

    python -m app.tools.telegram_setup

Co robi po kolei:
  1. czyta token z pliku .env (a jak go nie ma — prosi o wklejenie),
  2. sprawdza token i mówi wprost, co jest z nim nie tak (401 vs 404),
  3. wypisuje wszystkie osoby, które napisały do bota, z ich numerami,
  4. zapisuje token i numery do .env,
  5. wysyła wiadomość testową, żeby było widać, że działa.

Powstało po to, żeby nie trzeba było wklejać tokenu do przeglądarki ani
polegać na zewnętrznych botach typu @userinfobot, które bywają wyłączone.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

# Polskie znaki i ramki działają też na starszej konsoli Windows
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_PATH = BASE_DIR / ".env"
API_BASE = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org")

LINE = "-" * 66


def head(text: str) -> None:
    print(f"\n{text}\n{LINE}")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Polska odmiana: 1 rozmowę, 2-4 rozmowy, 5+ rozmów (z wyjątkiem 12-14)."""
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def ask(prompt: str, default: str = "") -> str:
    try:
        answer = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nPrzerwano.")
        sys.exit(1)
    return answer or default


# --------------------------------------------------------------------- .env


def read_env() -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


def write_env(updates: Dict[str, str]) -> None:
    """Podmienia wskazane klucze, zachowując resztę pliku i komentarze."""
    if not ENV_PATH.exists():
        source = BASE_DIR / ".env.example"
        if source.exists():
            ENV_PATH.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            ENV_PATH.write_text("", encoding="utf-8")

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    remaining = dict(updates)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"

    for key, value in remaining.items():
        lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ Telegram


def clean_token(raw: str) -> str:
    """Wybacza typowe błędy kopiowania: spacje, cudzysłowy, przedrostek 'bot'."""
    token = raw.strip().strip('"').strip("'").replace(" ", "")
    token = re.sub(r"^https?://\S*?/bot", "", token, flags=re.IGNORECASE)
    token = re.sub(r"/\w+$", "", token)
    if token.lower().startswith("bot") and ":" in token[3:]:
        token = token[3:]
    return token


def call(token: str, method: str) -> Tuple[Optional[dict], Optional[str]]:
    """Zwraca (wynik, komunikat_błędu_po_polsku)."""
    url = f"{API_BASE}/bot{token}/{method}"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        return None, f"Nie udało się połączyć z Telegramem: {exc}"

    try:
        data = resp.json()
    except ValueError:
        return None, f"Telegram odpowiedział czymś, czego nie rozumiem (HTTP {resp.status_code})."

    if data.get("ok"):
        return data.get("result"), None

    code = data.get("error_code")
    desc = data.get("description", "")

    if code == 401:
        return None, (
            "Token jest nieprawidłowy (401 Unauthorized).\n"
            "   Pobierz go na nowo: napisz do @BotFather komendę /mybots,\n"
            "   wybierz swojego bota i kliknij 'API Token'."
        )
    if code == 404:
        return None, (
            "Telegram nie rozpoznał tego tokenu (404 Not Found).\n"
            "   Najczęściej znaczy to, że token jest niekompletny — musi zawierać\n"
            "   cyfry, dwukropek i długi ciąg liter po nim, np. 8123456789:AAHx1k...\n"
            "   Skopiuj go jeszcze raz w całości od @BotFather."
        )
    return None, f"Telegram odmówił ({code}): {desc}"


def collect_chats(updates: List[dict]) -> List[dict]:
    """Wyciąga unikalne rozmowy z listy zdarzeń."""
    seen: Dict[int, dict] = {}
    for update in updates or []:
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            node = update.get(key)
            if not isinstance(node, dict):
                continue
            chat = node.get("chat")
            if not isinstance(chat, dict) or "id" not in chat:
                continue
            name = " ".join(
                str(chat.get(k)) for k in ("first_name", "last_name") if chat.get(k)
            ) or chat.get("title") or "(bez imienia)"
            seen[chat["id"]] = {
                "id": chat["id"],
                "name": name,
                "username": chat.get("username", ""),
                "type": chat.get("type", "private"),
            }
    return list(seen.values())


def send_test(token: str, chat_id: str, name: str) -> bool:
    url = f"{API_BASE}/bot{token}/sendMessage"
    text = (
        "*Monitor mieszkań — Wrocław*\n\n"
        "Konfiguracja zakończona. Od teraz będziesz tu dostawać nowe oferty "
        "pasujące do Waszych kryteriów."
    )
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
            )
        ok = resp.status_code == 200 and resp.json().get("ok")
    except httpx.HTTPError:
        ok = False
    print(f"   {'[OK]  ' if ok else '[BŁĄD]'} {name} ({chat_id})")
    return bool(ok)


# ---------------------------------------------------------------------- main


def main() -> int:
    print("\n" + "=" * 66)
    print("  Kreator powiadomień Telegram")
    print("=" * 66)

    env = read_env()

    # --- 1. token ---
    head("1/4  Token bota")
    token = clean_token(env.get("TELEGRAM_BOT_TOKEN", ""))
    if token:
        print(f"Znalazłem token w pliku .env (…{token[-6:]}).")
        if ask("Użyć go? [T/n]: ", "T").lower().startswith("n"):
            token = ""
    if not token:
        print("Skopiuj token od @BotFather i wklej go poniżej.")
        print("Wygląda tak: 8123456789:AAHx1k7Yq...")
        token = clean_token(ask("\nToken: "))
    if not token:
        print("\nBez tokenu nie ruszymy. Uruchom kreator ponownie, gdy go zdobędziesz.")
        return 1

    # --- 2. weryfikacja ---
    head("2/4  Sprawdzam token")
    me, error = call(token, "getMe")
    if error:
        print(f"[BŁĄD] {error}")
        return 1
    print(f"[OK] Połączono z botem: {me.get('first_name')} (@{me.get('username')})")

    # --- 3. rozmowy ---
    head("3/4  Szukam osób, które napisały do bota")
    updates, error = call(token, "getUpdates")
    if error:
        print(f"[BŁĄD] {error}")
        return 1

    chats = collect_chats(updates)
    while not chats:
        print("Nikt jeszcze nie napisał do bota (albo napisał ponad dobę temu).")
        print(f"\nOtwórz w Telegramie rozmowę z @{me.get('username')},")
        print("kliknij START i wyślij cokolwiek. To samo niech zrobi żona.")
        if ask("\nGotowe? Naciśnij Enter, żeby sprawdzić ponownie (albo wpisz 'x'): ").lower() == "x":
            return 1
        updates, error = call(token, "getUpdates")
        if error:
            print(f"[BŁĄD] {error}")
            return 1
        chats = collect_chats(updates)

    word = plural(len(chats), "rozmowę", "rozmowy", "rozmów")
    print(f"Znalazłem {len(chats)} {word}:\n")
    for i, chat in enumerate(chats, 1):
        handle = f" @{chat['username']}" if chat["username"] else ""
        print(f"  {i}. {chat['name']}{handle}  ->  chat id: {chat['id']}")

    print("\nKtóre osoby mają dostawać powiadomienia?")
    choice = ask("Numery po przecinku, albo Enter = wszystkie: ")
    if choice:
        picked = []
        for part in choice.replace(" ", "").split(","):
            if part.isdigit() and 1 <= int(part) <= len(chats):
                picked.append(chats[int(part) - 1])
        chats = picked or chats

    chat_ids = ",".join(str(c["id"]) for c in chats)

    # --- 4. zapis i test ---
    head("4/4  Zapis ustawień")
    print(f"TELEGRAM_BOT_TOKEN = …{token[-6:]}")
    print(f"TELEGRAM_CHAT_ID   = {chat_ids}")
    if ask("\nZapisać do pliku .env? [T/n]: ", "T").lower().startswith("n"):
        print("\nNic nie zapisałem. Możesz wpisać te wartości ręcznie w .env.")
        return 0

    write_env({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_ids})
    print(f"[OK] Zapisano w {ENV_PATH}")

    if not ask("\nWysłać wiadomość testową? [T/n]: ", "T").lower().startswith("n"):
        print()
        for chat in chats:
            send_test(token, str(chat["id"]), chat["name"])

    print("\n" + "=" * 66)
    print("  Gotowe. Możesz wrócić do instrukcji — krok 14 masz z głowy.")
    print("=" * 66 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
