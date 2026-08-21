#!/usr/bin/env bash
set -euo pipefail
python -c "p=r'C:/Users/gmhow/dev/xycalc/tools/bench/reef_run_t11_share.bat'; d=open(p,'rb').read().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'); open(p,'wb').write(d)"
scp -o BatchMode=yes /c/Users/gmhow/dev/xycalc/tools/bench/reef_run_t11_share.bat \
  owner@192.168.68.20:C:/Users/Owner/lab/RUN-T11-SHARE.bat
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c dir "C:\Program Files\Git\bin\bash.exe"'
# Write a tiny ps1 launcher on remote
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c echo $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=\"cmd.exe /c C:\\Users\\Owner\\lab\\RUN-T11-SHARE.bat\"} > C:\Users\Owner\lab\launch-t11.ps1'
# Fix: scp a clean ps1 instead
scp -o BatchMode=yes /c/Users/gmhow/dev/xycalc/tools/bench/reef_wmi_launch.ps1 \
  owner@192.168.68.20:C:/Users/Owner/lab/reef_wmi_launch.ps1
# Update wmi launch to point at T11 bat — write fresh
cat > /tmp/launch_t11.ps1 <<'PSEOF'
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = 'cmd.exe /c C:\Users\Owner\lab\RUN-T11-SHARE.bat'
}
"ReturnValue=$($r.ReturnValue) ProcessId=$($r.ProcessId)" | Out-File C:\Users\Owner\lab\t11-wmi.out
PSEOF
scp -o BatchMode=yes /tmp/launch_t11.ps1 owner@192.168.68.20:C:/Users/Owner/lab/launch-t11.ps1
ssh -o BatchMode=yes owner@192.168.68.20 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Owner\lab\launch-t11.ps1'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c type C:\Users\Owner\lab\t11-wmi.out'
sleep 90
echo '=== sweep.log ==='
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c type C:\Users\Owner\xycalc-results\colocation-share\sweep.log'
echo '=== docker ==='
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker ps --format {{.Names}}'
