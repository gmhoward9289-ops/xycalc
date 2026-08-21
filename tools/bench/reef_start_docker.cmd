@echo off
REM Start Docker Desktop in the interactive RDP session on reef, then wait for the engine.
schtasks /Delete /TN xycalc-start-docker /F >nul 2>&1
schtasks /Create /TN xycalc-start-docker /TR "\"C:\Program Files\Docker\Docker\Docker Desktop.exe\"" /SC ONCE /ST 23:59 /IT /F /RL HIGHEST
if errorlevel 1 exit /b 1
schtasks /Run /TN xycalc-start-docker
if errorlevel 1 exit /b 1

echo waiting for docker engine...
set /a n=0
:loop
set /a n+=1
docker info >nul 2>&1
if not errorlevel 1 (
  echo docker ready after %n% tries
  docker version
  exit /b 0
)
if %n% GEQ 60 (
  echo docker not ready after 60 tries
  sc query com.docker.service
  tasklist | findstr /i docker
  exit /b 2
)
ping -n 6 127.0.0.1 >nul
goto loop
