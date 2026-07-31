"""Opening the corpus, and building it on demand.

The database is a build artifact. Anything that needs it should call connect()
rather than assume a build already happened — a fresh clone has YAML and no
database, and making the first command the user runs fail on that is a poor
introduction to a project whose whole claim is that the data is trustworthy.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .build import DEFAULT_DB, build


def connect(db_path: Path | None = None, autobuild: bool = True) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB
    if not path.exists():
        if not autobuild:
            raise FileNotFoundError(f"no corpus at {path}; run `xycalc build`")
        build(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
