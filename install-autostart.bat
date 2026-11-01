@echo off
REM Makes the bot start by itself every time you log in to Windows, and stops the PC from sleeping while plugged in.
REM Double-click this file once.
cd /d "%~dp0"
schtasks /Create /TN "FX Signal Desk Bot" /TR "\"%~dp0start-bot.bat\"" /SC ONLOGON /RL LIMITED /F
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
echo.
echo Done. The bot will now start when you log in. To undo: schtasks /Delete /TN "FX Signal Desk Bot" /F
pause
