#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c sc query Schedule'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c sc query com.docker.service'
echo '---'
# WMI create in interactive session via explorer trick
ssh -o BatchMode=yes owner@192.168.68.20 'powershell -NoProfile -Command "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='\''cmd.exe /c C:\\Users\\Owner\\Desktop\\START-DOCKER.bat'\''; ProcessId=0}"'
echo '---'
# Try PsExec with -s system then -i 1 on Desktop exe directly
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c C:\Users\Owner\lab\PsExec64.exe -accepteula -i 1 -d "C:\Program Files\Docker\Docker\Docker Desktop.exe"'
