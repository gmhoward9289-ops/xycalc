#!/usr/bin/env bash
# Start ci / codespell / Deploy calculator on origin/main when a push event
# was swallowed. GitHub does not create workflow runs for `push` (or most
# other events) when the actor used GITHUB_TOKEN — which is what happens
# when auto-merge.yml queues native auto-merge and github-actions[bot]
# squash-merges. `workflow_dispatch` is the documented exception.
#
# Idempotent: if a workflow already has a run for main's current SHA, skip
# it. Deploy calculator still waits on merge-gate checks (audit, build,
# test, test-py314) before touching production.
set -euo pipefail

if [[ -z "${GITHUB_REPOSITORY:-}" ]]; then
  echo "::error::GITHUB_REPOSITORY must be set"
  exit 1
fi
if [[ -z "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ]]; then
  echo "::error::GH_TOKEN or GITHUB_TOKEN must be set"
  exit 1
fi
export GH_TOKEN="${GH_TOKEN:-$GITHUB_TOKEN}"

REPO="$GITHUB_REPOSITORY"
SHA="$(gh api "repos/${REPO}/commits/main" --jq .sha)"
echo "main SHA=${SHA}"

run_count_for() {
  local file="$1"
  gh api "repos/${REPO}/actions/workflows/${file}/runs?head_sha=${SHA}&per_page=1" \
    --jq '.total_count'
}

dispatch() {
  local file="$1"
  local n=0
  while true; do
    if gh workflow run "$file" --repo "$REPO" --ref main; then
      return 0
    fi
    n=$((n + 1))
    if (( n >= 6 )); then
      echo "::error::failed to dispatch ${file} after ${n} attempts"
      return 1
    fi
    echo "dispatch ${file} failed, retrying (${n})"
    sleep 5
  done
}

dispatch_if_missing() {
  local file="$1"
  local label="$2"
  local count
  count="$(run_count_for "$file")"
  if [[ "$count" != "0" ]]; then
    echo "${label}: already has ${count} run(s) on ${SHA}"
    return 0
  fi
  echo "${label}: no run on ${SHA} — workflow_dispatch"
  dispatch "$file"
}

dispatch_if_missing ci.yml ci
dispatch_if_missing codespell.yml Codespell
dispatch_if_missing deploy-calculator.yml "Deploy calculator"
