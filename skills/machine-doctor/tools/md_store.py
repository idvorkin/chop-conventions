"""Persistence for machine-doctor: SQLite sample history + spike-dump files.

Owns all durable state. Every function takes its path/connection explicitly so
tests run against :memory: and tmp dirs, never the real state directory.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from md_probe import ProcSample

DEFAULT_STATE_DIR = Path("~/.local/state/machine-doctor").expanduser()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sample (
    ts            INTEGER PRIMARY KEY,   -- unix seconds; PK so a clock step back fails loudly
    load1         REAL,
    idle_pct      INTEGER,
    mem_avail_kb  INTEGER,
    swap_used_kb  INTEGER,
    swap_out      INTEGER,               -- KB/s swapped out over the interval
    nproc         INTEGER,
    is_spike      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS proc_sample (
    ts        INTEGER NOT NULL,
    pid       INTEGER NOT NULL,
    ppid      INTEGER,
    comm      TEXT,
    cpu_pct   REAL,
    rss_kb    INTEGER,
    etime_s   INTEGER,
    PRIMARY KEY (ts, pid)
);
CREATE INDEX IF NOT EXISTS idx_proc_ts   ON proc_sample(ts);
CREATE INDEX IF NOT EXISTS idx_proc_comm ON proc_sample(comm);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def insert_sample(
    conn: sqlite3.Connection,
    ts: int,
    *,
    load1: float,
    idle_pct: int | None,
    mem_avail_kb: int,
    swap_used_kb: int,
    swap_out: int,
    nproc: int,
    is_spike: bool,
    procs: list[ProcSample],
) -> None:
    conn.execute(
        "INSERT INTO sample VALUES (?,?,?,?,?,?,?,?)",
        (ts, load1, idle_pct, mem_avail_kb, swap_used_kb, swap_out, nproc, int(is_spike)),
    )
    conn.executemany(
        "INSERT INTO proc_sample VALUES (?,?,?,?,?,?,?)",
        [(ts, p.pid, p.ppid, p.comm, p.cpu_pct, p.rss_kb, p.etime_s) for p in procs],
    )
    conn.commit()


def prune(conn: sqlite3.Connection, now_ts: int, keep_days: int = 7) -> int:
    cutoff = now_ts - keep_days * 86400
    dropped = conn.execute("DELETE FROM sample WHERE ts < ?", (cutoff,)).rowcount
    conn.execute("DELETE FROM proc_sample WHERE ts < ?", (cutoff,))
    conn.commit()
    return dropped


def dump_path_for(state_dir: Path, ts: int) -> Path:
    # Hyphens, not colons — colon filenames break on enough filesystems to matter.
    name = datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H-%M-%S") + ".txt"
    return Path(state_dir) / "spikes" / name


def write_spike_dump(state_dir: Path, ts: int, text: str) -> Path:
    path = dump_path_for(state_dir, ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def prune_spike_dumps(state_dir: Path, keep: int = 50) -> int:
    d = Path(state_dir) / "spikes"
    if not d.is_dir():
        return 0
    dumps = sorted(d.glob("*.txt"))  # ISO names sort chronologically
    excess = dumps[:-keep] if keep > 0 else dumps
    for f in excess:
        f.unlink()
    return len(excess)
