"""Opening the corpus, and building it on demand.

The database is a build artifact. Anything that needs it should call connect()
rather than assume a build already happened — a fresh clone has YAML and no
database, and making the first command the user runs fail on that is a poor
introduction to a project whose whole claim is that the data is trustworthy.

connect() also rebuilds when the on-disk schema stamp does not match
schema.sql. A missing file is the easy case; a file built against an older
schema is the one that used to 500 the GUI with `no such column`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .build import DEFAULT_DB, SCHEMA_HASH_KEY, build, schema_hash


def _schema_current(path: Path) -> bool:
    if not path.exists():
        return False
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (SCHEMA_HASH_KEY,)
        ).fetchone()
        return bool(row) and row[0] == schema_hash()
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def connect(db_path: Path | None = None, autobuild: bool = True) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB
    if autobuild and not _schema_current(path):
        build(path)
    elif not path.exists():
        raise FileNotFoundError(f"no corpus at {path}; run `xycalc build`")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
