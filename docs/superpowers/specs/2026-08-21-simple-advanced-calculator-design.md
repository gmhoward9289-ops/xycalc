# Simple / Advanced calculator modes (#54)

**Status:** playable prototype in `src/xycalc/static/calculator.html`  
**Date:** 2026-08-21

## Goal

End-user sizing surface with few inputs; full instrument remains one click away. Same corpus math — no second answer path.

## Locked decisions

| Decision | Choice |
|---|---|
| Job | Buy/build first pass (`mongodb.size-to-instance`) |
| Simple inputs | Total DB size (required) |
| Out for now | Devices / asset inventory; vuln record count on Simple (math/UX unclear) |
| Extra scenario fields | Advanced-only (index, foreign collections, current node, vuln counts) |
| Graphs on Simple | Answer visuals only (RAM band + lo/mode/hi instance picks) |
| Entry | Persistent Simple \| Advanced control; first visit → Simple; remember in `localStorage` (`xycalc.calcMode`) |
| Implementation | Single page mode shell |

## Simple → scenario mapping

| User | Feeds |
|---|---|
| Total DB size | `baseline_storage_size` (bare number → GB) |
| (silent) | `baseline_vuln_count` / `target_vuln_count` from scenario defaults (equal → **today’s size**, no growth) |
| (omitted) | `index_size`, `foreign_collections_size` (Advanced only) |
| Floor | Host RAM band clamped to ≥ **64 GiB** (`r8i.2xlarge`); instances re-picked |

## Refuses

Self-check and validation grades are unchanged; Simple does not hide failures.

## Out of scope

Separate URLs, CSS-only chrome hide, new models, devices field.
