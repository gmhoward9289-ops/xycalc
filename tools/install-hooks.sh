#!/bin/sh
# Install this repo's git hooks into the current checkout.
#
# Hooks live in tools/ because .git/hooks is not version controlled and does not
# survive a clone -- which is the moment they matter most, since a fresh clone
# on cooper or swamplink is exactly where an accidental push would come from.
# Run this after every clone.
set -e

root=$(git rev-parse --show-toplevel)
cd "$root"

install -m 0755 tools/pre-push .git/hooks/pre-push
echo "installed pre-push  — allowlist: swamplink + this repo"

# The secret scan is George's repo-secret-scan skill, not a copy of it. This
# repo is headed for GitHub, and the exposure surface it guards (credentials,
# PII, public IPs) is the same one that skill already covers -- vendoring a
# second copy here would just be a copy that drifts.
#
# Absent on a machine that does not have the skill, which is the normal case
# for anyone else who clones this. The hook is skipped with a message rather
# than faked.
scan="${REPO_SECRET_SCAN_HOME:-$HOME/.claude/skills/repo-secret-scan/scripts}"
if [ -f "$scan/pre-commit" ]; then
    install -m 0755 "$scan/pre-commit" .git/hooks/pre-commit
    echo "installed pre-commit — repo-secret-scan (gitleaks + PII)"
else
    echo "skipped pre-commit  — repo-secret-scan not found at $scan" >&2
fi
