#!/usr/bin/env python3
"""Unit tests for _impl.py pure functions.

Run with: python3 -m unittest test_impl.py
"""

import json
import sys
import tempfile
import unittest
from unittest import mock
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

# Make sibling _impl.py importable
sys.path.insert(0, str(Path(__file__).parent))

from _impl import (  # noqa: E402
    PR_CREATE_RE,
    PRICING,
    TITLE_RE,
    URL_RE,
    aggregate,
    build_report,
    cost_breakdown,
    empty_stats,
    empty_unknown,
    fetch_pr_titles,
    fmt_duration,
    fmt_pricing_summary,
    fmt_unknown_detail,
    humanize_project,
    ingest,
    money,
    normalize_model,
    parent_key,
    parse_args,
    parse_ts,
    pct_or_na,
    positive_int,
    record_unknown,
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

    def test_accepts_current_gen_models(self):
        # Current-gen API ids are bare aliases (no date suffix) — they must
        # match PRICING directly or the dominant spend lands in unknown_models.
        self.assertEqual(normalize_model("claude-fable-5"), "claude-fable-5")
        self.assertEqual(normalize_model("claude-opus-4-8"), "claude-opus-4-8")
        self.assertEqual(normalize_model("claude-opus-4-7"), "claude-opus-4-7")
        self.assertEqual(normalize_model("claude-sonnet-5"), "claude-sonnet-5")

    def test_legacy_haiku_id_resolves(self):
        # The real legacy id claude-3-5-haiku-20241022 normalizes to
        # claude-3-5-haiku. The old PRICING key "claude-haiku-3-5" was dead
        # config — it never matched any real API id.
        self.assertEqual(
            normalize_model("claude-3-5-haiku-20241022"), "claude-3-5-haiku"
        )
        self.assertIn("claude-3-5-haiku", PRICING)
        self.assertNotIn("claude-haiku-3-5", PRICING)


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


class TestCurrentGenPricing(unittest.TestCase):
    """New PRICING rows bill at the published list rates."""

    def test_fable_5_rates(self):
        # $10/$50 per MTok (claude-api skill pricing reference)
        s = empty_stats()
        s["models"]["claude-fable-5"]["inp"] = 1_000_000
        s["models"]["claude-fable-5"]["out"] = 1_000_000
        total, comps, by_model, _naive = cost_breakdown(s)
        self.assertAlmostEqual(comps["inp"], 10.00)
        self.assertAlmostEqual(comps["out"], 50.00)
        self.assertAlmostEqual(by_model["claude-fable-5"], 60.00)
        self.assertAlmostEqual(total, 60.00)

    def test_opus_4_8_and_4_7_rates(self):
        # Both $5/$25 per MTok, same as Opus 4.6
        for m in ("claude-opus-4-8", "claude-opus-4-7"):
            s = empty_stats()
            s["models"][m]["inp"] = 1_000_000
            s["models"][m]["out"] = 1_000_000
            total, _comps, _by_model, _naive = cost_breakdown(s)
            self.assertAlmostEqual(total, 30.00, msg=m)

    def test_sonnet_5_rates(self):
        # $3/$15 sticker list rate (intro $2/$10 through 2026-08-31 NOT applied)
        s = empty_stats()
        s["models"]["claude-sonnet-5"]["inp"] = 1_000_000
        s["models"]["claude-sonnet-5"]["out"] = 1_000_000
        total, _comps, _by_model, _naive = cost_breakdown(s)
        self.assertAlmostEqual(total, 18.00)

    def test_legacy_haiku_row_prices(self):
        # The re-keyed claude-3-5-haiku row is live: $0.80/$4.00 per MTok
        s = empty_stats()
        s["models"]["claude-3-5-haiku"]["inp"] = 1_000_000
        s["models"]["claude-3-5-haiku"]["out"] = 1_000_000
        total, _comps, _by_model, _naive = cost_breakdown(s)
        self.assertAlmostEqual(total, 4.80)

    def test_cache_multipliers_hold_for_all_models(self):
        # Cache rates derive from input rate: 1h write 2x, 5m write 1.25x,
        # read 0.1x. Enforced for every row so a future edit can't silently
        # break the derivation on one model.
        for m, p in PRICING.items():
            self.assertAlmostEqual(p["c1h"], p["inp"] * 2.0, msg=m)
            self.assertAlmostEqual(p["c5m"], p["inp"] * 1.25, msg=m)
            self.assertAlmostEqual(p["cread"], p["inp"] * 0.1, msg=m)


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


class TestFetchPrTitles(unittest.TestCase):
    def test_returns_titles_and_states(self):
        def fake_run(args, capture_output, text, timeout):
            self.assertEqual(args[:3], ["gh", "pr", "view"])
            self.assertEqual(capture_output, True)
            self.assertEqual(text, True)
            self.assertEqual(timeout, 15)
            payload = {
                "1": {"title": "One", "state": "OPEN"},
                "2": {"title": "Two", "state": "MERGED"},
            }[args[3]]
            return mock.Mock(returncode=0, stdout=json.dumps(payload))

        with mock.patch("_impl.subprocess.run", side_effect=fake_run):
            got = fetch_pr_titles({("o", "r", 2), ("o", "r", 1)})

        self.assertEqual(got[("o", "r", 1)], ("One", "OPEN"))
        self.assertEqual(got[("o", "r", 2)], ("Two", "MERGED"))

    def test_falls_back_to_none_on_error(self):
        def fake_run(args, capture_output, text, timeout):
            if args[3] == "1":
                raise RuntimeError("boom")
            return mock.Mock(returncode=1, stdout="")

        with mock.patch("_impl.subprocess.run", side_effect=fake_run):
            got = fetch_pr_titles({("o", "r", 1), ("o", "r", 2)})

        self.assertEqual(got[("o", "r", 1)], (None, None))
        self.assertEqual(got[("o", "r", 2)], (None, None))


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
        unknown = empty_unknown()
        unknown.update(turns=3, inp=1_000, out=500)
        bucket_meta = dict(agg, unknown_models={"claude-opus-5-0": unknown})
        today = date(2026, 4, 13)
        start = date(2026, 4, 7)
        report = build_report([], bucket_meta, 7, start, today, {})
        self.assertIn("claude-opus-5-0", report)
        self.assertIn("3 turn(s)", report)
        self.assertIn("1,000 input", report)


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
            "| Day | Input $ | Output $ | 1h cache write $ | 5m cache write $ | Cache read $ |",
            report,
        )
        self.assertIn(
            "| **Total** | **$5.00** | **$25.00** | **$0.00** | **$0.00** | **$0.50** |",
            report,
        )
        self.assertIn(
            "| Day | Dur | Session | Turns (main/sub) | Input $ | Output $ | 1h cache write $ | 5m cache write $ | Cache read $ | Actual $ | PRs shipped |",
            report,
        )
        self.assertIn(
            "| 2026-04-13 | 30m | abc12345 | 5/0 | $5.00 | $25.00 | $0.00 | $0.00 | $0.50 | $30.50 | — |",
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

            # Bucket populated with no usage, but unknown model tracked with
            # full token volumes, not just a turn count.
            self.assertEqual(list(unknown), ["claude-opus-5-0"])
            u = unknown["claude-opus-5-0"]
            self.assertEqual(u["turns"], 1)
            self.assertEqual(u["inp"], 100)
            self.assertEqual(u["out"], 50)

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


class TestRecordUnknown(unittest.TestCase):
    def test_accumulates_all_token_classes(self):
        unknown = {}
        record_unknown(
            unknown,
            "claude-fable-6",
            dict(
                input_tokens=10,
                output_tokens=20,
                cache_read_input_tokens=30,
                cache_creation=dict(
                    ephemeral_1h_input_tokens=40, ephemeral_5m_input_tokens=50
                ),
            ),
        )
        record_unknown(unknown, "claude-fable-6", dict(input_tokens=1))
        u = unknown["claude-fable-6"]
        self.assertEqual(u["turns"], 2)
        self.assertEqual(u["inp"], 11)
        self.assertEqual(u["out"], 20)
        self.assertEqual(u["cread"], 30)
        self.assertEqual(u["c1h"], 40)
        self.assertEqual(u["c5m"], 50)

    def test_fmt_unknown_detail(self):
        u = empty_unknown()
        u.update(turns=2, inp=1_000_000, out=50_000, cread=100, c1h=200, c5m=300)
        detail = fmt_unknown_detail(u)
        self.assertIn("2 turn(s)", detail)
        self.assertIn("1,000,000 input", detail)
        self.assertIn("50,000 output", detail)
        self.assertIn("600 cache tokens", detail)  # cread + c1h + c5m


class TestUnknownModelFootnoteQuantified(unittest.TestCase):
    """The unpriced-models footnote must state the exclusion in TOKENS,
    not just turn counts, so the reader can size the hole in the totals."""

    def _report_with_unknown(self, unknown_models):
        bucket = defaultdict(empty_stats)
        key = ("-home-developer-gits-example", "abc12345", date(2026, 4, 13))
        s = bucket[key]
        s["models"]["claude-opus-4-6"]["inp"] = 1_000_000
        s["models"]["claude-opus-4-6"]["turns"] = 1
        s["main_turns"] = 1
        s["first"] = datetime(2026, 4, 13, 10, 0, 0)
        s["last"] = datetime(2026, 4, 13, 10, 5, 0)
        agg = aggregate(bucket)
        bucket_meta = dict(agg, unknown_models=unknown_models)
        return build_report(
            agg["entries"], bucket_meta, 1, date(2026, 4, 13), date(2026, 4, 13), {}
        )

    def test_footnote_states_token_volumes(self):
        unknown = empty_unknown()
        unknown.update(turns=7, inp=2_500_000, out=800_000, cread=4_000_000)
        report = self._report_with_unknown({"claude-fable-6": unknown})
        self.assertIn("Unpriced models", report)
        self.assertIn("claude-fable-6", report)
        self.assertIn("7 turn(s)", report)
        self.assertIn("2,500,000 input", report)
        self.assertIn("800,000 output", report)
        self.assertIn("4,000,000 cache tokens", report)
        # Grand total across all token classes: 2.5M + 0.8M + 4M = 7.3M
        self.assertIn("7,300,000 tokens", report)
        self.assertIn("EXCLUDED", report)

    def test_no_footnote_when_no_unknowns(self):
        report = self._report_with_unknown({})
        self.assertNotIn("Unpriced models", report)


class TestPricingFootnoteDerived(unittest.TestCase):
    """The pricing footnote must render from PRICING, not hardcoded prose."""

    def test_summary_lists_every_model_and_rate(self):
        summary = fmt_pricing_summary()
        for m, p in PRICING.items():
            self.assertIn(m, summary)
        self.assertIn("claude-fable-5 $10/$50", summary)
        self.assertIn("claude-opus-4-8 $5/$25", summary)
        self.assertIn("claude-sonnet-5 $3/$15", summary)
        self.assertIn("claude-3-5-haiku $0.8/$4", summary)

    def test_footnote_follows_pricing_mutation(self):
        """Mutate a rate in PRICING and the rendered footnote must follow —
        proving the prose is derived, not a string literal."""
        bucket = defaultdict(empty_stats)
        key = ("-home-developer-gits-example", "abc12345", date(2026, 4, 13))
        s = bucket[key]
        s["models"]["claude-opus-4-6"]["inp"] = 1_000_000
        s["main_turns"] = 1
        s["first"] = datetime(2026, 4, 13, 10, 0, 0)
        s["last"] = datetime(2026, 4, 13, 10, 5, 0)
        agg = aggregate(bucket)
        bucket_meta = dict(agg, unknown_models={})

        mutated = dict(PRICING["claude-fable-5"], inp=99.00, out=999.00)
        with mock.patch.dict(PRICING, {"claude-fable-5": mutated}):
            report = build_report(
                agg["entries"],
                bucket_meta,
                1,
                date(2026, 4, 13),
                date(2026, 4, 13),
                {},
            )
        self.assertIn("claude-fable-5 $99/$999", report)
        self.assertNotIn("claude-fable-5 $10/$50", report)


if __name__ == "__main__":
    unittest.main()
