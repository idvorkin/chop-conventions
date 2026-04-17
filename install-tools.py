#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "typer>=0.12",
#     "rich>=13.0",
# ]
# ///
"""
Install chop-conventions skill CLIs via `uv tool install`.

Supersedes the symlink-based approach of PR #168. Each skill that ships a
CLI owns its own `pyproject.toml` and gets installed as a proper uv tool —
uv creates an isolated venv per package, symlinks entry-point binaries
into `~/.local/bin/`, and handles upgrades.

Typical usage:
    install-tools.py                 # install / upgrade all packages
    install-tools.py --dry-run       # enumerate planned actions, no writes
    install-tools.py --uninstall     # `uv tool uninstall` every known package
    install-tools.py --quiet         # minimal output (for scripting)

Design decisions (see PR body for the full writeup):

- **Per-skill packages, not one monorepo package.** Each tool's deps stay
  scoped to its own venv; gen-tts doesn't pull Telegram deps, watchdog
  doesn't pull TTS deps, diagnose stays stdlib-only.
- **Uninstall targets known package names, never touches `~/.local/bin`
  directly.** `uv tool uninstall chop-gen-tts` is the only safe path —
  arbitrary symlink sweeping is how people lose unrelated binaries.
- **The old `.py` shebang-driven shim files stay in place** as
  deprecation-period back-compat. Install-tools manages the packaged
  entries; the shims bootstrap the same code via PEP 723 `uv run --script`.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Package:
    """One installable uv tool — `pyproject.toml` lives at `<repo>/<relpath>`."""

    name: str  # PyPI distribution name (matches [project].name in pyproject.toml)
    relpath: str  # Repo-relative dir containing pyproject.toml
    entry_points: tuple[str, ...]  # Console-script names declared in [project.scripts]


# Add a new tool by appending an entry here. Missing `pyproject.toml` files
# surface as a hard error — no silent skip.
REGISTRY: tuple[Package, ...] = (
    Package(
        name="chop-gen-tts",
        relpath="skills/gen-tts",
        entry_points=("gen-tts",),
    ),
    Package(
        name="chop-telegram-tools",
        relpath="skills/harden-telegram",
        entry_points=("tg-doctor", "tg-watchdog"),
    ),
    Package(
        name="chop-up-to-date",
        relpath="skills/up-to-date",
        entry_points=("up-to-date-diag", "up-to-date-hook-trust"),
    ),
)


def repo_root() -> Path:
    """Resolve the chop-conventions repo root from this script's location.

    Falls back to the script's own dir if `git rev-parse` fails — the
    script always lives at the repo root, so that's a safe default.
    """
    script_dir = Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "-C", str(script_dir), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(out.stdout.strip()).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return script_dir


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run_uv_install(pkg: Package, root: Path, dry_run: bool, quiet: bool) -> int:
    """Execute `uv tool install --force --reinstall <path>` for one package.

    `--force --reinstall` makes this idempotent and picks up source edits —
    the common case when a developer is iterating on a tool. Returns the
    subprocess exit code (0 on success).
    """
    pkg_path = root / pkg.relpath
    pyproject = pkg_path / "pyproject.toml"
    if not pyproject.is_file():
        print(f"ERROR: {pyproject} not found")
        return 1
    cmd = ["uv", "tool", "install", "--force", "--reinstall", str(pkg_path)]
    if dry_run:
        print(f"DRY-RUN: {' '.join(cmd)}")
        return 0
    if not quiet:
        print(f"> {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode


def _run_uv_uninstall(pkg: Package, dry_run: bool, quiet: bool) -> int:
    """Execute `uv tool uninstall <pkg-name>` — key on the package name, never
    path — so removal is safe even if the source tree has moved.
    """
    cmd = ["uv", "tool", "uninstall", pkg.name]
    if dry_run:
        print(f"DRY-RUN: {' '.join(cmd)}")
        return 0
    if not quiet:
        print(f"> {' '.join(cmd)}")
    # `uv tool uninstall` exits nonzero if the tool isn't installed — that's
    # not a failure for our purposes (uninstalling something absent is a
    # no-op). Swallow it at the CLI level after inspecting stderr.
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 and "is not installed" in (result.stderr or ""):
        if not quiet:
            print("  (already uninstalled)")
        return 0
    if result.stdout and not quiet:
        print(result.stdout, end="")
    if result.stderr and result.returncode != 0:
        print(result.stderr, end="")
    return result.returncode


def install_all(dry_run: bool, quiet: bool) -> int:
    root = repo_root()
    failures: list[str] = []
    for pkg in REGISTRY:
        rc = _run_uv_install(pkg, root, dry_run, quiet)
        if rc != 0:
            failures.append(pkg.name)
    _report_which_check(dry_run, quiet)
    if failures:
        print(f"FAILED to install: {', '.join(failures)}")
        return 1
    return 0


def uninstall_all(dry_run: bool, quiet: bool) -> int:
    failures: list[str] = []
    for pkg in REGISTRY:
        rc = _run_uv_uninstall(pkg, dry_run, quiet)
        if rc != 0:
            failures.append(pkg.name)
    if failures:
        print(f"FAILED to uninstall: {', '.join(failures)}")
        return 1
    return 0


def _report_which_check(dry_run: bool, quiet: bool) -> None:
    """After an install, surface where each entry-point binary landed.

    This catches the single most common user bug: `~/.local/bin/` isn't on
    `$PATH`, so `uv tool install` succeeds but the binary is invisible.
    """
    if dry_run or quiet:
        return
    print()
    print("Entry points:")
    any_missing = False
    for pkg in REGISTRY:
        for entry in pkg.entry_points:
            resolved = _which(entry)
            if resolved:
                print(f"  {entry:<28} {resolved}")
            else:
                print(f"  {entry:<28} NOT ON $PATH")
                any_missing = True
    if any_missing:
        bin_dir = Path.home() / ".local" / "bin"
        if str(bin_dir) not in os.environ.get("PATH", ""):
            print(
                f"\nNOTE: {bin_dir} is not on $PATH in this shell — add it to "
                "your shell rc file so the binaries are callable."
            )


# --- CLI entry-point (lazy import of typer/rich so tests stay stdlib-pure) ---


def _build_app():  # pragma: no cover - thin CLI wrapper
    import typer

    app = typer.Typer(
        add_completion=False,
        help=(
            "Install chop-conventions skill CLIs via `uv tool install`. "
            "One isolated venv per package; upgrades via re-install."
        ),
    )

    @app.callback(invoke_without_command=True)
    def main(
        ctx: typer.Context,
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Print the commands that would run; no writes."
        ),
        uninstall: bool = typer.Option(
            False, "--uninstall", help="`uv tool uninstall` every registered package."
        ),
        quiet: bool = typer.Option(
            False, "--quiet", help="Minimal output (for scripting / hooks)."
        ),
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        if _which("uv") is None:
            print(
                "ERROR: `uv` not found on $PATH. Install uv first: "
                "https://docs.astral.sh/uv/"
            )
            raise typer.Exit(2)
        if uninstall:
            raise typer.Exit(uninstall_all(dry_run=dry_run, quiet=quiet))
        raise typer.Exit(install_all(dry_run=dry_run, quiet=quiet))

    return app


if __name__ == "__main__":
    _build_app()()
