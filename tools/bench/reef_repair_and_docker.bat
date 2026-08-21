@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Owner\lab\repair-taskcache2.ps1 -Apply > C:\Users\Owner\lab\repair-schedule.out 2>&1
sc start Schedule >> C:\Users\Owner\lab\repair-schedule.out 2>&1
timeout /t 2 /nobreak >nul
sc query Schedule >> C:\Users\Owner\lab\repair-schedule.out 2>&1
taskkill /F /IM "Docker Desktop.exe" >> C:\Users\Owner\lab\repair-schedule.out 2>&1
timeout /t 2 /nobreak >nul
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
echo launched_interactive > C:\Users\Owner\lab\docker-launch.stamp
