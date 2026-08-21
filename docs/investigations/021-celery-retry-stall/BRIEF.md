# BRIEF — Celery retry stall / recover (T8)

**As asked:** Do immediate vs exponential retries amplify load on a stalled
dependency, and how long to recover?

**Do NOT:** Publish amplification coefficients from `PROBE_STALL_MODE=pause`
if stall retries stay at 0 (connection freeze ≠ soft timeout).
