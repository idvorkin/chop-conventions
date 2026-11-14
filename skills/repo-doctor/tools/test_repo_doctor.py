"""Unit tests for repo_doctor: fixture repos built in tmp_path, no git subprocess."""

import os
from pathlib import Path

import repo_doctor
from repo_doctor import (
    check_agents_md,
    check_agents_pointers,
    check_beads_hooks,
    check_beads_local,
    check_bug_reporting,
    check_jtbd,
    check_justfile,
    check_no_mistakes,
    check_scripts_via_just,
    check_stories,
    diagnose,
    justfile_recipes,
    recipe_roles,
    render,
    render_matrix,
    scan,
)

HOUSE_CONFIG = '[core]\n\trepositoryformatversion = 0\n[remote "origin"]\n\turl = https://github.com/idvorkin/foo.git\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'


def make_repo(root: Path, name="foo", config=HOUSE_CONFIG) -> Path:
    repo = root / name
    (repo / ".git" / "info").mkdir(parents=True)
    (repo / ".git" / "hooks").mkdir()
    (repo / ".git" / "config").write_text(config)
    return repo


def full_house(root: Path) -> Path:
    """A repo that passes every check (an iOS one, so bug_reporting is ok not gap)."""
    repo = make_repo(root)
    (repo / "AGENTS.md").write_text(
        "# Agent instructions\n`just test`; bd for issues; docs/stories/README.md; VISION.md; the no-mistakes gate.\n"
    )
    os.symlink("AGENTS.md", repo / "CLAUDE.md")
    (repo / ".beads").mkdir()
    (repo / ".git" / "info" / "exclude").write_text("# local\n.beads/\n")
    (repo / "justfile").write_text(
        "default:\n    @just --list\n\ntest:\n    swift test\n\nbuild-sim:\n    xcodebuild\n\npull-logs:\n    ./scripts/pull-logs.sh\n"
    )
    (repo / "scripts").mkdir()
    (repo / "scripts" / "pull-logs.sh").write_text("#!/bin/sh\n")
    (repo / "VISION.md").write_text("# Vision\n")
    (repo / ".no-mistakes.yaml").write_text(
        'commands:\n  test: "just test"\nno_ci: true\n'
    )
    (repo / "docs" / "stories").mkdir(parents=True)
    (repo / "docs" / "stories" / "README.md").write_text("# Stories\n")
    (repo / "docs" / "stories" / "01-recording.md").write_text("### User Story 001:\n")
    (repo / "docs" / "jtbd.md").write_text("# JTBD\n")
    (repo / "App.xcodeproj").mkdir()
    (repo / "App.xcodeproj" / "project.pbxproj").write_text(
        'SDKROOT = iphoneos;\nXCRemoteSwiftPackageReference "bug-kit" = { repositoryURL = "https://github.com/idvorkin/bug-kit"; };\n'
    )
    return repo


# ---------- justfile parsing ----------


def test_justfile_recipes_skips_bodies_comments_and_assignments():
    text = (
        "# a comment\nport := '8080'\n\ndefault:\n    @just --list\n\n"
        "test *ARGS:\n    pytest {{ARGS}}\n\n@build-sim:\n    xcodebuild\n\nrun-device: build-sim\n    ios-deploy\n"
    )
    assert justfile_recipes(text) == ["default", "test", "build-sim", "run-device"]


def test_recipe_roles_match_by_prefix():
    roles = recipe_roles(["default", "test-sim", "test", "run", "logs"])
    assert roles["test"] == ["test-sim", "test"]
    assert roles["build"] == ["run"]
    assert recipe_roles(["restart-voice-bridge"]) == {"test": [], "build": []}


# ---------- per-check behaviour ----------


def test_agents_md_states(tmp_path):
    repo = make_repo(tmp_path)
    assert check_agents_md(repo).status == "fail"
    (repo / "AGENTS.md").write_text("# agents\n")
    assert check_agents_md(repo).status == "warn"  # no CLAUDE.md at all
    (repo / "CLAUDE.md").write_text("Read AGENTS.md first.\n\n@AGENTS.md\n")
    assert check_agents_md(repo).status == "ok"
    (repo / "CLAUDE.md").write_text("# A whole separate instruction file\n" * 20)
    assert check_agents_md(repo).status == "warn"
    (repo / "CLAUDE.md").unlink()
    os.symlink("AGENTS.md", repo / "CLAUDE.md")
    assert check_agents_md(repo).status == "ok"


def test_agents_pointers_only_demand_what_the_repo_has(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "AGENTS.md").write_text("Run `just test`.\n")
    assert (
        check_agents_pointers(repo, {"justfile": True, "beads": False}).status == "ok"
    )
    check = check_agents_pointers(
        repo, {"justfile": True, "beads": True, "vision": True}
    )
    assert check.status == "warn"
    assert (
        "beads" in check.detail
        and "vision" in check.detail
        and "justfile" not in check.detail
    )


def test_beads_local_flags_missing_exclude_and_tracked_files(tmp_path):
    repo = make_repo(tmp_path)
    (repo / ".beads").mkdir()
    check = check_beads_local(repo)
    assert check.status == "warn" and "exclude" in check.detail
    (repo / ".git" / "info" / "exclude").write_text(".beads/\n")
    (repo / ".git" / "index").write_bytes(
        b"DIRC\x00\x00\x00\x02\x00\x00\x00\x02junk\x00.beads/config.yaml\x00more\x00.beads/README.md\x00"
    )
    check = check_beads_local(repo)
    assert check.status == "warn" and "2 .beads/ file(s) tracked" in check.detail


def test_beads_hooks_detects_hookspath_and_hook_scripts(tmp_path):
    repo = make_repo(
        tmp_path,
        config=HOUSE_CONFIG.replace(
            "[core]\n", "[core]\n\thooksPath = /x/.beads/hooks\n"
        ),
    )
    (repo / ".beads").mkdir()
    assert check_beads_hooks(repo).status == "fail"
    repo2 = make_repo(tmp_path, name="bar")
    (repo2 / ".beads").mkdir()
    (repo2 / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nbd sync\n")
    (repo2 / ".git" / "hooks" / "pre-push.sample").write_text("bd\n")
    check = check_beads_hooks(repo2)
    assert check.status == "fail" and check.detail.endswith("pre-commit")


def test_justfile_check_wants_test_and_build_roles(tmp_path):
    repo = make_repo(tmp_path)
    assert check_justfile(repo).status == "fail"
    (repo / "justfile").write_text("default:\n    @just --list\n")
    check = check_justfile(repo)
    assert check.status == "warn" and "no test, build recipe" in check.detail
    (repo / "justfile").write_text("test:\n    pytest\ninstall:\n    cp a b\n")
    assert check_justfile(repo).status == "ok"


def test_scripts_via_just(tmp_path):
    repo = make_repo(tmp_path)
    assert check_scripts_via_just(repo).status == "na"
    (repo / "scripts").mkdir()
    (repo / "scripts" / "deploy.sh").write_text("#!/bin/sh\n")
    (repo / "scripts" / "notes.md").write_text("not runnable\n")
    assert check_scripts_via_just(repo).status == "warn"
    (repo / "justfile").write_text("deploy:\n    ./scripts/deploy.sh\n")
    assert check_scripts_via_just(repo).status == "ok"


def test_no_mistakes_accepts_commands_test_or_test_instructions(tmp_path):
    repo = make_repo(tmp_path)
    assert check_no_mistakes(repo).status == "fail"
    (repo / ".no-mistakes.yaml").write_text("commands:\n  lint: 'ruff'\n")
    assert check_no_mistakes(repo).status == "warn"
    (repo / ".no-mistakes.yaml").write_text(
        "commands:\n  lint: 'ruff'\ntest:\n  instructions: |\n    swift test --filter x\n"
    )
    assert check_no_mistakes(repo).status == "ok"
    (repo / ".no-mistakes.yaml").write_text('commands:\n  test: "just test"\n')
    assert "just test" in check_no_mistakes(repo).detail


def test_stories_layouts(tmp_path):
    repo = make_repo(tmp_path)
    assert check_stories(repo).status == "fail"
    (repo / "docs").mkdir()
    (repo / "docs" / "user-needs.md").write_text("# needs\n")
    check = check_stories(repo)
    assert check.status == "warn" and "docs/user-needs.md" in check.detail
    (repo / "docs" / "stories").mkdir()
    (repo / "docs" / "stories" / "README.md").write_text("# s\n")
    assert check_stories(repo).status == "warn"  # README but no journey files
    (repo / "docs" / "stories" / "01-x.md").write_text("### User Story 001:\n")
    assert check_stories(repo).status == "ok"


def test_jtbd_accepts_file_or_heading(tmp_path):
    repo = make_repo(tmp_path)
    assert check_jtbd(repo).status == "fail"
    (repo / "VISION.md").write_text("# Vision\n\n## Jobs to be done\n- one\n")
    assert check_jtbd(repo).status == "ok"
    (repo / "VISION.md").write_text("we mention jobs-to-be-done in prose only\n")
    assert check_jtbd(repo).status == "fail"
    (repo / "docs").mkdir()
    (repo / "docs" / "jtbd.md").write_text("# JTBD\n")
    assert check_jtbd(repo).detail == "docs/jtbd.md"


def test_bug_reporting_ios_vs_gap_vs_self(tmp_path):
    plain = make_repo(tmp_path, name="plain")
    assert check_bug_reporting(plain).status == "gap"
    mac = make_repo(tmp_path, name="mac")
    (mac / "Package.swift").write_text("platforms: [.macOS(.v14)]\n")
    assert check_bug_reporting(mac).status == "gap"
    ios = make_repo(tmp_path, name="ios")
    (ios / "Package.swift").write_text("platforms: [.iOS(.v17), .macOS(.v14)]\n")
    assert check_bug_reporting(ios).status == "fail"
    (ios / "Package.resolved").write_text(
        '"location" : "https://github.com/idvorkin/bug-kit"\n'
    )
    assert check_bug_reporting(ios).status == "ok"
    kit = make_repo(
        tmp_path, name="kit", config=HOUSE_CONFIG.replace("foo.git", "bug-kit.git")
    )
    (kit / "Package.swift").write_text("platforms: [.iOS(.v17)]\n")
    assert check_bug_reporting(kit).status == "na"


# ---------- whole-repo ----------


def test_full_house_is_healthy_and_renders(tmp_path):
    report = diagnose(full_house(tmp_path))
    failing = [c["name"] for c in report["checks"] if c["status"] != "ok"]
    assert failing == [], failing
    assert report["healthy"] and report["house_repo"] and report["ios"]
    text = render(report)
    assert "fix:" not in text and "ok=12" in text


def test_empty_repo_fails_loudly(tmp_path):
    report = diagnose(make_repo(tmp_path))
    assert not report["healthy"]
    by_name = {c["name"]: c["status"] for c in report["checks"]}
    assert by_name["agents_md"] == "fail"
    assert by_name["beads"] == "fail"
    assert by_name["vision"] == "fail"
    assert by_name["bug_reporting"] == "gap"
    assert all(c["fix"] for c in report["checks"] if c["status"] == "fail")


def test_scan_picks_house_repos_only(tmp_path):
    full_house(tmp_path)
    make_repo(
        tmp_path,
        name="other",
        config='[remote "origin"]\n\turl = git@github.com:someone/x.git\n',
    )
    (tmp_path / "not-a-repo").mkdir()
    assert [p.name for p in scan(tmp_path)] == ["foo"]


def test_matrix_has_one_row_per_repo(tmp_path):
    reports = [
        diagnose(full_house(tmp_path)),
        diagnose(make_repo(tmp_path, name="bare")),
    ]
    text = render_matrix(reports)
    lines = text.splitlines()
    assert lines[0].startswith("repo") and "bug_reporting" in lines[0]
    assert lines[2].startswith("foo") and lines[3].startswith("bare")
    assert "2 repo(s); 1 with failures" in text


def test_worktree_gitdir_pointer_resolves(tmp_path):
    main = make_repo(tmp_path, name="main")
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (main / ".git" / "worktrees" / "wt" / "commondir").write_text("../..\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n")
    assert repo_doctor.house_repo(wt)
