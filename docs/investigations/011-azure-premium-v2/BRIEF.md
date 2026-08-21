# BRIEF — Azure Premium SSD v2 throughput ceiling

Validate `azure.premium-v2-throughput-ceiling` against a live Premium SSD v2
disk (control-plane acceptance + optional fio delivery), free Azure credits
only. Plan: `docs/plans/azure-premium-v2-throughput-validation.md`.

Harness: `tools/bench/azure_premium_v2_probe.{sh,py}` · Import:
`tools/import_azure_probe.py` · Lifecycle: `tools/bench/azure_psv2_lifecycle.{sh,ps1}`.
