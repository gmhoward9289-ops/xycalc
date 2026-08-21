@echo off
set OUT=C:\Users\Owner\xycalc-results
mkdir "%OUT%" 2>nul
echo ===== phase A start %DATE% %TIME% ===== > "%OUT%\phase-a.log"
docker info >> "%OUT%\phase-a.log" 2>&1
if errorlevel 1 (
  echo FAIL docker info >> "%OUT%\phase-a.log"
  exit /b 1
)
echo Docker OK >> "%OUT%\phase-a.log"
docker pull mongo:7 >> "%OUT%\phase-a.log" 2>&1
docker pull python:3.12-slim >> "%OUT%\phase-a.log" 2>&1
echo Starting WSL probe... >> "%OUT%\phase-a.log"
wsl -e bash /mnt/c/Users/Owner/dev/xycalc/tools/bench/reef_run_cache_cliff_a.sh >> "%OUT%\phase-a.log" 2>&1
echo wsl_exit=%ERRORLEVEL% >> "%OUT%\phase-a.log"
dir "%OUT%\cache-cliff*" >> "%OUT%\phase-a.log" 2>&1
exit /b %ERRORLEVEL%
