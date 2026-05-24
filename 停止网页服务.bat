@echo off
setlocal

for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":4173 .*LISTENING"') do (
  taskkill /PID %%p /F >nul 2>nul
)

echo Local web service on 127.0.0.1:4173 has been stopped if it was running.
pause
