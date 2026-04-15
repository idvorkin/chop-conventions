#!/usr/bin/env python3
"""Unit tests for diagnose.py pure functions.

Run with: python3 -m unittest test_diagnose.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Make sibling diagnose.py importable
sys.path.insert(0, str(Path(__file__).parent))

from diagnose import (  # noqa: E402
    CherryAnalysis,
    MachineInfo,
    Remote,
    WorktreeRef,
    check_post_up_to_date,
    check_shared_claude_md,
    classify_dev_machine,
    classify_machine,
    classify_remotes,
    compute_slot_action,
    is_fork_url,
    parse_cherry_status,
    parse_left_right_count,
    parse_remotes,
    parse_symbolic_ref_output,
    parse_worktree_list,
    resolve_chop_root,
)

FORK_ORGS = ["idvorkin-ai-tools"]


class TestParseRemotes(unittest.TestCase):
    def test_single_remote_dedups_fetch_and_push(self):
        raw = (
            "origin\tgit@github.com:idvorkin/chop.git (fetch)\n"
            "origin\tgit@github.com:idvorkin/chop.git (push)\n"
        )
        self.assertEqual(
            parse_remotes(raw),
            [Remote("origin", "git@github.com:idvorkin/chop.git")],
        )

    def test_two_remotes(self):
        raw = (
            "origin\tgit@github.com:idvorkin-ai-tools/chop.git (fetch)\n"
            "origin\tgit@github.com:idvorkin-ai-tools/chop.git (push)\n"
            "upstream\tgit@github.com:idvorkin/chop.git (fetch)\n"
            "upstream\tgit@github.com:idvorkin/chop.git (push)\n"
        )
        result = parse_remotes(raw)
        self.assertEqual(len(result), 2)
        self.assertIn(
            Remote("origin", "git@github.com:idvorkin-ai-tools/chop.git"), result
        )
        self.assertIn(Remote("upstream", "git@github.com:idvorkin/chop.git"), result)

    def test_empty_output(self):
        self.assertEqual(parse_remotes(""), [])


class TestIsForkUrl(unittest.TestCase):
    def test_ssh_url_matches(self):
        self.assertTrue(
            is_fork_url("git@github.com:idvorkin-ai-tools/foo.git", FORK_ORGS)
        )

    def test_https_url_matches(self):
        self.assertTrue(
            is_fork_url("https://github.com/idvorkin-ai-tools/foo", FORK_ORGS)
        )

    def test_non_fork_does_not_match(self):
        self.assertFalse(is_fork_url("git@github.com:idvorkin/foo.git", FORK_ORGS))

    def test_substring_does_not_false_match(self):
        # A user org named "idvorkin" should NOT match fork org "idvorkin-ai-tools"
        self.assertFalse(
            is_fork_url(
                "git@github.com:idvorkin/idvorkin-ai-tools-plugin.git", FORK_ORGS
            )
        )


class TestClassifyRemotes(unittest.TestCase):
    def test_single_canonical_origin_is_clean(self):
        remotes = [Remote("origin", "git@github.com:idvorkin/foo.git")]
        result = classify_remotes(remotes, FORK_ORGS)
        self.assertEqual(result.source, "origin")
        self.assertFalse(result.is_fork_workflow)
        self.assertEqual(result.issues, [])

    def test_proper_fork_workflow_is_clean(self):
        remotes = [
            Remote("origin", "git@github.com:idvorkin-ai-tools/foo.git"),
            Remote("upstream", "git@github.com:idvorkin/foo.git"),
        ]
        result = classify_remotes(remotes, FORK_ORGS)
        self.assertEqual(result.source, "upstream")
        self.assertTrue(result.is_fork_workflow)
        self.assertEqual(result.issues, [])

    def test_non_standard_remote_name_flagged(self):
        remotes = [
            Remote("origin", "git@github.com:idvorkin/foo.git"),
            Remote("fork", "git@github.com:idvorkin-ai-tools/foo.git"),
        ]
        result = classify_remotes(remotes, FORK_ORGS)
        kinds = [i.kind for i in result.issues]
        self.assertIn("non_standard_name", kinds)

    def test_swapped_remotes_flagged(self):
        # origin points at canonical; upstream points at fork — backwards
        remotes = [
            Remote("origin", "git@github.com:idvorkin/foo.git"),
            Remote("upstream", "git@github.com:idvorkin-ai-tools/foo.git"),
        ]
        result = classify_remotes(remotes, FORK_ORGS)
        kinds = [i.kind for i in result.issues]
        self.assertIn("swapped_remotes", kinds)

    def test_lone_fork_remote_flagged(self):
        # Only a fork exists; no canonical remote to PR against
        remotes = [Remote("origin", "git@github.com:idvorkin-ai-tools/foo.git")]
        result = classify_remotes(remotes, FORK_ORGS)
        kinds = [i.kind for i in result.issues]
        self.assertIn("fork_without_canonical", kinds)

    def test_issue_includes_fix_command(self):
        remotes = [
            Remote("origin", "git@github.com:idvorkin/foo.git"),
            Remote("upstream", "git@github.com:idvorkin-ai-tools/foo.git"),
        ]
        result = classify_remotes(remotes, FORK_ORGS)
        for issue in result.issues:
            self.assertTrue(issue.fix, f"issue {issue.kind} missing fix command")


class TestParseCherryStatus(unittest.TestCase):
    def test_splits_unique_and_equivalent_commits(self):
        raw = (
            "- 1234567 already upstream under different sha\n"
            "+ 89abcde follow-up work still missing upstream\n"
        )
        self.assertEqual(
            parse_cherry_status(raw),
            CherryAnalysis(
                unique_commits=["89abcde follow-up work still missing upstream"],
                equivalent_commits=["1234567 already upstream under different sha"],
            ),
        )

    def test_empty_output(self):
        self.assertEqual(
            parse_cherry_status(""),
            CherryAnalysis(unique_commits=[], equivalent_commits=[]),
        )


class TestParseLeftRightCount(unittest.TestCase):
    def test_parses_tab_separated_counts(self):
        self.assertEqual(parse_left_right_count("3\t7"), (3, 7))

    def test_invalid_output_returns_none(self):
        self.assertIsNone(parse_left_right_count("nonsense"))


class TestParseWorktreeList(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(parse_worktree_list(""), [])

    def test_primary_only(self):
        raw = "worktree /home/user/repo\nHEAD abc123def456\nbranch refs/heads/main\n"
        self.assertEqual(
            parse_worktree_list(raw),
            [WorktreeRef(path="/home/user/repo", branch="main")],
        )

    def test_primary_plus_linked(self):
        raw = (
            "worktree /home/user/repo\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /home/user/repo/.worktrees/feature-1\n"
            "HEAD def456\n"
            "branch refs/heads/delegated/feature-1\n"
            "\n"
            "worktree /home/user/repo/.worktrees/feature-2\n"
            "HEAD ghi789\n"
            "branch refs/heads/delegated/feature-2\n"
        )
        self.assertEqual(
            parse_worktree_list(raw),
            [
                WorktreeRef(path="/home/user/repo", branch="main"),
                WorktreeRef(
                    path="/home/user/repo/.worktrees/feature-1",
                    branch="delegated/feature-1",
                ),
                WorktreeRef(
                    path="/home/user/repo/.worktrees/feature-2",
                    branch="delegated/feature-2",
                ),
            ],
        )

    def test_skips_detached_worktree(self):
        # Detached worktrees have no `branch` line — they should not appear
        # in the output since there's nothing to prune.
        raw = (
            "worktree /home/user/repo\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /home/user/repo/.worktrees/detached\n"
            "HEAD def456\n"
            "detached\n"
        )
        self.assertEqual(
            parse_worktree_list(raw),
            [WorktreeRef(path="/home/user/repo", branch="main")],
        )

    def test_skips_bare_worktree(self):
        # Bare worktrees have a `bare` marker instead of `HEAD`/`branch`.
        raw = (
            "worktree /home/user/repo.git\n"
            "bare\n"
            "\n"
            "worktree /home/user/repo\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
        )
        self.assertEqual(
            parse_worktree_list(raw),
            [WorktreeRef(path="/home/user/repo", branch="main")],
        )

    def test_preserves_order(self):
        # Primary-first ordering matters — caller relies on index 0 being primary.
        raw = (
            "worktree /primary\n"
            "HEAD aaa\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /linked\n"
            "HEAD bbb\n"
            "branch refs/heads/feature\n"
        )
        result = parse_worktree_list(raw)
        self.assertEqual(result[0].path, "/primary")
        self.assertEqual(result[1].path, "/linked")

    def test_path_with_spaces_preserved(self):
        # `git worktree list --porcelain` doesn't quote paths — spaces are
        # preserved literally. Parser must take everything after "worktree ".
        raw = "worktree /home/user/my project\nHEAD abc123\nbranch refs/heads/main\n"
        self.assertEqual(
            parse_worktree_list(raw),
            [WorktreeRef(path="/home/user/my project", branch="main")],
        )

    def test_branch_refs_heads_prefix_stripped(self):
        raw = "worktree /repo\nHEAD abc\nbranch refs/heads/nested/feature-name\n"
        self.assertEqual(
            parse_worktree_list(raw)[0].branch,
            "nested/feature-name",
        )


class TestParseSymbolicRefOutput(unittest.TestCase):
    def test_origin_main(self):
        self.assertEqual(
            parse_symbolic_ref_output("refs/remotes/origin/main", "origin"),
            "main",
        )

    def test_upstream_master(self):
        self.assertEqual(
            parse_symbolic_ref_output("refs/remotes/upstream/master", "upstream"),
            "master",
        )

    def test_trailing_newline_stripped(self):
        self.assertEqual(
            parse_symbolic_ref_output("refs/remotes/origin/develop\n", "origin"),
            "develop",
        )

    def test_wrong_src_returns_none(self):
        # If the ref belongs to a different remote, parser must not match.
        self.assertIsNone(
            parse_symbolic_ref_output("refs/remotes/upstream/main", "origin")
        )

    def test_missing_prefix_returns_none(self):
        self.assertIsNone(parse_symbolic_ref_output("refs/heads/main", "origin"))

    def test_empty_branch_returns_none(self):
        # Defensive: if stripping the prefix leaves an empty string, return None.
        self.assertIsNone(parse_symbolic_ref_output("refs/remotes/origin/", "origin"))


class TestClassifyMachine(unittest.TestCase):
    def test_darwin_with_mac_ver(self):
        machine, reasons = classify_machine(
            system="Darwin",
            mac_ver_nonempty=True,
            home_developer_exists=False,
        )
        self.assertEqual(machine, "mac")
        self.assertTrue(any("Darwin" in r for r in reasons))

    def test_darwin_without_mac_ver_is_unknown(self):
        # Defensive: platform.mac_ver()[0] being empty on Darwin is an
        # edge case we'd rather flag than silently misclassify.
        machine, _ = classify_machine(
            system="Darwin",
            mac_ver_nonempty=False,
            home_developer_exists=False,
        )
        self.assertEqual(machine, "unknown")

    def test_linux_with_home_developer_is_orbstack_dev(self):
        machine, reasons = classify_machine(
            system="Linux",
            mac_ver_nonempty=False,
            home_developer_exists=True,
        )
        self.assertEqual(machine, "orbstack-dev")
        self.assertTrue(any("/home/developer" in r for r in reasons))

    def test_linux_without_home_developer_is_unknown(self):
        machine, _ = classify_machine(
            system="Linux",
            mac_ver_nonempty=False,
            home_developer_exists=False,
        )
        self.assertEqual(machine, "unknown")

    def test_unknown_system(self):
        machine, reasons = classify_machine(
            system="FreeBSD",
            mac_ver_nonempty=False,
            home_developer_exists=False,
        )
        self.assertEqual(machine, "unknown")
        self.assertTrue(any("FreeBSD" in r for r in reasons))


class TestClassifyDevMachine(unittest.TestCase):
    def test_both_conditions_true(self):
        dev, reasons = classify_dev_machine(
            tailscale_present=True, hostname="c-5004"
        )
        self.assertTrue(dev)
        self.assertTrue(any("hostname=c-5004" in r for r in reasons))

    def test_tailscale_missing(self):
        dev, _ = classify_dev_machine(
            tailscale_present=False, hostname="c-5004"
        )
        self.assertFalse(dev)

    def test_hostname_does_not_match(self):
        # Mac with Tailscale installed but human hostname: not a dev machine.
        dev, _ = classify_dev_machine(
            tailscale_present=True, hostname="igor-mbp"
        )
        self.assertFalse(dev)

    def test_neither_condition(self):
        dev, _ = classify_dev_machine(
            tailscale_present=False, hostname="other-host"
        )
        self.assertFalse(dev)

    def test_hostname_case_insensitive(self):
        dev, _ = classify_dev_machine(
            tailscale_present=True, hostname="C-5004"
        )
        self.assertTrue(dev)


class TestResolveChopRoot(unittest.TestCase):
    def _make_chop_checkout(self, parent: Path, name: str = "chop-conventions") -> Path:
        root = parent / name
        (root / "claude-md").mkdir(parents=True)
        (root / "claude-md" / "global.md").write_text("# global", encoding="utf-8")
        return root

    def test_env_var_set_and_valid(self):
        with tempfile.TemporaryDirectory() as td:
            chop = self._make_chop_checkout(Path(td))
            home = Path(td) / "fake-home"
            home.mkdir()
            result = resolve_chop_root({"CHOP_CONVENTIONS_ROOT": str(chop)}, home)
            self.assertEqual(result, chop)

    def test_env_var_set_but_path_missing(self):
        # Env var set to nonsense → fall through to home fallback.
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            fallback = self._make_chop_checkout(home / "gits")
            (home / "gits").resolve()  # ensure parent stat-able
            result = resolve_chop_root(
                {"CHOP_CONVENTIONS_ROOT": "/nonexistent/path"}, home
            )
            self.assertEqual(result, fallback)

    def test_env_var_unset_fallback_valid(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            fallback = self._make_chop_checkout(home / "gits")
            result = resolve_chop_root({}, home)
            self.assertEqual(result, fallback)

    def test_env_var_unset_fallback_missing(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            result = resolve_chop_root({}, home)
            self.assertIsNone(result)

    def test_env_var_points_at_dir_without_global_md(self):
        # Directory exists but lacks `claude-md/global.md` → rejected.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "empty-repo"
            root.mkdir()
            home = Path(td) / "home"
            home.mkdir()
            result = resolve_chop_root(
                {"CHOP_CONVENTIONS_ROOT": str(root)}, home
            )
            self.assertIsNone(result)


class TestCheckSharedClaudeMd(unittest.TestCase):
    def _setup(self, td: str, enabled: bool = False, machine: str = "orbstack-dev",
              dev_machine: bool = True) -> tuple[Path, Path, MachineInfo]:
        chop = Path(td) / "chop-conventions"
        (chop / "claude-md" / "machines").mkdir(parents=True)
        (chop / "claude-md" / "global.md").write_text("# global", encoding="utf-8")
        (chop / "claude-md" / "dev-machine.md").write_text(
            "# dev-machine", encoding="utf-8"
        )
        (chop / "claude-md" / "machines" / f"{machine}.md").write_text(
            f"# {machine}", encoding="utf-8"
        )
        home = Path(td) / "home"
        (home / ".claude" / "claude-md").mkdir(parents=True)
        if enabled:
            (home / ".claude" / "claude-md" / ".enabled").touch()
        info = MachineInfo(machine=machine, dev_machine=dev_machine, reasons=[])
        return chop, home, info

    def test_enabled_false_zero_actions(self):
        with tempfile.TemporaryDirectory() as td:
            chop, home, info = self._setup(td, enabled=False)
            block, errors = check_shared_claude_md(chop, home, False, info)
            self.assertEqual(block["actions"], [])
            self.assertEqual(errors, [])

    def test_enabled_true_no_symlinks_three_create_actions(self):
        with tempfile.TemporaryDirectory() as td:
            chop, home, info = self._setup(td, enabled=True)
            block, errors = check_shared_claude_md(chop, home, True, info)
            kinds = [a["kind"] for a in block["actions"]]
            slots = [a["slot"] for a in block["actions"]]
            self.assertEqual(set(kinds), {"create_symlink"})
            self.assertEqual(set(slots), {"global", "machine", "dev_machine"})
            self.assertEqual(errors, [])

    def test_correct_symlinks_empty_actions(self):
        with tempfile.TemporaryDirectory() as td:
            chop, home, info = self._setup(td, enabled=True)
            cm_home = home / ".claude" / "claude-md"
            (cm_home / "global.md").symlink_to(chop / "claude-md" / "global.md")
            (cm_home / "machine.md").symlink_to(
                chop / "claude-md" / "machines" / "orbstack-dev.md"
            )
            (cm_home / "dev-machine.md").symlink_to(
                chop / "claude-md" / "dev-machine.md"
            )
            block, _ = check_shared_claude_md(chop, home, True, info)
            self.assertEqual(block["actions"], [])
            for slot in ("global", "machine", "dev_machine"):
                self.assertFalse(block["actual"][slot]["drift"])

    def test_stale_machine_symlink_replace_action(self):
        with tempfile.TemporaryDirectory() as td:
            chop, home, info = self._setup(td, enabled=True, machine="orbstack-dev")
            # Point machine at the wrong file (e.g. mac).
            (chop / "claude-md" / "machines" / "mac.md").write_text(
                "# mac", encoding="utf-8"
            )
            cm_home = home / ".claude" / "claude-md"
            (cm_home / "machine.md").symlink_to(
                chop / "claude-md" / "machines" / "mac.md"
            )
            block, _ = check_shared_claude_md(chop, home, True, info)
            kinds_by_slot = {a["slot"]: a["kind"] for a in block["actions"]}
            self.assertEqual(kinds_by_slot["machine"], "replace_stale_symlink")

    def test_real_file_at_slot_reports_user_file(self):
        with tempfile.TemporaryDirectory() as td:
            chop, home, info = self._setup(td, enabled=True)
            cm_home = home / ".claude" / "claude-md"
            (cm_home / "global.md").write_text("my own rules", encoding="utf-8")
            block, _ = check_shared_claude_md(chop, home, True, info)
            kinds_by_slot = {a["slot"]: a["kind"] for a in block["actions"]}
            self.assertEqual(kinds_by_slot["global"], "report_user_file")

    def test_dev_machine_slot_obsolete_when_not_dev(self):
        # Machine went dev → non-dev: dev_machine symlink should be
        # surfaced as removable but not auto-removed.
        with tempfile.TemporaryDirectory() as td:
            chop, home, info = self._setup(td, enabled=True, dev_machine=False)
            cm_home = home / ".claude" / "claude-md"
            (cm_home / "dev-machine.md").symlink_to(
                chop / "claude-md" / "dev-machine.md"
            )
            block, _ = check_shared_claude_md(chop, home, True, info)
            kinds_by_slot = {a["slot"]: a["kind"] for a in block["actions"]}
            self.assertEqual(
                kinds_by_slot["dev_machine"], "remove_obsolete_symlink"
            )

    def test_partial_installation(self):
        # Only global symlinked; machine + dev_machine missing.
        with tempfile.TemporaryDirectory() as td:
            chop, home, info = self._setup(td, enabled=True)
            cm_home = home / ".claude" / "claude-md"
            (cm_home / "global.md").symlink_to(chop / "claude-md" / "global.md")
            block, _ = check_shared_claude_md(chop, home, True, info)
            kinds_by_slot = {a["slot"]: a["kind"] for a in block["actions"]}
            self.assertNotIn("global", kinds_by_slot)
            self.assertEqual(kinds_by_slot["machine"], "create_symlink")
            self.assertEqual(kinds_by_slot["dev_machine"], "create_symlink")

    def test_claude_md_dir_is_symlink_errors_and_no_actions(self):
        with tempfile.TemporaryDirectory() as td:
            chop = Path(td) / "chop-conventions"
            (chop / "claude-md" / "machines").mkdir(parents=True)
            (chop / "claude-md" / "global.md").write_text("g", encoding="utf-8")
            (chop / "claude-md" / "dev-machine.md").write_text("d", encoding="utf-8")
            (chop / "claude-md" / "machines" / "orbstack-dev.md").write_text(
                "o", encoding="utf-8"
            )
            home = Path(td) / "home"
            (home / ".claude").mkdir(parents=True)
            # Replace ~/.claude/claude-md with a symlink — refuse.
            target = Path(td) / "hostile"
            target.mkdir()
            (home / ".claude" / "claude-md").symlink_to(target)
            info = MachineInfo(
                machine="orbstack-dev", dev_machine=True, reasons=[]
            )
            _, errors = check_shared_claude_md(chop, home, True, info)
            self.assertTrue(
                any(e.get("code") == "claude_md_dir_is_symlink" for e in errors)
            )


class TestComputeSlotAction(unittest.TestCase):
    def _expected(self, should_install: bool = True) -> dict:
        return {
            "path": "/home/x/.claude/claude-md/global.md",
            "target": "/repo/claude-md/global.md",
            "should_install": should_install,
        }

    def test_correct_symlink_emits_no_action(self):
        expected = self._expected()
        actual = {
            "exists": True,
            "is_symlink": True,
            "resolves_to": "/repo/claude-md/global.md",
        }
        self.assertIsNone(compute_slot_action("global", expected, actual))

    def test_missing_with_should_install_emits_create(self):
        expected = self._expected()
        actual = {"exists": False, "is_symlink": False, "resolves_to": None}
        action = compute_slot_action("global", expected, actual)
        assert action is not None
        self.assertEqual(action["kind"], "create_symlink")

    def test_missing_without_should_install_emits_nothing(self):
        expected = self._expected(should_install=False)
        actual = {"exists": False, "is_symlink": False, "resolves_to": None}
        self.assertIsNone(compute_slot_action("global", expected, actual))


class TestCheckPostUpToDate(unittest.TestCase):
    def test_no_repo_toplevel(self):
        path, errors = check_post_up_to_date(None)
        self.assertIsNone(path)
        self.assertEqual(errors, [])

    def test_hook_present(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".claude").mkdir()
            hook = repo / ".claude" / "post-up-to-date.md"
            hook.write_text("# hook", encoding="utf-8")
            path, errors = check_post_up_to_date(repo)
            self.assertEqual(path, str(hook))
            self.assertEqual(errors, [])

    def test_hook_absent(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            path, errors = check_post_up_to_date(repo)
            self.assertIsNone(path)
            self.assertEqual(errors, [])

    def test_symlinked_hook_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".claude").mkdir()
            real = Path(td) / "elsewhere.md"
            real.write_text("# elsewhere", encoding="utf-8")
            hook = repo / ".claude" / "post-up-to-date.md"
            hook.symlink_to(real)
            path, errors = check_post_up_to_date(repo)
            self.assertIsNone(path)
            self.assertTrue(
                any(e.get("code") == "hook_is_symlink" for e in errors)
            )

    def test_subdirectory_does_not_affect_resolution(self):
        # The function takes an already-resolved toplevel, so running it
        # with a toplevel that differs from cwd is the whole point.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".claude").mkdir()
            hook = repo / ".claude" / "post-up-to-date.md"
            hook.write_text("# hook", encoding="utf-8")
            (repo / "subdir").mkdir()
            path, _ = check_post_up_to_date(repo)
            self.assertEqual(path, str(hook))


class TestRunDiagnoseChopRootUnresolved(unittest.TestCase):
    """Orchestrator invariant (spec §734-737):

    When `resolve_chop_root` returns None, `run_diagnose()` MUST
    (a) append a `{subsystem: "shared_claude_md", code:
    "chop_root_unresolved"}` entry to `errors[]` and (b) omit the
    `shared_claude_md` key entirely from the returned JSON — not
    emit an empty block.

    We exercise this via a subprocess invocation: a throwaway
    minimal git repo + bogus `CHOP_CONVENTIONS_ROOT` + throwaway
    HOME with no `~/gits/chop-conventions/`. That drives both
    resolver candidates to miss and `resolve_chop_root` returns
    None, exactly the condition under test.
    """

    DIAGNOSE_PATH = Path(__file__).parent / "diagnose.py"

    def test_chop_root_unresolved_omits_key_and_emits_error(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            fake_home = Path(td) / "fake-home"
            fake_home.mkdir()
            # Minimal git repo — runtime of `diagnose.py` shells out
            # to git, so it needs a repo to run against.
            def _git(*args: str) -> None:
                subprocess.run(
                    ["git", *args],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                )

            _git("init", "-q")
            _git("config", "user.email", "test@test")
            _git("config", "user.name", "test")
            _git("config", "commit.gpgsign", "false")
            (repo / "README.md").write_text("# r", encoding="utf-8")
            _git("add", "README.md")
            _git("commit", "-q", "-m", "initial")

            env = {
                **os.environ,
                "CHOP_CONVENTIONS_ROOT": str(Path(td) / "nowhere"),
                "HOME": str(fake_home),
            }
            proc = subprocess.run(
                [sys.executable, str(self.DIAGNOSE_PATH)],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)

            # Invariant 1: no `shared_claude_md` key at all.
            self.assertNotIn("shared_claude_md", data)

            # Invariant 2: errors[] carries the chop_root_unresolved
            # entry, tagged with the right subsystem+code.
            matches = [
                e
                for e in data["errors"]
                if isinstance(e, dict)
                and e.get("subsystem") == "shared_claude_md"
                and e.get("code") == "chop_root_unresolved"
            ]
            self.assertEqual(
                len(matches),
                1,
                f"expected exactly one chop_root_unresolved error, "
                f"got errors={data['errors']!r}",
            )


if __name__ == "__main__":
    unittest.main()
