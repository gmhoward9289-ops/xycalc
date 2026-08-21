#!/usr/bin/env bash
set -euo pipefail
# CRLF for bat
python -c "p=r'C:/Users/gmhow/dev/xycalc/tools/bench/reef_psexec_session1.bat'; d=open(p,'rb').read().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'); open(p,'wb').write(d)"

scp -o BatchMode=yes /c/Users/gmhow/dev/xycalc/tools/bench/reef_psexec_session1.bat \
  owner@192.168.68.20:C:/Users/Owner/lab/

# Run the bat via WMI (session 0 is fine for PsExec itself — PsExec then injects into session 1)
ssh -o BatchMode=yes owner@192.168.68.20 \
  'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Owner\lab\reef_wmi_launch.ps1' || true

# Overwrite wmi target to our new bat
ssh -o BatchMode=yes owner@192.168.68.20 'powershell -NoProfile -Command "$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='\''cmd.exe /c C:\\Users\\Owner\\lab\\reef_psexec_session1.bat'\''}; \"Return=$($r.ReturnValue) Pid=$($r.ProcessId)\" | Out-File C:\\Users\\Owner\\lab\\psexec-wmi.out"'

sleep 5
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c type C:\Users\Owner\lab\psexec-docker.out'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c type C:\Users\Owner\lab\psexec-wmi.out'
echo '--- sessions of Docker ---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c tasklist /FI "IMAGENAME eq Docker Desktop.exe" /V'

echo '== poll engine =='
for i in $(seq 1 30); do
  out=$(ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker info -f {{.ServerVersion}}' 2>/dev/null \
    | tr -d '\r' | grep -E '^[0-9]' | tail -1 || true)
  if [ -n "$out" ]; then echo "READY $out"; exit 0; fi
  echo "try $i"
  sleep 5
done
exit 1
