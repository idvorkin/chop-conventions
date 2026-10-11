#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer>=0.12"]
# ///
"""Audit a repository against the house layout (AGENTS.md, beads, just, VISION.md,
no-mistakes, JTBD + user stories, bug reporting).

Read-only: nothing here writes to the target repo. The skill's convert playbook
(convert.md) is what fixes a finding, one item per branch + PR.

    repo_doctor.py ~/gits/foo ~/gits/bar      # table, exit 1 if any check fails
    repo_doctor.py --json ~/gits/foo          # machine-readable
    repo_doctor.py --scan ~/gits              # every idvorkin / idvorkin-ai-tools repo under ~/gits

Statuses: ok | warn | fail | na | gap. Only `fail` sets the exit code. `gap` is a
convention the repo cannot meet yet because the house tool does not exist (bug-kit
for non-iOS repos).

Tested as a library via test_repo_doctor.py (pure functions over a repo path;
git is read through `.git/config` and `.git/info/exclude`, not subprocesses).
"""

import configparser
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

HOUSE_ORGS = ("idvorkin", "idvorkin-ai-tools")

VISION_HINT = (
    "run the vision skill (kunchenguid/vision, ~/gits/vision) and get Igor's approval; "
    "never draft one blind"
)
STORIES_HINT = (
    "docs/stories/README.md (persona, format, Status vocabulary) + NN-<journey>.md files, "
    "Mike Cohn use case + Gherkin; copy the README from exercise-analyzer or cockpit"
)
JTBD_HINT = (
    "a jobs-to-be-done doc (docs/jtbd.md, as in context-grabber) or a '## Jobs to be done' "
    "section in VISION.md or docs/stories/README.md; draft it for Igor's review"
)

# Recipes that count as the repo's "do the thing" entry points, by role.
RECIPE_ROLES = {
    "test": ("test",),
    "build": ("build", "run", "install", "dev", "app", "serve", "start"),
}

_RECIPE_RE = re.compile(r"^(?:@)?([A-Za-z_][A-Za-z0-9_-]*)(?:\s+[^:=\n]*)?\s*:(?!=)")
_IOS_PBX_RE = re.compile(r"SDKROOT\s*=\s*iphoneos|IPHONEOS_DEPLOYMENT_TARGET")
_IOS_SWIFTPM_RE = re.compile(r"\.iOS\s*\(")
_BUG_KIT_RE = re.compile(
    r"idvorkin/bug-kit|XCRemoteSwiftPackageReference \"bug-kit\"|\bBugKit\b"
)
_JTBD_RE = re.compile(r"jobs[- ]to[- ]be[- ]done|\bJTBD\b", re.IGNORECASE)


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail | na | gap
    detail: str
    fix: str = ""


# ---------- small file helpers ----------


def _read(path: Path, limit: int = 2_000_000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def _git_dir(repo: Path) -> Path | None:
    dot_git = repo / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():  # worktree or submodule: "gitdir: <path>"
        text = _read(dot_git).strip()
        if text.startswith("gitdir:"):
            target = Path(text.split(":", 1)[1].strip())
            return target if target.is_absolute() else (repo / target).resolve()
    return None


def _common_dir(repo: Path) -> Path | None:
    git_dir = _git_dir(repo)
    if git_dir is None:
        return None
    common = git_dir / "commondir"
    if common.is_file():
        rel = _read(common).strip()
        return (git_dir / rel).resolve()
    return git_dir


def git_config(repo: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    common = _common_dir(repo)
    if common is not None:
        text = _read(common / "config")
        try:
            parser.read_string(text)
        except configparser.Error:
            pass
    return parser


def remote_urls(repo: Path) -> dict[str, str]:
    config = git_config(repo)
    urls = {}
    for section in config.sections():
        match = re.fullmatch(r'remote "(.+)"', section)
        if match and config.has_option(section, "url"):
            urls[match.group(1)] = config.get(section, "url").strip()
    return urls


def owner_of(url: str) -> str:
    match = re.search(r"github\.com[:/]([^/]+)/", url)
    return match.group(1) if match else ""


def house_repo(repo: Path) -> bool:
    return any(owner_of(url) in HOUSE_ORGS for url in remote_urls(repo).values())


def exclude_entries(repo: Path) -> list[str]:
    common = _common_dir(repo)
    if common is None:
        return []
    lines = _read(common / "info" / "exclude").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def tracked_beads_files(repo: Path) -> int:
    """Count `.beads/` paths in the git index without spawning git.

    The index is binary; path names are NUL-terminated after a fixed header, so a
    plain substring scan is enough to count entries that start with `.beads/`.
    """
    git_dir = _git_dir(repo)
    if git_dir is None:
        return 0
    try:
        data = (git_dir / "index").read_bytes()
    except OSError:
        return 0
    return len(re.findall(rb"\x00\.beads/[^\x00]+", data))


# ---------- platform detection ----------


def _pbxprojs(repo: Path) -> list[Path]:
    found = []
    for depth_glob in ("*.xcodeproj/project.pbxproj", "*/*.xcodeproj/project.pbxproj"):
        found.extend(p for p in repo.glob(depth_glob) if "node_modules" not in p.parts)
    return found


def _package_swifts(repo: Path) -> list[Path]:
    found = []
    for depth_glob in ("Package.swift", "*/Package.swift"):
        found.extend(p for p in repo.glob(depth_glob) if ".build" not in p.parts)
    return found


def is_ios(repo: Path) -> bool:
    for pbx in _pbxprojs(repo):
        if _IOS_PBX_RE.search(_read(pbx)):
            return True
    return any(_IOS_SWIFTPM_RE.search(_read(pkg)) for pkg in _package_swifts(repo))


def is_bug_kit_itself(repo: Path) -> bool:
    return any(
        owner_of(u) in HOUSE_ORGS
        and u.rstrip("/").removesuffix(".git").endswith("/bug-kit")
        for u in remote_urls(repo).values()
    )


def depends_on_bug_kit(repo: Path) -> bool:
    candidates = _pbxprojs(repo) + _package_swifts(repo)
    candidates += list(repo.glob("project.yml")) + list(repo.glob("*/project.yml"))
    candidates += list(repo.glob("**/Package.resolved"))
    return any(
        _BUG_KIT_RE.search(_read(path))
        for path in candidates
        if "node_modules" not in path.parts
    )


# ---------- justfile ----------


def justfile_recipes(text: str) -> list[str]:
    names = []
    for line in text.splitlines():
        if line.startswith((" ", "\t")) or line.lstrip().startswith("#"):
            continue
        match = _RECIPE_RE.match(line)
        if match:
            names.append(match.group(1))
    return names


def recipe_roles(names: list[str]) -> dict[str, list[str]]:
    roles = {}
    for role, prefixes in RECIPE_ROLES.items():
        roles[role] = [
            n for n in names if n == role or any(n.startswith(p) for p in prefixes)
        ]
    return roles


# ---------- the checks ----------


def check_agents_md(repo: Path) -> Check:
    agents = repo / "AGENTS.md"
    claude = repo / "CLAUDE.md"
    if not agents.is_file():
        return Check(
            "agents_md",
            "fail",
            "no AGENTS.md",
            "write AGENTS.md (the one agent instruction file) and make CLAUDE.md a symlink to it",
        )
    if not claude.exists():
        return Check(
            "agents_md",
            "warn",
            "AGENTS.md present, no CLAUDE.md",
            "ln -s AGENTS.md CLAUDE.md so Claude Code loads it",
        )
    if claude.is_symlink():
        target = os.readlink(claude)
        if Path(target).name == "AGENTS.md":
            return Check("agents_md", "ok", "AGENTS.md; CLAUDE.md -> AGENTS.md")
        return Check(
            "agents_md",
            "warn",
            f"CLAUDE.md -> {target}, not AGENTS.md",
            "point the symlink at AGENTS.md",
        )
    claude_text = _read(claude)
    if re.search(r"^@AGENTS\.md\s*$", claude_text, re.MULTILINE):
        return Check("agents_md", "ok", "AGENTS.md; CLAUDE.md imports it (@AGENTS.md)")
    return Check(
        "agents_md",
        "warn",
        f"CLAUDE.md is a separate file ({claude.stat().st_size} bytes) - two instruction files drift",
        "fold CLAUDE.md into AGENTS.md, then replace CLAUDE.md with a symlink or an @AGENTS.md import",
    )


def check_agents_pointers(repo: Path, present: dict[str, bool]) -> Check:
    """AGENTS.md is the anchor: it must point readers at every convention the repo has."""
    agents = repo / "AGENTS.md"
    if not agents.is_file():
        return Check("agents_pointers", "na", "no AGENTS.md to check")
    text = _read(agents)
    wanted = {
        "justfile": r"\bjust\b",
        "beads": r"\bbd\b|beads",
        "stories": r"docs/stories",
        "vision": r"VISION\.md",
        "no_mistakes": r"no-mistakes",
    }
    missing = [
        name
        for name, pattern in wanted.items()
        if present.get(name) and not re.search(pattern, text)
    ]
    if missing:
        return Check(
            "agents_pointers",
            "warn",
            "AGENTS.md does not mention: " + ", ".join(missing),
            "add a line per convention so an agent reading AGENTS.md finds just, bd, the stories, VISION.md and the gate",
        )
    covered = [name for name in wanted if present.get(name)]
    return Check(
        "agents_pointers",
        "ok",
        "AGENTS.md points at: " + (", ".join(covered) or "nothing yet"),
    )


def check_beads(repo: Path) -> Check:
    beads = repo / ".beads"
    if not beads.is_dir():
        return Check(
            "beads",
            "fail",
            "no .beads/",
            "bd init -p <prefix> --skip-hooks --skip-agents --non-interactive; then add .beads/ to .git/info/exclude",
        )
    return Check("beads", "ok", ".beads/ present")


def check_beads_local(repo: Path) -> Check:
    if not (repo / ".beads").is_dir():
        return Check("beads_local", "na", "no beads")
    excluded = any(e.rstrip("/") == ".beads" for e in exclude_entries(repo))
    tracked = tracked_beads_files(repo)
    problems = []
    if not excluded:
        problems.append(".beads/ not in .git/info/exclude")
    if tracked:
        problems.append(f"{tracked} .beads/ file(s) tracked in git")
    if problems:
        return Check(
            "beads_local",
            "warn",
            "; ".join(problems),
            "beads stay local to the box: echo .beads/ >> .git/info/exclude; git rm -r --cached .beads (keep the files on disk)",
        )
    return Check("beads_local", "ok", ".beads/ excluded, nothing tracked")


def check_beads_hooks(repo: Path) -> Check:
    if not (repo / ".beads").is_dir():
        return Check("beads_hooks", "na", "no beads")
    config = git_config(repo)
    hooks_path = config.get("core", "hookspath", fallback="").strip()
    if ".beads" in hooks_path:
        return Check(
            "beads_hooks",
            "fail",
            f"core.hooksPath = {hooks_path} (bd-installed hooks)",
            "git config --unset core.hooksPath; bd hooks ignored prek's exit status for months - never let bd install hooks",
        )
    common = _common_dir(repo)
    bd_hooks = []
    if common is not None and (common / "hooks").is_dir():
        for hook in (common / "hooks").iterdir():
            if hook.suffix == ".sample" or not hook.is_file():
                continue
            if re.search(r"\bbd\b|beads", _read(hook, 4096)):
                bd_hooks.append(hook.name)
    if bd_hooks:
        return Check(
            "beads_hooks",
            "fail",
            "bd hooks in .git/hooks: " + ", ".join(sorted(bd_hooks)),
            "remove them; re-run bd init with --skip-hooks if needed",
        )
    return Check("beads_hooks", "ok", "no bd git hooks")


def check_justfile(repo: Path) -> Check:
    justfile = next(
        (
            repo / n
            for n in ("justfile", "Justfile", ".justfile")
            if (repo / n).is_file()
        ),
        None,
    )
    if justfile is None:
        return Check(
            "justfile",
            "fail",
            "no justfile",
            "add a justfile with `default: @just --list`, `test`, and the build/run/install recipes; scripts run through just",
        )
    names = justfile_recipes(_read(justfile))
    roles = recipe_roles(names)
    missing = [role for role, hits in roles.items() if not hits]
    if missing:
        return Check(
            "justfile",
            "warn",
            f"{len(names)} recipe(s); no {', '.join(missing)} recipe",
            "every repo exposes `just test` and a build/run/install recipe; wrap the real command (uv run pytest, swift build, ...)",
        )
    return Check(
        "justfile",
        "ok",
        f"{len(names)} recipes; test={roles['test'][0]}, build={roles['build'][0]}",
    )


def check_scripts_via_just(repo: Path) -> Check:
    scripts = repo / "scripts"
    justfile = next(
        (
            repo / n
            for n in ("justfile", "Justfile", ".justfile")
            if (repo / n).is_file()
        ),
        None,
    )
    if not scripts.is_dir():
        return Check("scripts_via_just", "na", "no scripts/ dir")
    runnable = [
        p.name
        for p in scripts.iterdir()
        if p.is_file() and (os.access(p, os.X_OK) or p.suffix in (".sh", ".py", ".js"))
    ]
    if not runnable:
        return Check("scripts_via_just", "na", "scripts/ has no runnable files")
    if justfile is None:
        return Check(
            "scripts_via_just",
            "warn",
            f"{len(runnable)} script(s) in scripts/, no justfile",
            "expose them as just recipes",
        )
    text = _read(justfile)
    uncovered = [name for name in runnable if name not in text]
    if uncovered:
        return Check(
            "scripts_via_just",
            "warn",
            "scripts not reachable from the justfile: " + ", ".join(sorted(uncovered)),
            "add a recipe per script (or delete the script)",
        )
    return Check(
        "scripts_via_just", "ok", f"all {len(runnable)} script(s) have a just recipe"
    )


def check_vision(repo: Path) -> Check:
    if (repo / "VISION.md").is_file():
        return Check("vision", "ok", "VISION.md present")
    return Check("vision", "fail", "no VISION.md", VISION_HINT)


def check_no_mistakes(repo: Path) -> Check:
    path = repo / ".no-mistakes.yaml"
    if not path.is_file():
        return Check(
            "no_mistakes",
            "fail",
            "no .no-mistakes.yaml",
            "copy the shape from exercise-analyzer (commands.test: 'just test', no_ci, review.path_instructions); gate fields are read from the default branch",
        )
    text = _read(path)
    has_test_cmd = (
        re.search(r"^commands:\s*$[\s\S]*?^\s+test:", text, re.MULTILINE) is not None
    )
    has_test_instr = (
        re.search(r"^test:\s*$[\s\S]*?^\s+instructions:", text, re.MULTILINE)
        is not None
    )
    if not (has_test_cmd or has_test_instr):
        return Check(
            "no_mistakes",
            "warn",
            ".no-mistakes.yaml has no commands.test or test.instructions",
            "give the gate a test command, ideally `just test`",
        )
    via_just = "just test" in text
    return Check(
        "no_mistakes",
        "ok",
        ".no-mistakes.yaml; test "
        + ("= just test" if via_just else "configured (not via just)"),
    )


def check_stories(repo: Path) -> Check:
    stories = repo / "docs" / "stories"
    readme = stories / "README.md"
    journeys = (
        sorted(p.name for p in stories.glob("[0-9][0-9]-*.md"))
        if stories.is_dir()
        else []
    )
    if readme.is_file() and journeys:
        return Check(
            "stories", "ok", f"docs/stories: README + {len(journeys)} journey file(s)"
        )
    if readme.is_file() or journeys:
        return Check(
            "stories",
            "warn",
            f"docs/stories incomplete (README={readme.is_file()}, journeys={len(journeys)})",
            STORIES_HINT,
        )
    other = (
        [
            p
            for p in (repo / "docs").glob("*.md")
            if re.search(r"stor|needs", p.name, re.IGNORECASE)
        ]
        if (repo / "docs").is_dir()
        else []
    )
    if other:
        return Check(
            "stories",
            "warn",
            "stories in another layout: " + ", ".join(f"docs/{p.name}" for p in other),
            STORIES_HINT,
        )
    return Check("stories", "fail", "no user stories", STORIES_HINT)


def check_jtbd(repo: Path) -> Check:
    docs = repo / "docs"
    named = (
        [p for p in docs.glob("*.md") if _JTBD_RE.search(p.name)]
        if docs.is_dir()
        else []
    )
    if named:
        return Check("jtbd", "ok", "docs/" + named[0].name)
    for candidate in (
        repo / "VISION.md",
        docs / "stories" / "README.md",
        repo / "README.md",
    ):
        if candidate.is_file() and re.search(
            r"^#+\s.*" + _JTBD_RE.pattern,
            _read(candidate),
            re.IGNORECASE | re.MULTILINE,
        ):
            return Check("jtbd", "ok", f"section in {candidate.relative_to(repo)}")
    return Check("jtbd", "fail", "no jobs-to-be-done doc", JTBD_HINT)


def check_bug_reporting(repo: Path) -> Check:
    if is_bug_kit_itself(repo):
        return Check("bug_reporting", "na", "this is bug-kit")
    if is_ios(repo):
        if depends_on_bug_kit(repo):
            return Check("bug_reporting", "ok", "iOS; depends on idvorkin/bug-kit")
        return Check(
            "bug_reporting",
            "fail",
            "iOS app without bug-kit",
            "add https://github.com/idvorkin/bug-kit as a package dependency (bug-kit docs/integration.md)",
        )
    return Check(
        "bug_reporting",
        "gap",
        "not iOS; no bug-kit-like library exists for non-iOS repos yet",
        "known gap (Igor 2026-10-11): report only, do not build one here",
    )


def diagnose(repo: Path) -> dict:
    repo = repo.resolve()
    urls = remote_urls(repo)
    present = {
        "justfile": any(
            (repo / n).is_file() for n in ("justfile", "Justfile", ".justfile")
        ),
        "beads": (repo / ".beads").is_dir(),
        "stories": (repo / "docs" / "stories").is_dir(),
        "vision": (repo / "VISION.md").is_file(),
        "no_mistakes": (repo / ".no-mistakes.yaml").is_file(),
    }
    checks = [
        check_agents_md(repo),
        check_agents_pointers(repo, present),
        check_beads(repo),
        check_beads_local(repo),
        check_beads_hooks(repo),
        check_justfile(repo),
        check_scripts_via_just(repo),
        check_vision(repo),
        check_no_mistakes(repo),
        check_stories(repo),
        check_jtbd(repo),
        check_bug_reporting(repo),
    ]
    counts = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return {
        "repo": str(repo),
        "name": repo.name,
        "remotes": urls,
        "house_repo": house_repo(repo),
        "ios": is_ios(repo),
        "checks": [asdict(c) for c in checks],
        "counts": counts,
        "healthy": counts.get("fail", 0) == 0,
    }


def scan(root: Path) -> list[Path]:
    """Every direct child of `root` that is a git repo with an idvorkin / idvorkin-ai-tools remote."""
    repos = []
    for child in sorted(root.iterdir()):
        if (
            child.is_dir()
            and not child.name.startswith(".")
            and _git_dir(child) is not None
            and house_repo(child)
        ):
            repos.append(child)
    return repos


# ---------- rendering ----------

_GLYPH = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL", "na": "n/a ", "gap": "GAP "}


def render(report: dict) -> str:
    lines = [
        f"{report['name']}  ({report['repo']})  {'iOS' if report['ios'] else 'non-iOS'}  {'house' if report['house_repo'] else 'not a house repo'}"
    ]
    for check in report["checks"]:
        line = f"  {_GLYPH[check['status']]} {check['name']:<17} {check['detail']}"
        if check["fix"] and check["status"] in ("warn", "fail"):
            line += f"\n       fix: {check['fix']}"
        lines.append(line)
    summary = ", ".join(f"{k}={v}" for k, v in sorted(report["counts"].items()))
    lines.append(f"  -> {summary}")
    return "\n".join(lines)


_CELL = {"ok": "ok", "warn": "WARN", "fail": "FAIL", "na": "-", "gap": "GAP"}


def render_matrix(reports: list[dict]) -> str:
    """One row per repo, one column per check; the shape that stays readable at 50 repos."""
    names = [c["name"] for c in reports[0]["checks"]]
    repo_width = max(len(r["name"]) for r in reports)
    header = f"{'repo':<{repo_width}}  " + "  ".join(names)
    rows = [header, "-" * len(header)]
    for report in reports:
        cells = "  ".join(
            f"{_CELL[c['status']]:<{len(name)}}"
            for name, c in zip(names, report["checks"])
        )
        rows.append(f"{report['name']:<{repo_width}}  {cells}")
    rows.append(
        f"\n{len(reports)} repo(s); {sum(not r['healthy'] for r in reports)} with failures. Cells: ok, WARN, FAIL, GAP, - (n/a)."
    )
    return "\n".join(rows)


def _build_app():
    import typer

    app = typer.Typer(add_completion=False)

    @app.command()
    def main(
        repos: list[Path] = typer.Argument(None, help="Repos to audit (default: cwd)."),
        json_out: bool = typer.Option(
            False, "--json", help="Emit JSON instead of text."
        ),
        scan_root: Path | None = typer.Option(
            None, "--scan", help="Audit every house repo directly under this dir."
        ),
        matrix: bool = typer.Option(
            False, "--matrix", help="One compact status grid across repos (text mode)."
        ),
    ):
        """Audit repos against the house layout. Exit 1 when any check fails."""
        targets = list(repos or [])
        if scan_root is not None:
            targets += scan(scan_root)
        if not targets:
            targets = [Path.cwd()]
        reports = []
        for target in targets:
            if _git_dir(target) is None:
                typer.echo(f"{target}: not a git repo", err=True)
                raise typer.Exit(2)
            reports.append(diagnose(target))
        if json_out:
            print(json.dumps(reports if len(reports) > 1 else reports[0], indent=2))
        elif matrix and len(reports) > 1:
            print(render_matrix(reports))
        else:
            print("\n\n".join(render(r) for r in reports))
        raise typer.Exit(0 if all(r["healthy"] for r in reports) else 1)

    return app


if __name__ == "__main__":
    _build_app()()
