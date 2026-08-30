@echo off
chcp 65001 >nul
title Monitor mieszkan - Wroclaw
cd /d "%~dp0"

rem ---------------------------------------------------------------------
rem  Uruchamia dashboard bez potrzeby aktywowania srodowiska .venv.
rem  Wywolujemy Pythona ze srodowiska bezposrednio, wiec nie dotyczy nas
rem  ani zapominanie o aktywacji, ani blokada skryptow w PowerShellu.
rem
rem  Uzycie: kliknij dwukrotnie ten plik.
rem ---------------------------------------------------------------------

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   Nie znalazlem srodowiska .venv w folderze:
    echo   %CD%
    echo.
    echo   Utworz je raz, w terminalu otwartym w tym folderze:
    echo.
    echo       python -m venv .venv
    echo       .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo.
    echo   Brak pliku .env z ustawieniami. Tworze go z wzorca...
    copy ".env.example" ".env" >nul
    echo   Gotowe. Uzupelnij go pozniej poleceniem: notepad .env
    echo.
)

echo.
echo   Uruchamiam monitor mieszkan...
echo   Po starcie otworz w przegladarce:  http://127.0.0.1:8000
echo   Zatrzymanie: Ctrl+C  albo zamkniecie tego okna.
echo.

".venv\Scripts\python.exe" run.py

echo.
echo   Aplikacja zostala zatrzymana.
pause
