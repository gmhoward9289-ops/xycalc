@echo off
REM Kill session-0 Docker Desktop, then PsExec into RDP session 1.
taskkill /F /IM "Docker Desktop.exe" >nul 2>&1
timeout /t 2 /nobreak >nul
C:\Users\Owner\lab\PsExec64.exe -accepteula -i 1 -d "C:\Program Files\Docker\Docker\Docker Desktop.exe"
echo psexec_exit=%ERRORLEVEL% > C:\Users\Owner\lab\psexec-docker.out
tasklist /FI "IMAGENAME eq Docker Desktop.exe" >> C:\Users\Owner\lab\psexec-docker.out
