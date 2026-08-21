# Simple / Advanced calculator modes (#54)

**Status:** playable prototype in `src/xycalc/static/calculator.html`  
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
| Total DB size | `baseline_storage_size` |
| Vulns (optional) | `baseline_vuln_count` (else `250000`) |
| (silent) | `index_size` `40GB`, `foreign_collections_size` `80GB` |
| (omitted) | `target_vuln_count` → three-year growth path |

## Refuses

Self-check and validation grades are unchanged; Simple does not hide failures.

## Out of scope

Separate URLs, CSS-only chrome hide, new models, devices field.
