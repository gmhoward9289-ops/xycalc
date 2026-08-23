#!/usr/bin/env bash
# Poll until $PR_URL is merged, then kick main pipelines. Used by
# auto-merge.yml so a GITHUB_TOKEN auto-merge still starts ci + deploy.
# Does not squash-merge itself — native auto-merge must already be queued.
set -euo pipefail

if [[ -z "${PR_URL:-}" ]]; then
  echo "::error::PR_URL must be set"
  exit 1
fi
if [[ -z "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ]]; then
  echo "::error::GH_TOKEN or GITHUB_TOKEN must be set"
  exit 1
fi
export GH_TOKEN="${GH_TOKEN:-$GITHUB_TOKEN}"

DEADLINE=$((SECONDS + 2700))
SLEEP=15
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

while true; do
  json="$(gh pr view "$PR_URL" --json state,mergeCommit)"
  state="$(jq -r '.state' <<<"$json")"
  sha="$(jq -r '.mergeCommit.oid // empty' <<<"$json")"
  echo "PR state=${state} sha=${sha:-none}"
  if [[ "$state" == "MERGED" ]]; then
    bash "${ROOT}/.github/scripts/kick-main-pipelines.sh"
    exit 0
  fi
  if [[ "$state" != "OPEN" ]]; then
    echo "PR is ${state} and not merged; not dispatching"
    exit 0
  fi
  if (( SECONDS >= DEADLINE )); then
    echo "::warning::timed out waiting for auto-merge of ${PR_URL}; kick-main-pipelines.yml (CodeQL on main) is the backstop"
    exit 0
  fi
  sleep "$SLEEP"
done
