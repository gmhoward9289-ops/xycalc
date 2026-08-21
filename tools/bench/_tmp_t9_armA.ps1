$ErrorActionPreference = "Continue"
$env:PROBE_DEV = "/dev/sdd"
$env:PROBE_ARM = "throttled"
$env:PROBE_FILE_MB = "2048"
$env:PROBE_RUNTIME = "12"
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Owner\lab\reef_run_t9_io.ps1