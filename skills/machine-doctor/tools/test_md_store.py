"""Unit tests for md_store persistence. In-memory SQLite + tmp dirs only."""

import tempfile
import unittest
from pathlib import Path

from md_probe import ProcSample
from md_store import (
    connect,
    dump_path_for,
    insert_sample,
    prune,
    prune_spike_dumps,
    write_spike_dump,
)


def _proc(pid, cpu=0.0, rss=0, comm="x"):
    return ProcSample(pid=pid, ppid=1, comm=comm, cpu_pct=cpu, rss_kb=rss, etime_s=1)


def _insert(conn, ts, procs=(), is_spike=False, **kw):
    defaults = dict(load1=1.0, idle_pct=90, mem_avail_kb=1000, swap_used_kb=0,
                    swap_out=0, nproc=100)
    defaults.update(kw)
    insert_sample(conn, ts, is_spike=is_spike, procs=list(procs), **defaults)


class TestSchemaAndInsert(unittest.TestCase):
    def test_insert_and_read_back(self):
        conn = connect(":memory:")
        _insert(conn, 1000, procs=[_proc(5, cpu=50.0, comm="go")])
        row = conn.execute("SELECT ts, load1, is_spike FROM sample").fetchone()
        self.assertEqual((row[0], row[2]), (1000, 0))
        prow = conn.execute("SELECT ts, pid, comm FROM proc_sample").fetchone()
        self.assertEqual(prow, (1000, 5, "go"))

    def test_ts_collision_fails_loudly(self):
        """A clock stepping backwards must not silently overwrite history."""
        import sqlite3
        conn = connect(":memory:")
        _insert(conn, 1000)
        with self.assertRaises(sqlite3.IntegrityError):
            _insert(conn, 1000)


class TestPrune(unittest.TestCase):
    def test_drops_old_keeps_new(self):
        conn = connect(":memory:")
        old = 1000
        new = old + 8 * 86400
        _insert(conn, old, procs=[_proc(1)])
        _insert(conn, new, procs=[_proc(2)])
        dropped = prune(conn, now_ts=new, keep_days=7)
        self.assertEqual(dropped, 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM proc_sample").fetchone()[0], 1)


class TestSpikeDumps(unittest.TestCase):
    def test_write_and_locate(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_spike_dump(Path(d), 1753798598, "tree text")
            self.assertTrue(p.exists())
            self.assertEqual(p, dump_path_for(Path(d), 1753798598))
            self.assertEqual(p.read_text(), "tree text")

    def test_prune_keeps_newest_n(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(6):
                write_spike_dump(Path(d), 1753798598 + i * 60, f"dump {i}")
            removed = prune_spike_dumps(Path(d), keep=3)
            self.assertEqual(removed, 3)
            left = sorted((Path(d) / "spikes").glob("*.txt"))
            self.assertEqual(len(left), 3)
            self.assertIn("dump 5", left[-1].read_text())


from md_store import (
    nearest_sample,
    procs_at,
    report,
    sample_count,
    spike_count,
)


class TestReport(unittest.TestCase):
    def _seed(self):
        conn = connect(":memory:")
        # Build-style: many short-lived cc1 pids, each modest — must aggregate.
        _insert(conn, 1000, procs=[_proc(11, cpu=80.0, comm="cc1"), _proc(30, cpu=90.0, comm="agent")])
        _insert(conn, 1030, procs=[_proc(12, cpu=80.0, comm="cc1"), _proc(30, cpu=10.0, comm="agent")])
        _insert(conn, 1060, procs=[_proc(13, cpu=80.0, comm="cc1", rss=500000)])
        return conn

    def test_groups_by_comm_and_ranks_by_cpu_sum(self):
        got = report(self._seed(), since_ts=0)
        self.assertEqual(got[0].comm, "cc1")
        self.assertEqual(got[0].cpu_sum, 240.0)
        self.assertEqual(got[0].samples, 3)
        self.assertEqual(got[0].peak_rss_kb, 500000)
        self.assertEqual(got[1].comm, "agent")

    def test_since_filters(self):
        got = report(self._seed(), since_ts=1050)
        self.assertEqual([r.comm for r in got], ["cc1"])

    def test_exclude_drops_sampler(self):
        got = report(self._seed(), since_ts=0, exclude=frozenset({"cc1"}))
        self.assertEqual([r.comm for r in got], ["agent"])

    def test_counts(self):
        conn = self._seed()
        self.assertEqual(sample_count(conn, 0), 3)
        self.assertEqual(spike_count(conn, 0), 0)


class TestNearestSample(unittest.TestCase):
    def test_within_tolerance(self):
        conn = connect(":memory:")
        _insert(conn, 1000, is_spike=True)
        row = nearest_sample(conn, 1030, tolerance_s=60)
        self.assertEqual(row.ts, 1000)
        self.assertTrue(row.is_spike)

    def test_outside_tolerance_is_none(self):
        conn = connect(":memory:")
        _insert(conn, 1000)
        self.assertIsNone(nearest_sample(conn, 2000, tolerance_s=60))

    def test_empty_db_is_none(self):
        self.assertIsNone(nearest_sample(connect(":memory:"), 1000))

    def test_procs_at_returns_that_instant(self):
        conn = connect(":memory:")
        _insert(conn, 1000, procs=[_proc(5, cpu=42.0, comm="dolt")])
        got = procs_at(conn, 1000)
        self.assertEqual([(p.pid, p.comm) for p in got], [(5, "dolt")])


if __name__ == "__main__":
    unittest.main()
