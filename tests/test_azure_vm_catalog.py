"""Azure Esv5/Esv6 SKU catalog: sourced figures and per-band-end picks."""

from __future__ import annotations

from xycalc.model import (
    Result,
    load_instance_catalog,
    parse_bytes,
    select_instance,
)

GiB = 1024**3

ESV5 = {
    "Esv5.Standard_E2s_v5": (2, 16),
    "Esv5.Standard_E4s_v5": (4, 32),
    "Esv5.Standard_E8s_v5": (8, 64),
    "Esv5.Standard_E16s_v5": (16, 128),
    "Esv5.Standard_E20s_v5": (20, 160),
    "Esv5.Standard_E32s_v5": (32, 256),
    "Esv5.Standard_E48s_v5": (48, 384),
    "Esv5.Standard_E64s_v5": (64, 512),
    "Esv5.Standard_E96s_v5": (96, 672),
}
ESV6 = {
    "Esv6.Standard_E2s_v6": (2, 16),
    "Esv6.Standard_E4s_v6": (4, 32),
    "Esv6.Standard_E8s_v6": (8, 64),
    "Esv6.Standard_E16s_v6": (16, 128),
    "Esv6.Standard_E20s_v6": (20, 160),
    "Esv6.Standard_E32s_v6": (32, 256),
    "Esv6.Standard_E48s_v6": (48, 384),
    "Esv6.Standard_E64s_v6": (64, 512),
    "Esv6.Standard_E96s_v6": (96, 768),
    "Esv6.Standard_E128s_v6": (128, 1024),
}


def _need(lo_gib: float, mode_gib: float, hi_gib: float) -> Result:
    return Result(
        model="test",
        lo=lo_gib * GiB,
        mode=mode_gib * GiB,
        hi=hi_gib * GiB,
        unit="bytes",
    )


def test_azure_catalog_matches_learn_basics_tables(conn):
    cat = load_instance_catalog(conn, "azure-vm")
    by_name = {i.name: i for i in cat}
    assert set(by_name) == set(ESV5) | set(ESV6)
    for name, (vcpu, gib) in {**ESV5, **ESV6}.items():
        spec = by_name[name]
        assert spec.vcpu == vcpu
        assert spec.ram_bytes == gib * GiB
        assert spec.source_url and "learn.microsoft.com" in spec.source_url
        assert spec.ebs_bandwidth_gbps is None
    assert "Esv5.Standard_E104is_v5" not in by_name
    assert "Esv6.Standard_E192is_v6" not in by_name


def test_esv6_pick_preserves_the_band(conn):
    cat = load_instance_catalog(conn, "azure-vm")
    sel = select_instance(_need(20, 30, 60), cat, family="Esv6", ceiling_bytes=None)
    assert sel["pick_lo"].name == "Esv6.Standard_E4s_v6"
    assert sel["pick_mode"].name == "Esv6.Standard_E4s_v6"
    assert sel["pick_hi"].name == "Esv6.Standard_E8s_v6"
    assert not sel["exceeds_pool"]


def test_esv5_exceeds_pool_above_672_gib(conn):
    cat = load_instance_catalog(conn, "azure-vm")
    sel = select_instance(_need(600, 650, 673), cat, family="Esv5", ceiling_bytes=None)
    assert sel["largest_in_pool"].name == "Esv5.Standard_E96s_v5"
    assert sel["largest_in_pool"].ram_bytes == 672 * GiB
    assert sel["exceeds_pool"]
    assert sel["pick_hi"] is None


def test_policy_ceiling_still_fires_on_azure(conn):
    cat = load_instance_catalog(conn, "azure-vm")
    ceiling = parse_bytes("1536GiB")
    sel = select_instance(_need(900, 1000, 1600), cat, family="Esv6", ceiling_bytes=ceiling)
    assert sel["largest_in_pool"].ram_bytes <= ceiling
    assert sel["exceeds_pool"]
    assert sel["pick_hi"] is None
    assert sel["pick_mode"].name == "Esv6.Standard_E128s_v6"
