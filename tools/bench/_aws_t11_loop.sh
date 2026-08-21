#!/usr/bin/env bash
# Emits a wake sentinel every 15 minutes for the T11 AWS monitor loop.
while true; do
  sleep 900
  echo 'AGENT_LOOP_TICK_t11-aws {"prompt":"Run bash tools/bench/_aws_t11_monitor.sh. If DONE: confirm teardown + summarize summary.jsonl. If RUNNING: brief progress. If STALLED: diagnose."}'
done
