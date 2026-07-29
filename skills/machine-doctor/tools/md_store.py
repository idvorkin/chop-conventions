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


@dataclass(frozen=True)
class CommRank:
    comm: str
    cpu_sum: float  # sum of interval cpu_pct — proportional to CPU-seconds at a fixed interval
    peak_rss_kb: int
    samples: int
    first_ts: int
    last_ts: int


def report(
    conn: sqlite3.Connection,
    since_ts: int,
    top: int = 10,
    exclude: frozenset[str] = frozenset(),
) -> list[CommRank]:
    """Grouped by comm, not pid: build-style workloads split their load across
    hundreds of short-lived pids that no per-pid ranking would surface."""
    rows = conn.execute(
        """SELECT comm, COALESCE(SUM(cpu_pct), 0), COALESCE(MAX(rss_kb), 0),
                  COUNT(*), MIN(ts), MAX(ts)
           FROM proc_sample WHERE ts >= ?
           GROUP BY comm
           ORDER BY COALESCE(SUM(cpu_pct), 0) DESC, COALESCE(MAX(rss_kb), 0) DESC""",
        (since_ts,),
    ).fetchall()
    ranked = [CommRank(*r) for r in rows if r[0] not in exclude]
    return ranked[:top]


def sample_count(conn: sqlite3.Connection, since_ts: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM sample WHERE ts >= ?", (since_ts,)).fetchone()[0]


def spike_count(conn: sqlite3.Connection, since_ts: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM sample WHERE ts >= ? AND is_spike = 1", (since_ts,)
    ).fetchone()[0]


@dataclass(frozen=True)
class SampleRow:
    ts: int
    load1: float
    idle_pct: int | None
    mem_avail_kb: int
    swap_used_kb: int
    swap_out: int
    nproc: int
    is_spike: bool


def nearest_sample(
    conn: sqlite3.Connection, ts: int, tolerance_s: int = 60
) -> SampleRow | None:
    """Nearest sample within tolerance, else None. The record-time interval is
    not stored, so the tolerance is fixed rather than interval-derived."""
    row = conn.execute(
        "SELECT ts, load1, idle_pct, mem_avail_kb, swap_used_kb, swap_out, nproc, is_spike "
        "FROM sample ORDER BY ABS(ts - ?) LIMIT 1",
        (ts,),
    ).fetchone()
    if row is None or abs(row[0] - ts) > tolerance_s:
        return None
    return SampleRow(*row[:7], bool(row[7]))


def procs_at(conn: sqlite3.Connection, ts: int) -> list[ProcSample]:
    rows = conn.execute(
        "SELECT pid, ppid, comm, cpu_pct, rss_kb, etime_s FROM proc_sample "
        "WHERE ts = ? ORDER BY COALESCE(cpu_pct, -1) DESC, rss_kb DESC",
        (ts,),
    ).fetchall()
    return [
        ProcSample(pid=r[0], ppid=r[1], comm=r[2], cpu_pct=r[3], rss_kb=r[4], etime_s=r[5])
        for r in rows
    ]
