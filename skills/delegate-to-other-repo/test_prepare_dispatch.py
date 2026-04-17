#!/usr/bin/env python3
"""Unit tests for prepare_dispatch.py pure functions + dry-run orchestration.

Run with: python3 -m unittest test_prepare_dispatch

Typer is NOT imported anywhere in this file — the helper's CLI is wired up
in `_build_app()` which lives behind the `if __name__ == "__main__":`
guard. Tests exercise pure functions directly and drive the orchestrator
through a stubbed subprocess layer.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Ensure the sibling module is importable when invoked via
# `python3 -m unittest` from the skill directory. `unittest discover`
# already puts the start dir on sys.path; the insert below is for
# pytest + pyright parity without adding a conftest.py to the staging set.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import prepare_dispatch  # noqa: E402 — sibling import after sys.path shim above
from prepare_dispatch import (  # noqa: E402 — sibling import after sys.path shim above
    choose_base,
    choose_default_branch,
    find_newest_jsonl,
    parse_repo_slug,
    resolve_session_log,
    resolve_target_path,
    resolve_unique_slug,
    sanitize_slug,
    session_log_hash_of,
    timestamp_slug,
)


class TestSanitizeSlug(unittest.TestCase):
    def test_ascii_passthrough(self):
        self.assertEqual(sanitize_slug("fix-typo"), "fix-typo")

    def test_lowercasing(self):
        self.assertEqual(sanitize_slug("Fix-Typo"), "fix-typo")

    def test_non_alnum_collapsed(self):
        self.assertEqual(sanitize_slug("add  fancy_feature!!"), "add-fancy-feature")

    def test_leading_and_trailing_stripped(self):
        self.assertEqual(sanitize_slug("--leading--trailing--"), "leading-trailing")

    def test_length_cap_at_40_with_trailing_dash_stripped(self):
        # Build an input that, after sanitization, puts a `-` exactly at position 40
        # so truncation leaves a trailing dash to strip.
        raw = (
            "a" * 39 + " " + "b" * 10
        )  # -> "aaaa...a-bbbb...b", cut to 40 = "aaaa...a-"
        out = sanitize_slug(raw)
        self.assertIsNotNone(out)
        assert out is not None  # narrowing for type-checkers
        self.assertLessEqual(len(out), 40)
        self.assertFalse(out.endswith("-"))

    def test_empty_input_returns_none(self):
        self.assertIsNone(sanitize_slug(""))

    def test_pure_punctuation_returns_none(self):
        self.assertIsNone(sanitize_slug("!!!---???"))

    def test_non_ascii_returns_none(self):
        # Greek/Japanese characters don't survive step 2.
        self.assertIsNone(sanitize_slug("αβγ 日本語"))


class TestTimestampSlug(unittest.TestCase):
    def test_format(self):
        import datetime

        now = datetime.datetime(2026, 4, 17, 9, 30, 0)
        self.assertEqual(timestamp_slug(now), "task-20260417-093000")


class TestResolveUniqueSlug(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(
            resolve_unique_slug("fix-thing", ref_exists=lambda _s: False),
            "fix-thing",
        )

    def test_one_existing_suffix_2(self):
        def fake(s: str) -> bool:
            return s == "fix-thing"

        self.assertEqual(resolve_unique_slug("fix-thing", fake), "fix-thing-2")

    def test_eight_existing_hits_limit_then_timestamp(self):
        import datetime

        # -2..-9 all taken (8 entries) plus the base — falls through to timestamp.
        taken = {"fix-thing"} | {f"fix-thing-{i}" for i in range(2, 10)}

        now = datetime.datetime(2026, 4, 17, 9, 30, 0)
        out = resolve_unique_slug("fix-thing", lambda s: s in taken, now=now)
        self.assertEqual(out, "task-20260417-093000")

    def test_gap_in_middle_picks_lowest_free(self):
        taken = {"fix-thing", "fix-thing-2", "fix-thing-4"}
        self.assertEqual(
            resolve_unique_slug("fix-thing", lambda s: s in taken),
            "fix-thing-3",
        )


class TestChooseDefaultBranch(unittest.TestCase):
    def test_symbolic_ref_present(self):
        self.assertEqual(choose_default_branch("origin/main", None), "main")

    def test_symbolic_ref_without_prefix(self):
        self.assertEqual(choose_default_branch("trunk", None), "trunk")

    def test_falls_through_to_gh(self):
        self.assertEqual(choose_default_branch(None, "master"), "master")

    def test_falls_through_to_main(self):
        self.assertEqual(choose_default_branch(None, None), "main")

    def test_empty_symbolic_ref_falls_through(self):
        self.assertEqual(choose_default_branch("", "trunk"), "trunk")

    def test_whitespace_only_gh_falls_through_to_main(self):
        self.assertEqual(choose_default_branch(None, "   "), "main")


class TestChooseBase(unittest.TestCase):
    def test_upstream_preferred_when_reachable(self):
        self.assertEqual(
            choose_base("main", upstream_has_ref=True), ("upstream", "upstream/main")
        )

    def test_origin_fallback_when_upstream_missing(self):
        self.assertEqual(
            choose_base("main", upstream_has_ref=False), ("origin", "origin/main")
        )

    def test_origin_fallback_when_upstream_unreachable(self):
        # upstream_has_ref=False covers both "no upstream remote" and
        # "upstream exists but ref unreachable" — single boolean.
        self.assertEqual(
            choose_base("master", upstream_has_ref=False), ("origin", "origin/master")
        )


class TestParseRepoSlug(unittest.TestCase):
    def test_https_with_git_suffix(self):
        self.assertEqual(
            parse_repo_slug("https://github.com/idvorkin/chop-conventions.git"),
            "idvorkin/chop-conventions",
        )

    def test_https_without_git_suffix(self):
        self.assertEqual(
            parse_repo_slug("https://github.com/idvorkin/chop-conventions"),
            "idvorkin/chop-conventions",
        )

    def test_ssh_with_git_suffix(self):
        self.assertEqual(
            parse_repo_slug("git@github.com:idvorkin/chop-conventions.git"),
            "idvorkin/chop-conventions",
        )

    def test_ssh_without_git_suffix(self):
        self.assertEqual(
            parse_repo_slug("git@github.com:idvorkin/blog"),
            "idvorkin/blog",
        )

    def test_gibberish_returns_none(self):
        self.assertIsNone(parse_repo_slug("not a url"))


class TestSessionLogHash(unittest.TestCase):
    def test_slash_replaced(self):
        self.assertEqual(
            session_log_hash_of("/home/foo/gits/bar"),
            "-home-foo-gits-bar",
        )

    def test_dot_also_replaced(self):
        # Load-bearing: `.github.io` -> `-github-io`, not `.github.io`.
        self.assertEqual(
            session_log_hash_of("/home/foo/gits/bar.github.io"),
            "-home-foo-gits-bar-github-io",
        )


class TestResolveSessionLog(unittest.TestCase):
    def test_symlinked_cwd_uses_physical_path(self):
        # Build a temp home with a .claude/projects/<hash> dir keyed to the
        # PHYSICAL path; caller passes the physical path so it must find the jsonl.
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            real_repo = home / "gits" / "myrepo.github.io"
            real_repo.mkdir(parents=True)
            physical = str(real_repo)

            # Put a jsonl under the PHYSICAL hash directory.
            hash_dir = home / ".claude" / "projects" / session_log_hash_of(physical)
            hash_dir.mkdir(parents=True)
            jsonl = hash_dir / "session-1.jsonl"
            jsonl.write_text("")

            # Caller resolved the symlink to `physical` via os.path.realpath.
            found = resolve_session_log(physical, physical, home)
            self.assertEqual(found, str(jsonl))

    def test_falls_back_to_repo_toplevel(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            # cwd hash dir doesn't exist; toplevel hash dir does.
            toplevel = "/home/x/gits/other"
            hash_dir = home / ".claude" / "projects" / session_log_hash_of(toplevel)
            hash_dir.mkdir(parents=True)
            jsonl = hash_dir / "session-1.jsonl"
            jsonl.write_text("")

            found = resolve_session_log("/home/x/wt", toplevel, home)
            self.assertEqual(found, str(jsonl))

    def test_returns_none_when_neither_resolves(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            found = resolve_session_log("/nowhere/cwd", "/nowhere/toplevel", home)
            self.assertIsNone(found)

    def test_picks_newest_jsonl_by_mtime(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            physical = "/tmp/phys"
            hash_dir = home / ".claude" / "projects" / session_log_hash_of(physical)
            hash_dir.mkdir(parents=True)
            older = hash_dir / "old.jsonl"
            newer = hash_dir / "new.jsonl"
            older.write_text("")
            newer.write_text("")
            # Force mtime order so the test doesn't race the clock resolution.
            os.utime(older, (1_000_000, 1_000_000))
            os.utime(newer, (2_000_000, 2_000_000))
            self.assertEqual(find_newest_jsonl(hash_dir), str(newer))


class TestResolveTargetPath(unittest.TestCase):
    def setUp(self):
        self.cwd = Path("/home/dev/gits/blog6")
        self.home = Path("/home/dev")

    def test_absolute_path(self):
        p, err = resolve_target_path("/absolute/elsewhere", self.cwd, self.home)
        self.assertIsNone(err)
        self.assertEqual(p, Path("/absolute/elsewhere"))

    def test_bare_name_resolves_to_home_gits(self):
        p, err = resolve_target_path("other-repo", self.cwd, self.home)
        self.assertIsNone(err)
        self.assertEqual(p, Path("/home/dev/gits/other-repo"))

    def test_owner_repo_slug_errors(self):
        p, err = resolve_target_path("idvorkin/chop", self.cwd, self.home)
        self.assertIsNone(p)
        assert err is not None
        self.assertIn("gh repo clone", err)


class TestRunPrepareDryRun(unittest.TestCase):
    """Orchestrator smoke test using stubbed subprocess calls."""

    def _fake_git(self, script):
        """Return a fake `_git(target, *args, check=False)` closure.

        `script` is a list of `(matcher, returncode, stdout, stderr)` tuples
        where `matcher(args_tuple)` returns True for the command this entry
        should handle. Entries are consumed in-order; unmatched calls raise.
        """
        import subprocess

        def fake(target: str, *args: str, check: bool = False):
            for matcher, rc, out, err in script:
                if matcher(args):
                    return subprocess.CompletedProcess(
                        args=["git", "-C", target, *args],
                        returncode=rc,
                        stdout=out,
                        stderr=err,
                    )
            raise AssertionError(f"unexpected git call: {args}")

        return fake

    def test_dry_run_does_not_mutate(self):
        """--dry-run must not call worktree add or write the exclude file."""
        import subprocess

        # Matchers — single-shot; not consumed.
        def is_(*expected):
            return lambda args: tuple(args[: len(expected)]) == expected

        script = [
            # Order doesn't matter — matchers are evaluated per-call.
            (is_("rev-parse", "--is-inside-work-tree"), 0, "true\n", ""),
            (
                is_("remote", "get-url", "origin"),
                0,
                "git@github.com:idvorkin/blog.git\n",
                "",
            ),
            (is_("remote"), 0, "origin\nupstream\n", ""),
            (is_("fetch", "origin"), 0, "", ""),
            (is_("fetch", "upstream"), 0, "", ""),
            (is_("remote", "set-head", "origin", "--auto"), 0, "", ""),
            (
                is_("symbolic-ref", "--short", "refs/remotes/origin/HEAD"),
                0,
                "origin/main\n",
                "",
            ),
            (is_("rev-parse", "--verify", "--quiet", "upstream/main"), 0, "", ""),
            (
                is_("rev-parse", "--verify", "--quiet", "refs/heads/delegated/my-slug"),
                1,
                "",
                "",
            ),
            (
                is_(
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    "refs/remotes/origin/delegated/my-slug",
                ),
                1,
                "",
                "",
            ),
            (is_("rev-parse", "--show-toplevel"), 0, "/home/dev/gits/blog6\n", ""),
        ]

        write_calls: list[str] = []

        def fake_worktree_add(*a, **kw):
            write_calls.append("worktree_add")
            return subprocess.CompletedProcess(
                args=a, returncode=0, stdout="", stderr=""
            )

        def fake_ensure_exclude(target):
            write_calls.append("ensure_exclude")
            return True, None

        with (
            mock.patch.object(
                prepare_dispatch, "_git", side_effect=self._fake_git(script)
            ),
            mock.patch.object(
                prepare_dispatch, "_worktree_add", side_effect=fake_worktree_add
            ),
            mock.patch.object(
                prepare_dispatch, "_ensure_exclude", side_effect=fake_ensure_exclude
            ),
        ):
            with tempfile.TemporaryDirectory() as td:
                home = Path(td)
                target_dir = home / "gits" / "blog"
                target_dir.mkdir(parents=True)
                result = prepare_dispatch.run_prepare(
                    target_raw="blog",
                    slug_raw="my-slug",
                    task="do the thing",
                    dry_run=True,
                    cwd=Path(td),
                    home=home,
                )

        self.assertEqual(result["errors"], [])
        self.assertEqual(result["slug"], "my-slug")
        self.assertEqual(result["branch"], "delegated/my-slug")
        self.assertEqual(result["base_remote"], "upstream")
        self.assertEqual(result["base_ref"], "upstream/main")
        self.assertEqual(result["default_branch"], "main")
        self.assertEqual(result["target_repo_slug"], "idvorkin/blog")
        self.assertTrue(
            result["worktree_path"].endswith("/.worktrees/delegated-my-slug")
        )
        # Load-bearing: dry-run emits the JSON but never calls the mutating helpers.
        self.assertEqual(write_calls, [])


if __name__ == "__main__":
    unittest.main()
