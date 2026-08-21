@echo off
setlocal
set PATH=C:\Program Files\Docker\Docker\resources\bin;%PATH%
set OUT=C:\Users\Owner\xycalc-results\colocation-share
mkdir "%OUT%" 2>nul
set BASH=C:\Program Files\Git\bin\bash.exe
"%BASH%" -lc "export PATH='/c/Program Files/Docker/Docker/resources/bin:$PATH'; cd /c/Users/Owner/dev/xycalc/tools/bench/colocation_probe && docker version && MONGO_MEM_GB=4 SHARE_PCTS=50,70 OVERSUB=2.5 REDIS_MEM=2g CLICKHOUSE_MEM=4g WORKER_MEM=1g OUTDIR=/c/Users/Owner/xycalc-results/colocation-share bash ./share_sweep.sh" > "%OUT%\sweep.log" 2>&1
echo DONE rc=%ERRORLEVEL% >> "%OUT%\sweep.log"
