"""Sesja bazy danych + inicjalizacja schematu."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base

IS_SQLITE = settings.database_url.startswith("sqlite")
# Bez limitu czasu literówka w adresie bazy potrafi zawiesić start na minuty,
# zamiast od razu powiedzieć, że coś jest nie tak.
connect_args = (
    {"check_same_thread": False} if IS_SQLITE else {"connect_timeout": 15}
)


def describe_database() -> str:
    """Krótki, czytelny opis bazy — do wypisania przy starcie."""
    url = settings.database_url
    if IS_SQLITE:
        return f"SQLite (plik na dysku): {url.split('///')[-1]}"
    host = url.split("@")[-1].split("/")[0] if "@" in url else "?"
    return f"PostgreSQL w chmurze: {host}"


try:
    engine = create_engine(
        settings.database_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
except ModuleNotFoundError as exc:
    # Najczęstszy przypadek: przejście z SQLite na Postgres bez doinstalowania
    # sterownika. Sam ModuleNotFoundError nic laikowi nie mówi.
    raise RuntimeError(
        "\n\n"
        "  Brakuje sterownika bazy danych.\n"
        "  W pliku .env ustawiony jest adres PostgreSQL, ale biblioteka do obsługi\n"
        "  tej bazy nie jest zainstalowana.\n\n"
        "  Napraw to jedną komendą (z aktywnym środowiskiem .venv):\n\n"
        "      pip install -r requirements.txt\n\n"
        f"  Szczegóły techniczne: {exc}\n"
    ) from exc


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")      # równoległy odczyt podczas scrapowania
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def init_db() -> None:
    try:
        Base.metadata.create_all(engine)
    except Exception as exc:  # noqa: BLE001
        if IS_SQLITE:
            raise
        raise RuntimeError(
            "\n\n"
            "  Nie udało się połączyć z bazą w chmurze.\n"
            f"  Adres: {describe_database()}\n\n"
            "  Najczęstsze przyczyny:\n"
            "    1. Adres w DATABASE_URL jest niekompletny — musi zaczynać się od\n"
            "       postgresql+psycopg://  i kończyć na  ?sslmode=require\n"
            "    2. Hasło w adresie zostało obcięte przy kopiowaniu.\n"
            "    3. Projekt na Neonie został usunięty albo uśpiony na dłużej.\n\n"
            "  Sprawdź linijkę DATABASE_URL:  notepad .env\n\n"
            f"  Szczegóły techniczne: {type(exc).__name__}: {str(exc)[:300]}\n"
        ) from exc


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sesja z automatycznym commit/rollback — do użycia w schedulerze i CLI."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """Dependency dla FastAPI."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
