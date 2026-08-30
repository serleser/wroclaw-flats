@echo off
chcp 65001 >nul
title Monitor mieszkan - narzedzia
cd /d "%~dp0"

rem ---------------------------------------------------------------------
rem  Menu z komendami, ktore uruchamia sie od czasu do czasu.
rem  Tak jak start.bat, uzywa Pythona ze srodowiska .venv bezposrednio.
rem ---------------------------------------------------------------------

set PY=.venv\Scripts\python.exe

if not exist "%PY%" (
    echo.
    echo   Nie znalazlem srodowiska .venv. Najpierw uruchom raz w terminalu:
    echo       python -m venv .venv
    echo       .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

:menu
cls
echo.
echo   ================================================================
echo     MONITOR MIESZKAN - WROCLAW
echo   ================================================================
echo.
echo     1.  Pobierz oferty teraz (z wysylka alertow)
echo     2.  Pobierz oferty teraz (bez alertow)
echo     3.  Sprawdz portal - Otodom
echo     4.  Sprawdz portal - Nieruchomosci-online
echo     5.  Usun oferty spoza Wroclawia (podglad)
echo     6.  Usun oferty spoza Wroclawia (na powaznie)
echo     7.  Ustawienia Telegrama
echo     8.  Otworz plik ustawien (.env)
echo     9.  Doinstaluj brakujace biblioteki
echo.
echo     D.  Wyslij podsumowanie na Telegram (ostatnie 24 h)
echo     P.  Podglad podsumowania (bez wysylki)
echo.
echo     0.  Wyjscie
echo.
set /p wybor=  Wybierz numer i nacisnij Enter:

if "%wybor%"=="1" ( "%PY%" -m app.tools.run_once & goto pauza )
if "%wybor%"=="2" ( "%PY%" -m app.tools.run_once --no-alerts & goto pauza )
if "%wybor%"=="3" ( "%PY%" -m app.tools.probe otodom & goto pauza )
if "%wybor%"=="4" ( "%PY%" -m app.tools.probe nieruchomosci_online & goto pauza )
if "%wybor%"=="5" ( "%PY%" -m app.tools.cleanup & goto pauza )
if "%wybor%"=="6" ( "%PY%" -m app.tools.cleanup --usun & goto pauza )
if "%wybor%"=="7" ( "%PY%" -m app.tools.telegram_setup & goto pauza )
if "%wybor%"=="8" ( notepad .env & goto menu )
if "%wybor%"=="9" ( "%PY%" -m pip install -r requirements.txt & goto pauza )
if /i "%wybor%"=="D" ( "%PY%" -m app.tools.digest & goto pauza )
if /i "%wybor%"=="P" ( "%PY%" -m app.tools.digest --dry-run & goto pauza )
if "%wybor%"=="0" exit /b 0

goto menu

:pauza
echo.
pause
goto menu
