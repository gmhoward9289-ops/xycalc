"""Turn pasted MongoDB metrics into model inputs and a candidate observation.

This is the ramp the corpus has been missing: a contributor pastes
``db.stats()`` / ``serverStatus`` JSON and gets (1) the fields the models
actually consume, (2) a sizing run on those fields, and (3) optionally a
YAML skeleton they can finish into a PR.

Honesty: an ingested paste is a **candidate**, not a cited fact and not a
validation. Default ingest writes nothing. ``--emit-observation`` writes
candidate YAML in corpus layout (``sources/`` + ``observations/``); paths
under the published tree ``data/`` are refused unless ``--force-corpus``.
MCP ``ingest_dbstats`` never writes files. Provenance that cannot be derived
from the paste is ``TODO`` or omitted — never ``source_type: measured`` and
never today's calendar date stamped into tag/slug/source id.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .model import format_quantity

# serverStatus WiredTiger cache keys. Spelled out because they contain spaces
# and a typo becomes a silent miss — same reason tools/import_mongodb.py
# keeps these as constants.
K_IN_CACHE = "bytes currently in the cache"
K_MAX = "maximum bytes configured"
K_DIRTY = "tracked dirty bytes in the cache"

# db.stats() fields this maps. Anything else in that object is reported as
# ignored so a reader can see what the paste contained that we did not take.
STATS_USED = ("dataSize", "storageSize", "indexSize", "db", "scaleFactor")
CACHE_USED = (K_IN_CACHE, K_MAX, K_DIRTY)
VERSION_KEYS = ("version", "versionString")
DATE_KEYS = ("at", "localTime")

TODO = "TODO"

_MAP_TOOLS = None


def _parameter_map():
    """The one metric-name → parameter map. Grafana import and db.stats ingest
    must not grow a second copy that can drift."""
    global _MAP_TOOLS
    if _MAP_TOOLS is None:
        import sys
        from pathlib import Path

        tools_dir = Path(__file__).resolve().parents[2] / "tools"
        if str(tools_dir) not in sys.path:
            sys.path.insert(0, str(tools_dir))
        import import_metrics_export as ime  # type: ignore

        _MAP_TOOLS = (ime.load_map(), ime.resolve_mapping)
    return _MAP_TOOLS


def corpus_mapping(field: str) -> dict[str, str]:
    """Look up a db.stats / cache field in tools/metrics_parameter_map.yaml.

    Missing keys fail here rather than growing a parallel slug table in this
    module.
    """
    mappings, resolve = _parameter_map()
    hit = resolve(field, mappings, system=None, parameter=None)
    if not hit:
        raise IngestError(
            f"{field!r} is not in tools/metrics_parameter_map.yaml. Add it "
            f"there (and a parameter in data/parameters.yaml if needed) "
            f"rather than inventing a slug in ingest.py."
        )
    return hit

_OBS_HEADER = """\
# CANDIDATE observation skeleton from `xycalc ingest`.
# This paste is NOT a cited corpus fact and has NOT been validated.
# Fill every TODO before opening a PR. Split the two lists into:
#   data/sources/<tag>.yaml         ← sources:
#   data/observations/<tag>.yaml    ← observations:
# Then: xycalc build && xycalc audit
# Writing under data/ is refused unless --force-corpus.
"""

# Published corpus compiled by xycalc.build (not local/).
PUBLISHED_CORPUS = Path(__file__).resolve().parents[2] / "data"


class IngestError(Exception):
    """Unreadable paste. Loud failure is the good outcome."""


def read_number(v: Any) -> int | float:
    """Read a number that may have arrived as a 64-bit wrapper.

    mongosh's JSON.stringify does not emit plain integers for NumberLong. It
    emits ``{"low": ..., "high": ..., "unsigned": ...}`` — a two's-complement
    pair — and EJSON emits ``{"$numberLong": "..."}``. Observed in the wild on
    2026-07-31: ``maximum bytes configured`` came back as
    ``{'high': 0, 'low': -2147483648, 'unsigned': False}``, which is 2 GiB,
    and which a naive reader would take as -2147483648 or crash on.

    The crash is the good outcome. Silently ingesting a negative cache size
    as a measurement is the bad one.
    """
    if isinstance(v, bool):
        raise IngestError(f"expected a number, got a boolean: {v!r}")
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return int(v) if v.lstrip("-").isdigit() else float(v)
        except ValueError as e:
            raise IngestError(f"cannot read a number from {v!r}") from e
    if isinstance(v, dict):
        if "$numberLong" in v:
            return int(v["$numberLong"])
        if "$numberInt" in v:
            return int(v["$numberInt"])
        if "$numberDouble" in v:
            return float(v["$numberDouble"])
        if "$numberDecimal" in v:
            return float(v["$numberDecimal"])
        if "low" in v and "high" in v:
            return (int(v["high"]) << 32) + (int(v["low"]) & 0xFFFFFFFF)
    raise IngestError(f"cannot read a number from {v!r}")


def _iso_date(v: Any) -> str | None:
    """Best-effort date (YYYY-MM-DD) from a dump's timestamp. None if absent."""
    if v is None:
        return None
    if isinstance(v, dict) and "$date" in v:
        v = v["$date"]
        if isinstance(v, dict) and "$numberLong" in v:
            # millis since epoch — too easy to get the timezone wrong; do not
            # invent a calendar day from a bare epoch.
            return None
    text = str(v).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


@dataclass
class FieldRead:
    path: str
    value: Any
    used_as: str
    unit: str | None = None

    def as_dict(self) -> dict:
        out = {"path": self.path, "value": self.value, "used_as": self.used_as}
        if self.unit:
            out["unit"] = self.unit
        return out


@dataclass
class Extraction:
    """What a paste yielded. Empty collections mean 'not in the dump'."""

    kind: str = "mongodb"
    read: list[FieldRead] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    model_inputs: dict[str, int | float] = field(default_factory=dict)
    observations: list[dict] = field(default_factory=list)
    version: str | None = None
    observed_on: str | None = None
    db_name: str | None = None
    configured_cache: int | float | None = None
    resident_cache: int | float | None = None
    data_size: int | float | None = None
    storage_size: int | float | None = None
    index_size: int | float | None = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "read": [f.as_dict() for f in self.read],
            "ignored": list(self.ignored),
            "warnings": list(self.warnings),
            "model_inputs": dict(self.model_inputs),
            "system_version": self.version,
            "observed_on": self.observed_on,
            "db": self.db_name,
            "configured_cache": self.configured_cache,
            "resident_cache": self.resident_cache,
        }


def parse_metrics(raw: Any) -> dict:
    """Accept a dict, a JSON string, or bytes. Refuse anything else."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise IngestError("empty metrics paste")
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as e:
            raise IngestError(f"metrics are not JSON: {e}") from e
        if not isinstance(doc, dict):
            raise IngestError(
                f"metrics JSON must be an object, got {type(doc).__name__}"
            )
        return doc
    raise IngestError(f"cannot read metrics from {type(raw).__name__}")


_STATS_KEYS = ("storageSize", "dataSize", "indexSize")


def _looks_like_stats(d: dict) -> bool:
    """A db.stats()-shaped object. Any of the three size fields is enough.

    Requiring storageSize AND dataSize was a false gate: the wt-cache inputs
    are storageSize and indexSize, and a mongod-shaped paste with storageSize
    but no dataSize was classified as serverStatus and the sizes were ignored.
    """
    return isinstance(d, dict) and any(k in d for k in _STATS_KEYS)


def _looks_like_cache(d: dict) -> bool:
    return isinstance(d, dict) and (K_IN_CACHE in d or K_MAX in d)


def _looks_like_server_status(d: dict) -> bool:
    return isinstance(d, dict) and (
        "wiredTiger" in d or (d.get("process") in ("mongod", "mongos"))
    )


def _find_stats(dump: dict) -> tuple[dict, str]:
    for key in ("stats", "dbStats", "dbstats", "db_stats"):
        inner = dump.get(key)
        if _looks_like_stats(inner):
            return inner, key
    if _looks_like_stats(dump):
        return dump, ""
    return {}, ""


def _find_cache(dump: dict) -> tuple[dict, str]:
    for key in ("cache",):
        inner = dump.get(key)
        if _looks_like_cache(inner):
            return inner, key
    ss, ss_path = _find_server_status(dump)
    wt = (ss or {}).get("wiredTiger") or {}
    cache = wt.get("cache") if isinstance(wt, dict) else None
    if _looks_like_cache(cache):
        prefix = f"{ss_path}." if ss_path else ""
        return cache, f"{prefix}wiredTiger.cache"
    if _looks_like_cache(dump):
        return dump, ""
    return {}, ""


def _find_server_status(dump: dict) -> tuple[dict, str]:
    for key in ("serverStatus", "serverstatus", "server_status"):
        inner = dump.get(key)
        if isinstance(inner, dict):
            return inner, key
    # A paste may be both: process=mongod plus storageSize. Do not hide the
    # serverStatus region just because the object also looks like stats.
    if _looks_like_server_status(dump):
        return dump, ""
    return {}, ""


def _find_version(dump: dict, ss: dict) -> tuple[str | None, str | None]:
    for path, obj in (
        ("version", dump.get("version")),
        ("serverStatus.version", ss.get("version") if ss else None),
        ("buildInfo.version", (dump.get("buildInfo") or {}).get("version")
         if isinstance(dump.get("buildInfo"), dict) else None),
    ):
        if isinstance(obj, (str, int, float)) and str(obj).strip():
            return str(obj).strip(), path
    return None, None


def _find_when(dump: dict, ss: dict) -> tuple[str | None, str | None]:
    for path, obj in (
        ("at", dump.get("at")),
        ("localTime", dump.get("localTime")),
        ("serverStatus.localTime", ss.get("localTime") if ss else None),
    ):
        parsed = _iso_date(obj)
        if parsed:
            return parsed, path
    return None, None


def _prefix(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def extract_mongodb(dump: dict) -> Extraction:
    """Pull model inputs and observation rows out of a MongoDB metrics dump."""
    ext = Extraction()
    stats, stats_path = _find_stats(dump)
    cache, cache_path = _find_cache(dump)
    ss, ss_path = _find_server_status(dump)

    if not stats and not cache and not ss:
        raise IngestError(
            "paste does not look like db.stats() or serverStatus JSON. "
            "Expected storageSize, dataSize, and/or indexSize, "
            "wiredTiger.cache, or a wrapper with `stats` / `cache` / "
            "`serverStatus` keys."
        )

    version, version_path = _find_version(dump, ss)
    if version:
        ext.version = version
        ext.read.append(
            FieldRead(version_path or "version", version, "system_version / applies_to")
        )

    when, when_path = _find_when(dump, ss)
    if when:
        ext.observed_on = when
        ext.read.append(FieldRead(when_path or "at", when, "observed_on"))

    ignored: list[str] = []

    if stats:
        scale = 1
        if "scaleFactor" in stats:
            scale = read_number(stats["scaleFactor"])
            ext.read.append(
                FieldRead(_prefix(stats_path, "scaleFactor"), scale, "unit check")
            )
            if scale not in (0, 1):
                raise IngestError(
                    f"db.stats() scaleFactor is {scale}, not 1 — sizes are not "
                    f"in bytes. Re-run without a scale (db.stats() / "
                    f"db.stats({{scale: 1}})) rather than guessing the unit."
                )
        if "db" in stats and stats["db"] is not None:
            ext.db_name = str(stats["db"])
            ext.read.append(
                FieldRead(_prefix(stats_path, "db"), ext.db_name, "database name")
            )
        mapping = (
            ("storageSize", "storage_size",
             "mongodb.wt-cache --storage-size (compressed collection bytes)"),
            ("indexSize", "index_size",
             "mongodb.wt-cache --index-size"),
            ("dataSize", None,
             "uncompressed bytes — NOT the model's --storage-size input"),
        )
        for key, input_key, used_as in mapping:
            if key not in stats:
                continue
            hit = corpus_mapping(key)
            value = read_number(stats[key])
            setattr(ext, {
                "storageSize": "storage_size",
                "indexSize": "index_size",
                "dataSize": "data_size",
            }[key], value)
            if input_key:
                ext.model_inputs[input_key] = value
            ext.read.append(
                FieldRead(
                    _prefix(stats_path, key),
                    value,
                    used_as,
                    unit=hit.get("unit") or "bytes",
                )
            )
            ext.observations.append(
                {
                    "parameter": hit["parameter"],
                    "value": value,
                    "unit": hit.get("unit") or "bytes",
                    "field": key,
                    "path": _prefix(stats_path, key),
                }
            )
        if (
            ext.data_size is not None
            and ext.storage_size is not None
            and ext.storage_size != 0
        ):
            ratio = round(ext.data_size / ext.storage_size, 3)
            hit = corpus_mapping("dataSize/storageSize")
            ext.observations.append(
                {
                    "parameter": hit["parameter"],
                    "value": ratio,
                    "unit": hit.get("unit") or "ratio",
                    "field": "dataSize/storageSize",
                    "path": f"{_prefix(stats_path, 'dataSize')} / {_prefix(stats_path, 'storageSize')}",
                }
            )
            ext.read.append(
                FieldRead(
                    f"{_prefix(stats_path, 'dataSize')}/{_prefix(stats_path, 'storageSize')}",
                    ratio,
                    "storage.compression_ratio (this collection set only)",
                    unit="ratio",
                )
            )
        used = set(STATS_USED)
        for key in stats:
            if key not in used:
                ignored.append(_prefix(stats_path, key))

    if cache:
        if K_IN_CACHE in cache:
            ext.resident_cache = read_number(cache[K_IN_CACHE])
            hit = corpus_mapping(K_IN_CACHE)
            ext.read.append(
                FieldRead(
                    _prefix(cache_path, K_IN_CACHE),
                    ext.resident_cache,
                    "observation cache.size_bytes (resident contents, not configured size)",
                    unit=hit.get("unit") or "bytes",
                )
            )
            ext.observations.append(
                {
                    "parameter": hit["parameter"],
                    "value": ext.resident_cache,
                    "unit": hit.get("unit") or "bytes",
                    "field": K_IN_CACHE,
                    "path": _prefix(cache_path, K_IN_CACHE),
                }
            )
        if K_MAX in cache:
            ext.configured_cache = read_number(cache[K_MAX])
            ext.read.append(
                FieldRead(
                    _prefix(cache_path, K_MAX),
                    ext.configured_cache,
                    "configured WiredTiger cache (not a mongodb.wt-cache input)",
                    unit="bytes",
                )
            )
            if ext.resident_cache and ext.configured_cache:
                frac = ext.resident_cache / ext.configured_cache
                if frac > 0.75:
                    ext.warnings.append(
                        f"cache is {frac:.0%} full. Resident bytes then measure "
                        f"how much fits, not how much the working set needs — "
                        f"read a validation against this paste as a floor."
                    )
        if K_DIRTY in cache:
            dirty = read_number(cache[K_DIRTY])
            ext.read.append(
                FieldRead(
                    _prefix(cache_path, K_DIRTY),
                    dirty,
                    "dirty bytes (context only, not a model input)",
                    unit="bytes",
                )
            )
        used = set(CACHE_USED)
        for key in cache:
            if key not in used:
                ignored.append(_prefix(cache_path, key))

    # Top-level wrapper keys we did not interpret as a region.
    wrapper_used = {
        "stats", "dbStats", "dbstats", "db_stats",
        "cache",
        "serverStatus", "serverstatus", "server_status",
        "version", "at", "localTime", "buildInfo",
    }
    if stats_path == "" and stats:
        # Raw db.stats() at the top level — already walked.
        pass
    elif cache_path == "" and cache and not stats:
        pass
    elif ss_path == "" and ss and not stats:
        for key in ss:
            if key in ("version", "localTime", "wiredTiger"):
                continue
            ignored.append(key)
        wt = ss.get("wiredTiger") or {}
        if isinstance(wt, dict):
            for key in wt:
                if key != "cache":
                    ignored.append(_prefix("wiredTiger", key))
    else:
        for key in dump:
            if key not in wrapper_used:
                ignored.append(key)
        if ss and ss_path:
            for key in ss:
                if key in ("version", "localTime", "wiredTiger"):
                    continue
                ignored.append(_prefix(ss_path, key))
            wt = ss.get("wiredTiger") or {}
            if isinstance(wt, dict):
                for key in wt:
                    if key != "cache":
                        ignored.append(_prefix(f"{ss_path}.wiredTiger", key))

    ext.ignored = sorted(ignored)
    # `not value` is wrong here: storageSize 0 is present, not missing.
    if "storage_size" not in ext.model_inputs:
        ext.warnings.append(
            "no storageSize in the paste — mongodb.wt-cache cannot run. "
            "Include db.stats() (scale 1) or a wrapper with a `stats` key."
        )
    return ext


def observation_skeleton(
    ext: Extraction,
    *,
    tag: str | None = None,
    workload: str | None = None,
    machine_class: str | None = None,
    publisher: str | None = None,
    source_type: str | None = None,
) -> dict:
    """YAML-ready dict. Missing provenance is ``TODO``, never invented.

    ``source_type`` is ``TODO`` unless the caller passes ``measured`` or
    ``benchmark``. A paste is not evidence of which. Dates, publisher,
    workload, machine class, and today's calendar are not guessed into
    tag/slug/source id.
    """
    when = ext.observed_on or TODO
    version = ext.version or TODO
    db_bit = (ext.db_name or "mongodb").replace(" ", "-")
    if tag:
        tag = tag.replace(" ", "-")
    elif ext.observed_on:
        tag = f"ingest-{db_bit}-{ext.observed_on}"
    else:
        tag = f"ingest-{db_bit}"
    source_slug = f"obs-mongodb-{tag}"
    resolved_source_type = source_type or TODO

    # Title describes the instrument, not a fake paper. Publisher is who
    # ran it — unknown unless the caller said.
    source = {
        "slug": source_slug,
        "title": (
            f"MongoDB db.stats() / wiredTiger.cache paste, {when}"
            if when != TODO
            else "MongoDB db.stats() / wiredTiger.cache paste, TODO date"
        ),
        "publisher": publisher or TODO,
        "retrieved_on": when if when != TODO else TODO,
        "source_type": resolved_source_type,
        "notes": (
            "CANDIDATE from `xycalc ingest`. Not yet cited in the published "
            "corpus and not a validation of any model. Fill every TODO "
            "(publisher, workload, machine_class, source_type, and any "
            "missing date/version) before opening a PR. "
            f"MongoDB version in the paste: {version}. "
            "source_type is TODO until you set `measured` (a running "
            "system) or `benchmark` (a committed harness)."
        ),
    }
    if ext.version:
        source["version"] = ext.version

    common = {
        "system": "mongodb",
        "workload": workload or TODO,
        "machine_class": machine_class or TODO,
        "system_version": version,
        "observed_on": when,
        "source": source_slug,
    }

    observations = []
    for row in ext.observations:
        field_note = row["path"]
        slug_bit = (
            str(row["field"]).replace(" ", "-").replace("/", "-")
            if row.get("field")
            else row["parameter"].rsplit(".", 1)[-1]
        )
        notes = f"Ingested from {field_note}. Candidate — not yet reviewed."
        if row["parameter"] == "storage.compression_ratio":
            notes = (
                f"dataSize / storageSize from this paste ({field_note}). "
                "Applies to this collection set and no other: compressibility "
                "is a property of the documents. Candidate — not yet reviewed."
            )
        if row["parameter"] == "cache.size_bytes":
            notes = (
                f"serverStatus wiredTiger.cache['{K_IN_CACHE}'] ({field_note}). "
                "This is resident contents, not the cache size to configure. "
                "Candidate — not yet reviewed."
            )
        observations.append(
            {
                "slug": f"{tag}-{slug_bit}",
                "parameter": row["parameter"],
                "value": row["value"],
                "unit": row["unit"],
                "notes": notes,
                **common,
            }
        )

    return {
        "tag": tag,
        "sources": [source],
        "observations": observations,
        "applies_to": version,
    }


def render_observation_yaml(skeleton: dict) -> str:
    body = yaml.safe_dump(
        {
            "sources": skeleton["sources"],
            "observations": skeleton["observations"],
        },
        sort_keys=False,
        allow_unicode=True,
    )
    return _OBS_HEADER + body


def is_published_corpus_path(dest: Any) -> bool:
    """True if dest is the published ``data/`` tree ``xycalc.build`` compiles."""
    if dest in (None, "-", Path("-")):
        return False
    path = Path(dest).expanduser().resolve()
    corpus = PUBLISHED_CORPUS.resolve()
    if path == corpus:
        return True
    try:
        path.relative_to(corpus)
        return True
    except ValueError:
        return False


def write_observation_files(
    skeleton: dict, dest: Any, *, force_corpus: bool = False
) -> list[Any]:
    """Write sources + observations YAML. ``dest`` is a directory or a file.

    A directory gets the two-file layout a PR actually needs. A file (or
    ``-``) gets the combined document. Destinations under the published
    corpus ``data/`` are refused unless ``force_corpus`` is true. Every
    written file carries the CANDIDATE header.
    """
    yaml_text = render_observation_yaml(skeleton)
    if dest in (None, "-", Path("-")):
        return []
    if is_published_corpus_path(dest) and not force_corpus:
        raise IngestError(
            "refusing to write under data/ (the published corpus that "
            "xycalc.build compiles). Default ingest writes nothing. Pass "
            "--emit-observation a path outside data/, or --force-corpus "
            "if you really mean to write candidate YAML into the published "
            "tree."
        )
    path = Path(dest)
    written = []
    # A .yaml file is the combined document; anything else is the two-file
    # layout a PR actually needs (sources/ + observations/).
    if path.suffix.lower() in {".yaml", ".yml"}:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml_text, encoding="utf-8")
        return [path]
    path.mkdir(parents=True, exist_ok=True)
    tag = skeleton["tag"]
    src = path / "sources" / f"{tag}.yaml"
    obs = path / "observations" / f"{tag}.yaml"
    src.parent.mkdir(parents=True, exist_ok=True)
    obs.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(
        _OBS_HEADER
        + yaml.safe_dump({"sources": skeleton["sources"]}, sort_keys=False),
        encoding="utf-8",
    )
    obs.write_text(
        _OBS_HEADER
        + yaml.safe_dump(
            {"observations": skeleton["observations"]}, sort_keys=False
        ),
        encoding="utf-8",
    )
    written.extend([src, obs])
    return written


def format_extraction(ext: Extraction) -> str:
    """Human report: what was taken, what was ignored, candidate disclaimer."""
    lines = [
        "CANDIDATE MEASUREMENT — not cited, not validated.",
        "The sizing below (if any) is the model run on extracted inputs.",
        "It does not add this paste to the corpus and it is not a validation case.",
        "",
        "READ",
    ]
    if not ext.read:
        lines.append("  (nothing mapped)")
    for f in ext.read:
        value = f.value
        if f.unit == "bytes" and isinstance(value, (int, float)):
            shown = f"{value:g} B ({format_quantity(value, 'bytes')})"
        else:
            shown = str(value)
        lines.append(f"  {f.path}")
        lines.append(f"      {shown}")
        lines.append(f"      → {f.used_as}")
    lines.append("")
    lines.append("IGNORED (present in the paste, not mapped to a model input or observation)")
    if not ext.ignored:
        lines.append("  (none at the walked layer)")
    else:
        for path in ext.ignored:
            lines.append(f"  {path}")
    if ext.warnings:
        lines.append("")
        lines.append("WARNINGS")
        for w in ext.warnings:
            lines.append(f"  · {w}")
    if ext.model_inputs:
        lines.append("")
        lines.append("MODEL INPUTS for mongodb.wt-cache")
        for key, value in ext.model_inputs.items():
            lines.append(
                f"  {key:<16} {format_quantity(value, 'bytes')}  ({value:g} B)"
            )
    if ext.version:
        lines.append("")
        lines.append(f"APPLIES TO (from paste): MongoDB {ext.version}")
    else:
        lines.append("")
        lines.append("APPLIES TO: TODO — no version in the paste (serverStatus.version)")
    return "\n".join(lines)
