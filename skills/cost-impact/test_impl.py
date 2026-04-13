#!/usr/bin/env python3
"""Unit tests for _impl.py pure functions.

Run with: python3 -m unittest test_impl.py
"""

import json
import sys
import tempfile
import unittest
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

# Make sibling _impl.py importable
sys.path.insert(0, str(Path(__file__).parent))

from _impl import (  # noqa: E402
    PR_CREATE_RE,
    TITLE_RE,
    URL_RE,
    aggregate,
    build_report,
    cost_breakdown,
    empty_stats,
    fmt_duration,
    humanize_project,
    ingest,
    money,
    normalize_model,
    parent_key,
    parse_args,
    parse_ts,
    pct_or_na,
    positive_int,
)


class TestNormalizeModel(unittest.TestCase):
    def test_strips_date_suffix(self):
        self.assertEqual(
            normalize_model("claude-haiku-4-5-20251001"), "claude-haiku-4-5"
        )
        self.assertEqual(normalize_model("claude-opus-4-6-20260101"), "claude-opus-4-6")

    def test_returns_none_for_synthetic(self):
        self.assertIsNone(normalize_model("<synthetic>"))

    def test_returns_none_for_empty(self):
        self.assertIsNone(normalize_model(""))
        self.assertIsNone(normalize_model(None))

    def test_returns_none_for_unknown_model(self):
        # Unknown models must be dropped so the caller can warn.
        self.assertIsNone(normalize_model("claude-opus-5-0"))
        self.assertIsNone(normalize_model("claude-martian-9-9"))

    def test_accepts_known_bare_model(self):
        self.assertEqual(normalize_model("claude-opus-4-6"), "claude-opus-4-6")
        self.assertEqual(normalize_model("claude-sonnet-4-6"), "claude-sonnet-4-6")


class TestPctOrNa(unittest.TestCase):
    def test_zero_denom_returns_na(self):
        # Empty window: tot_naive == 0 must not crash.
        self.assertEqual(pct_or_na(0, 0), "n/a")
        self.assertEqual(pct_or_na(100, 0), "n/a")

    def test_positive_denom_returns_pct(self):
        self.assertEqual(pct_or_na(50, 100), "50%")
        self.assertEqual(pct_or_na(82, 100), "82%")
        self.assertEqual(pct_or_na(0, 100), "0%")


class TestMoney(unittest.TestCase):
    def test_basic_conversion(self):
        # 1M tokens at $5/Mtok = $5.00
        self.assertAlmostEqual(money(1_000_000, 5.00), 5.00)
        # 500k tokens at $25/Mtok = $12.50
        self.assertAlmostEqual(money(500_000, 25.00), 12.50)

    def test_zero_tokens(self):
        self.assertEqual(money(0, 25.00), 0.0)


class TestCostBreakdown(unittest.TestCase):
    def test_empty_models(self):
        s = empty_stats()
        total, comps, by_model, naive = cost_breakdown(s)
        self.assertEqual(total, 0.0)
        self.assertEqual(naive, 0.0)
        self.assertEqual(by_model, {})
        self.assertEqual(comps["inp"], 0.0)

    def test_naive_vs_actual(self):
        """With cache_read present, naive should exceed actual."""
        s = empty_stats()
        # 1M input, 1M output, 1M cache_read on Opus 4.6
        s["models"]["claude-opus-4-6"]["inp"] = 1_000_000
        s["models"]["claude-opus-4-6"]["out"] = 1_000_000
        s["models"]["claude-opus-4-6"]["cread"] = 1_000_000
        total, _comps, _by_model, naive = cost_breakdown(s)
        # Actual: 1M * $5 (inp) + 1M * $25 (out) + 1M * $0.50 (cread) = $30.50
        self.assertAlmostEqual(total, 30.50)
        # Naive: (1M input + 1M cread) * $5 + 1M * $25 = $10 + $25 = $35.00
        self.assertAlmostEqual(naive, 35.00)
        self.assertGreater(naive, total)  # cache must save money

    def test_multi_model_sums(self):
        s = empty_stats()
        s["models"]["claude-opus-4-6"]["inp"] = 1_000_000  # $5
        s["models"]["claude-sonnet-4-6"]["inp"] = 1_000_000  # $3
        total, _, by_model, _ = cost_breakdown(s)
        self.assertAlmostEqual(by_model["claude-opus-4-6"], 5.00)
        self.assertAlmostEqual(by_model["claude-sonnet-4-6"], 3.00)
        self.assertAlmostEqual(total, 8.00)


class TestHumanizeProject(unittest.TestCase):
    def test_strips_prefix_and_dashes(self):
        self.assertEqual(
            humanize_project("-home-developer-gits-chop-conventions"),
            "chop-conventions",
        )
        self.assertEqual(humanize_project("-home-developer-tmp"), "tmp")

    def test_leaves_plain_name(self):
        self.assertEqual(humanize_project("plain-project"), "plain-project")


class TestFmtDuration(unittest.TestCase):
    def test_sub_hour(self):
        self.assertEqual(fmt_duration(0), "0m")
        self.assertEqual(fmt_duration(5), "5m")
        self.assertEqual(fmt_duration(59), "59m")

    def test_hours_and_minutes(self):
        self.assertEqual(fmt_duration(60), "1h 00m")
        self.assertEqual(fmt_duration(125), "2h 05m")
        self.assertEqual(fmt_duration(60 * 5 + 30), "5h 30m")


class TestParseTs(unittest.TestCase):
    def test_z_suffix(self):
        ts = parse_ts("2026-04-13T10:00:00.123Z")
        self.assertIsNotNone(ts)
        self.assertEqual(ts.tzinfo, timezone.utc)

    def test_offset_suffix(self):
        ts = parse_ts("2026-04-13T10:00:00+00:00")
        self.assertIsNotNone(ts)

    def test_none_and_garbage(self):
        self.assertIsNone(parse_ts(None))
        self.assertIsNone(parse_ts(""))
        self.assertIsNone(parse_ts("not a date"))


class TestParentKey(unittest.TestCase):
    def test_main_session(self):
        root = Path("/root")
        f = Path("/root/proj/uuid.jsonl")
        self.assertEqual(parent_key(f, root), ("proj", "uuid", False))

    def test_subagent_session(self):
        root = Path("/root")
        f = Path("/root/proj/uuid/subagents/agent-foo.jsonl")
        self.assertEqual(parent_key(f, root), ("proj", "uuid", True))


class TestPositiveInt(unittest.TestCase):
    def test_accepts_positive(self):
        self.assertEqual(positive_int("1"), 1)
        self.assertEqual(positive_int("7"), 7)
        self.assertEqual(positive_int("365"), 365)

    def test_rejects_zero(self):
        import argparse as _ap

        with self.assertRaises(_ap.ArgumentTypeError):
            positive_int("0")

    def test_rejects_negative(self):
        import argparse as _ap

        with self.assertRaises(_ap.ArgumentTypeError):
            positive_int("-1")

    def test_rejects_non_integer(self):
        import argparse as _ap

        with self.assertRaises(_ap.ArgumentTypeError):
            positive_int("abc")


class TestParseArgs(unittest.TestCase):
    def test_default(self):
        args = parse_args([])
        self.assertEqual(args.days, 7)

    def test_explicit(self):
        args = parse_args(["14"])
        self.assertEqual(args.days, 14)

    def test_rejects_zero(self):
        with self.assertRaises(SystemExit):
            parse_args(["0"])


class TestRegexes(unittest.TestCase):
    def test_pr_create_bare(self):
        self.assertIsNotNone(PR_CREATE_RE.search("gh pr create --title 'foo'"))

    def test_pr_create_compound(self):
        self.assertIsNotNone(
            PR_CREATE_RE.search("cd ~/gits/x && gh pr create --title 'foo'")
        )
        self.assertIsNotNone(PR_CREATE_RE.search("git push && gh pr create"))

    def test_pr_create_no_false_match(self):
        self.assertIsNone(PR_CREATE_RE.search("ghx pr create"))
        self.assertIsNone(PR_CREATE_RE.search("git show gh-pages"))

    def test_title_extract(self):
        m = TITLE_RE.search('gh pr create --title "feat: add X" --body "y"')
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "feat: add X")

    def test_url_extract(self):
        m = URL_RE.search("see https://github.com/owner/repo/pull/42 for details")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "owner")
        self.assertEqual(m.group(2), "repo")
        self.assertEqual(m.group(3), "42")


class TestAggregateEmpty(unittest.TestCase):
    """Empty bucket must yield empty aggregation without crashing."""

    def test_empty_bucket(self):
        bucket = defaultdict(empty_stats)
        agg = aggregate(bucket)
        self.assertEqual(agg["entries"], [])
        self.assertEqual(agg["tot_actual"], 0.0)
        self.assertEqual(agg["tot_naive"], 0.0)
        self.assertEqual(agg["tot_main_turns"], 0)
        self.assertEqual(agg["tot_sub_turns"], 0)
        self.assertEqual(list(agg["by_repo"]), [])

    def test_zero_total_entries_excluded(self):
        """Sessions with zero cost must not appear in entries."""
        bucket = defaultdict(empty_stats)
        # Populate a session that has a `first`/`last` but no model usage
        bucket[("proj", "uuid", date(2026, 4, 13))]["first"] = datetime(
            2026, 4, 13, 10, 0, 0
        )
        bucket[("proj", "uuid", date(2026, 4, 13))]["last"] = datetime(
            2026, 4, 13, 10, 5, 0
        )
        agg = aggregate(bucket)
        self.assertEqual(agg["entries"], [])


class TestBuildReportEmptyWindow(unittest.TestCase):
    """Empty window must produce a minimal report, NOT crash with ZeroDivisionError."""

    def test_no_zero_division(self):
        agg = aggregate(defaultdict(empty_stats))
        bucket_meta = dict(agg, unknown_models={})
        today = date(2026, 4, 13)
        start = date(2026, 4, 7)
        # Previously crashed with ZeroDivisionError on the cache-savings line
        report = build_report([], bucket_meta, 7, start, today, {})
        self.assertIn("No billable turns", report)
        self.assertIn(f"{start} → {today}", report)

    def test_empty_window_surfaces_unknown_models(self):
        agg = aggregate(defaultdict(empty_stats))
        bucket_meta = dict(agg, unknown_models={"claude-opus-5-0": 3})
        today = date(2026, 4, 13)
        start = date(2026, 4, 7)
        report = build_report([], bucket_meta, 7, start, today, {})
        self.assertIn("claude-opus-5-0", report)
        self.assertIn("3", report)


class TestBuildReportWithData(unittest.TestCase):
    """End-to-end: populate bucket, aggregate, render report."""

    def test_single_session_day(self):
        bucket = defaultdict(empty_stats)
        key = ("-home-developer-gits-example", "abc12345", date(2026, 4, 13))
        s = bucket[key]
        s["models"]["claude-opus-4-6"]["inp"] = 1_000_000
        s["models"]["claude-opus-4-6"]["out"] = 1_000_000
        s["models"]["claude-opus-4-6"]["cread"] = 1_000_000
        s["models"]["claude-opus-4-6"]["turns"] = 5
        s["main_turns"] = 5
        s["first"] = datetime(2026, 4, 13, 10, 0, 0)
        s["last"] = datetime(2026, 4, 13, 10, 30, 0)

        agg = aggregate(bucket)
        self.assertEqual(len(agg["entries"]), 1)
        self.assertAlmostEqual(agg["tot_actual"], 30.50)
        self.assertAlmostEqual(agg["tot_naive"], 35.00)

        bucket_meta = dict(agg, unknown_models={})
        report = build_report(
            agg["entries"], bucket_meta, 1, date(2026, 4, 13), date(2026, 4, 13), {}
        )
        # Non-crashing rendering
        self.assertIn("$30.50", report)
        self.assertIn("example", report)  # humanized project name
        self.assertIn(
            "| Day | Actual $ | Sessions | Main turns | Sub turns | No-cache ref $ |",
            report,
        )
        self.assertIn(
            "| **Total** | **$30.50** | **1** | **5** | **0** | **$35.00** |",
            report,
        )
        self.assertIn("### Daily cost details", report)
        self.assertIn(
            "| Day | Fresh input $ | Output $ | 1h cache write $ | 5m cache write $ | Cache read $ |",
            report,
        )
        self.assertIn(
            "| **Total** | **$5.00** | **$25.00** | **$0.00** | **$0.00** | **$0.50** |",
            report,
        )
        # Cache savings: (35 - 30.50) / 35 = 12.857% -> "13%"
        self.assertIn("13%", report)
        # TTL footnote: zero c5m tokens -> "not hit by the bug"
        self.assertIn("not hit by the bug", report)

    def test_ttl_footnote_when_c5m_present(self):
        bucket = defaultdict(empty_stats)
        key = ("-home-developer-gits-example", "abc", date(2026, 4, 13))
        s = bucket[key]
        s["models"]["claude-opus-4-6"]["inp"] = 1_000_000
        s["models"]["claude-opus-4-6"]["out"] = 1_000_000
        s["models"]["claude-opus-4-6"]["c5m"] = 500_000  # <-- TTL bug hit
        s["main_turns"] = 1
        s["first"] = datetime(2026, 4, 13, 10, 0, 0)
        s["last"] = datetime(2026, 4, 13, 10, 5, 0)

        agg = aggregate(bucket)
        bucket_meta = dict(agg, unknown_models={})
        report = build_report(
            agg["entries"], bucket_meta, 1, date(2026, 4, 13), date(2026, 4, 13), {}
        )
        # Must NOT say "not hit by the bug" because we DID see c5m
        self.assertNotIn("not hit by the bug", report)
        self.assertIn("ephemeral_5m_input_tokens", report)
        self.assertIn("500,000", report)


class TestIngestStreaming(unittest.TestCase):
    """Verify ingest() streams files without loading whole thing into memory.

    Indirectly tested: craft a jsonl with a mix of valid and invalid lines,
    verify that valid entries are bucketed and invalid ones are skipped."""

    def _write_jsonl(self, tmpdir, rel, lines):
        path = Path(tmpdir) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(ln) for ln in lines) + "\n")
        return path

    def test_basic_ingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            today = date(2026, 4, 13)
            start = date(2026, 4, 7)

            valid_line = dict(
                timestamp="2026-04-13T10:00:00.000Z",
                message=dict(
                    model="claude-opus-4-6",
                    usage=dict(input_tokens=1_000_000, output_tokens=500_000),
                ),
            )
            f = Path(tmp) / "proj" / "uuid.jsonl"
            f.parent.mkdir(parents=True, exist_ok=True)
            with f.open("w") as fh:
                fh.write(json.dumps(valid_line) + "\n")
                fh.write("not json\n")  # must be skipped
                fh.write(json.dumps(dict(timestamp=None)) + "\n")  # must be skipped

            bucket = defaultdict(empty_stats)
            unknown = {}
            ingest(f, False, root, start, today, bucket, unknown)

            self.assertEqual(len(bucket), 1)
            key = list(bucket.keys())[0]
            s = bucket[key]
            self.assertEqual(s["models"]["claude-opus-4-6"]["inp"], 1_000_000)
            self.assertEqual(s["main_turns"], 1)
            self.assertEqual(unknown, {})

    def test_unknown_model_tracked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            today = date(2026, 4, 13)
            start = date(2026, 4, 7)

            unknown_line = dict(
                timestamp="2026-04-13T10:00:00.000Z",
                message=dict(
                    model="claude-opus-5-0",  # fake future model
                    usage=dict(input_tokens=100, output_tokens=50),
                ),
            )
            f = Path(tmp) / "proj" / "uuid.jsonl"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(unknown_line) + "\n")

            bucket = defaultdict(empty_stats)
            unknown = {}
            ingest(f, False, root, start, today, bucket, unknown)

            # Bucket populated with no usage, but unknown model tracked
            self.assertEqual(unknown, {"claude-opus-5-0": 1})

    def test_filters_out_of_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            today = date(2026, 4, 13)
            start = date(2026, 4, 13)  # single-day window

            out_of_window = dict(
                timestamp="2026-03-01T10:00:00.000Z",  # way outside window
                message=dict(
                    model="claude-opus-4-6",
                    usage=dict(input_tokens=1_000_000, output_tokens=1_000_000),
                ),
            )
            f = Path(tmp) / "proj" / "uuid.jsonl"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(out_of_window) + "\n")

            bucket = defaultdict(empty_stats)
            unknown = {}
            ingest(f, False, root, start, today, bucket, unknown)

            # Bucket may be populated with an empty stats entry via defaultdict,
            # but no models should have been recorded.
            for s in bucket.values():
                self.assertEqual(sum(t["turns"] for t in s["models"].values()), 0)


if __name__ == "__main__":
    unittest.main()
