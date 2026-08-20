#!/usr/bin/env bash
# Run the Celery probe end to end and print its JSON.
#
#   ./run.sh                                  # defaults (acks_late=1)
#   PROBE_ACKS_LATE=0 ./run.sh                # control: early ack (vacuous for redelivery)
#   PROBE_VISIBILITY_TIMEOUT=5 ./run.sh       # shorter redelivery window (needs acks_late=1)
#   PROBE_SECONDS=10 PROBE_DOCS=800000 PROBE_RATES=50,200 ./run.sh   # smoke
set -euo pipefail
cd "$(dirname "$0")"

# The blkio limit needs a real block device; compose.yml names /dev/sda, which
# is right on swamplink and wrong elsewhere. Fail loudly rather than silently
# running unthrottled, which would be a run that proves nothing.
if [ ! -b "${PROBE_DEV:-/dev/sda}" ]; then
    echo "compose.yml throttles ${PROBE_DEV:-/dev/sda}, which is not a block device here." >&2
    echo "Edit blkio_config in compose.yml for this host, or run on swamplink." >&2
    exit 1
fi

cleanup() { docker compose down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

docker compose up -d --build redis bookkeeping mongo worker >&2
docker compose run --rm --no-deps -T driver python drive.py
