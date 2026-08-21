# Simple / Advanced calculator modes (#54)

**Status:** shipped in `src/xycalc/static/calculator.html` + `app.js`  
**Date:** 2026-08-21

## Goal

End-user sizing surface with few inputs; full instrument remains one click away. Same corpus math — no second answer path.

## Locked decisions

| Decision | Choice |
|---|---|
| Job | Buy/build first pass (`mongodb.size-to-instance`) |
| Simple inputs | Total DB size (required), vuln count (optional) |
| Out for now | Devices / asset inventory |
| Extra scenario fields | Advanced-only (index, foreign collections, current node, target count) |
| Graphs on Simple | Answer visuals only (RAM band + lo/mode/hi instance picks) |
| Entry | Persistent Simple \| Advanced control; first visit → Simple; remember in `localStorage` (`xycalc.calcMode`) |
| Implementation | Single page mode shell |

## Simple → scenario mapping

| User | Feeds |
|---|---|
| Total DB size | `baseline_storage_size` (bare number → GB) |
| Vulns (optional) | `baseline_vuln_count` (else `250000`) |
| (silent) | `target_vuln_count` = baseline → **today’s size**, no 3-year growth |
| (omitted) | `index_size`, `foreign_collections_size` (Advanced only) |
| Floor | Host RAM band clamped to ≥ **64 GiB** (`r8i.2xlarge`); instances re-picked |

## Refuses

Self-check and validation grades are unchanged; Simple does not hide failures. The page-level golden self-check still blanks the whole app on drift.

## Acceptance

- [x] End-user path has a short input set and a clear answer
- [x] Advanced / full instrument remains available (mode bar + “Open Advanced” under the answer)
- [x] Simple still refuses / warns when unvalidated or self-check fails
- [x] Preference remembered in `localStorage` without a reload surprise

## Out of scope

Separate URLs, CSS-only chrome hide, new models, devices field.
