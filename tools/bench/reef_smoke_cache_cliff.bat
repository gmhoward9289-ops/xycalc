@echo off
set OUT=C:\Users\Owner\xycalc-results
mkdir "%OUT%" 2>nul
echo ===== smoke %DATE% %TIME% ===== > "%OUT%\phase-a-smoke.log"
wsl -e bash -lc "sed -i 's/\r$//' /mnt/c/Users/Owner/dev/xycalc/tools/bench/reef_run_cache_cliff_smoke.sh /mnt/c/Users/Owner/dev/xycalc/tools/bench/cache_cliff_probe.sh; bash /mnt/c/Users/Owner/dev/xycalc/tools/bench/reef_run_cache_cliff_smoke.sh" >> "%OUT%\phase-a-smoke.log" 2>&1
echo wsl_exit=%ERRORLEVEL% >> "%OUT%\phase-a-smoke.log"
exit /b %ERRORLEVEL%
