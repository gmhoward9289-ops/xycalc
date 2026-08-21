@echo off
REM Launch RUN-PHASE-A.bat in interactive session 1 (RDP) where Docker pipes live.
C:\Users\Owner\lab\PsExec64.exe -accepteula -i 1 -d cmd.exe /c "C:\Users\Owner\Desktop\RUN-PHASE-A.bat"
echo psexec_rc=%ERRORLEVEL% > C:\Users\Owner\xycalc-results\psexec-phase-a.out
query session >> C:\Users\Owner\xycalc-results\psexec-phase-a.out
