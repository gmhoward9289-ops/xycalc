"""Regression tests for the production deploy trigger chain.

#123: after GITHUB_TOKEN auto-merge, GitHub swallows `push` so ci.yml and
deploy-calculator.yml never start. These assertions lock the shape that
restores those runs without squash-merging before required checks.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPTS = ROOT / ".github" / "scripts"


def _load(name: str) -> dict:
    # PyYAML 1.1 treats bare `on` as boolean true.
    raw = (WORKFLOWS / name).read_text(encoding="utf-8")
    raw = re.sub(r"^on:", '"on":', raw, count=1, flags=re.M)
    return yaml.safe_load(raw)


def test_ci_and_deploy_accept_workflow_dispatch():
    ci = _load("ci.yml")
    deploy = _load("deploy-calculator.yml")
    codespell = _load("codespell.yml")
    assert "workflow_dispatch" in ci["on"]
    assert "workflow_dispatch" in deploy["on"]
    assert "workflow_dispatch" in codespell["on"]


def test_deploy_still_waits_on_merge_gates_before_production():
    deploy = _load("deploy-calculator.yml")
    assert deploy["jobs"]["deploy"]["needs"] == ["merge-gates"]
    script = (SCRIPTS / "wait-merge-gates.sh").read_text(encoding="utf-8")
    assert "REQUIRED=(audit build test test-py314)" in script


def test_auto_merge_queues_native_auto_merge_only():
    text = (WORKFLOWS / "auto-merge.yml").read_text(encoding="utf-8")
    assert "gh pr merge --auto --squash --delete-branch" in text
    run_lines = [
        line
        for line in text.splitlines()
        if line.lstrip().startswith("run:") or "gh pr merge" in line
    ]
    assert any("--auto" in line and "gh pr merge" in line for line in run_lines)
    assert not any(
        "gh pr merge" in line and "--auto" not in line and not line.lstrip().startswith("#")
        for line in text.splitlines()
    )
    auto = _load("auto-merge.yml")
    assert auto["permissions"]["actions"] == "write"
    assert "kick-after-merge" in auto["jobs"]
    assert auto["jobs"]["kick-after-merge"]["needs"] == ["enable-auto-merge"]


def test_kick_backstop_watches_codeql_on_main():
    kick = _load("kick-main-pipelines.yml")
    assert kick["on"]["workflow_run"]["workflows"] == ["CodeQL"]
    assert "head_branch == 'main'" in kick["jobs"]["kick"]["if"]
    assert (SCRIPTS / "kick-main-pipelines.sh").is_file()
    assert (SCRIPTS / "wait-pr-merged.sh").is_file()
    script = (SCRIPTS / "kick-main-pipelines.sh").read_text(encoding="utf-8")
    assert "workflow_dispatch" in script
    assert "ci.yml" in script
    assert "deploy-calculator.yml" in script
    wait = (SCRIPTS / "wait-pr-merged.sh").read_text(encoding="utf-8")
    assert "gh pr merge" not in wait
