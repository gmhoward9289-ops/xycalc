#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c dir C:\Users\Owner\lab'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c dir C:\Users\Owner\Desktop'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c dir C:\Users\Owner\dev\xycalc\tools\bench'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c qwinsta'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c query user'
