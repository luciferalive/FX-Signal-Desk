@echo off
REM FX Signal Desk - starts the bot on this computer and restarts it if it ever stops.
REM Double-click this file. Keep MetaTrader 5 open and logged in to your account.
title FX Signal Desk bot
cd /d "%~dp0"

set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

REM Optional: start MetaTrader 5 first (edit the path if yours is different, or delete these 3 lines)
if exist "C:\Program Files\XM MT5\terminal64.exe" (
  tasklist /FI "IMAGENAME eq terminal64.exe" | find /I "terminal64.exe" >nul || (start "" "C:\Program Files\XM MT5\terminal64.exe" & timeout /t 40 >nul)
)

:loop
echo.
echo [%date% %time%] starting the bot...
"%PY%" -m fxbot trade run
echo.
echo The bot stopped. Restarting in 20 seconds. Close this window to quit for good.
timeout /t 20 >nul
goto loop
