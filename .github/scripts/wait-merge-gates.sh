#!/usr/bin/env bash
# Poll the Checks API until the latest run of each merge-gate job on this SHA
# is conclusion=success. Job names are the required_status_checks on the
# "main protection" ruleset (audit, build, test, test-py314) — the same set
# the ci.yml deploy job lists in `needs:`. Do not add test-py311 / test-py313
# or codespell; those are not merge gates.
set -euo pipefail

if [[ -z "${GITHUB_REPOSITORY:-}" || -z "${GITHUB_SHA:-}" ]]; then
  echo "::error::GITHUB_REPOSITORY and GITHUB_SHA must be set"
  exit 1
fi
if [[ -z "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ]]; then
  echo "::error::GH_TOKEN or GITHUB_TOKEN must be set"
  exit 1
fi
export GH_TOKEN="${GH_TOKEN:-$GITHUB_TOKEN}"

REQUIRED=(audit build test test-py314)
DEADLINE=$((SECONDS + 1800))
SLEEP=15

latest_for() {
  local name="$1"
  local json="$2"
  jq -c --arg n "$name" '
    [.check_runs[] | select(.name == $n)]
    | sort_by(.id)
    | last
    // empty
  ' <<<"$json"
}

while true; do
  json="$(gh api "repos/${GITHUB_REPOSITORY}/commits/${GITHUB_SHA}/check-runs?per_page=100")"
  pending=0
  failed=0
  for name in "${REQUIRED[@]}"; do
    row="$(latest_for "$name" "$json")"
    if [[ -z "$row" ]]; then
      echo "$name: not reported yet"
      pending=1
      continue
    fi
    status="$(jq -r '.status' <<<"$row")"
    conclusion="$(jq -r '.conclusion // "null"' <<<"$row")"
    echo "$name: status=$status conclusion=$conclusion"
    if [[ "$status" != "completed" ]]; then
      pending=1
      continue
    fi
    if [[ "$conclusion" != "success" ]]; then
      echo "::error::$name concluded $conclusion on ${GITHUB_SHA} — refusing to deploy a red SHA"
      failed=1
    fi
  done
  if [[ "$failed" -ne 0 ]]; then
    exit 1
  fi
  if [[ "$pending" -eq 0 ]]; then
    echo "merge-gate checks are green on ${GITHUB_SHA}: ${REQUIRED[*]}"
    exit 0
  fi
  if (( SECONDS >= DEADLINE )); then
    echo "::error::timed out waiting for merge-gate checks on ${GITHUB_SHA}"
    exit 1
  fi
  sleep "$SLEEP"
done
