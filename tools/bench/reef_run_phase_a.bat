@echo off
REM After Docker Desktop is green: smoke + A1 run 1 via WSL.
setlocal
set ROOT=C:\Users\Owner\dev\xycalc
set OUT=C:\Users\Owner\xycalc-results
mkdir "%OUT%" 2>nul

echo Waiting for Docker engine...
:wait
docker info >nul 2>&1
if errorlevel 1 (
  ping -n 4 127.0.0.1 >nul
  goto wait
)
echo Docker ready.

wsl -e bash -lc "sed -i 's/\r$//' /mnt/c/Users/Owner/dev/xycalc/tools/bench/reef_run_cache_cliff_a.sh; bash /mnt/c/Users/Owner/dev/xycalc/tools/bench/reef_run_cache_cliff_a.sh"
echo.
echo Results under %OUT%
pause
