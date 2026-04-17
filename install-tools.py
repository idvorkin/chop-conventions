#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "typer>=0.12",
#     "rich>=13.0",
# ]
# ///
"""
Install chop-conventions skill CLIs as symlinks in ~/.local/bin.

Registry of (source path relative to repo root, target bin name) pairs at
REGISTRY below. Safe to re-run (idempotent). --uninstall only removes
symlinks whose targets actually resolve into this repo.

Typical usage:
    install-tools.py                 # install / refresh symlinks
    install-tools.py --dry-run       # show planned changes, no writes
    install-tools.py --uninstall     # remove this repo's symlinks
    install-tools.py --quiet         # minimal output (for git hooks)

Also auto-discovers uv-shebang scripts under skills/**/tools/ that aren't in
REGISTRY and warns — catches drift when a new tool is added but forgotten.
"""

# NOTE: intentionally NOT using `from __future__ import annotations` — Typer
# reads runtime annotations to build the CLI, and stringified annotations
# fail to resolve when typer is lazy-imported inside _build_app().

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


# (source path relative to repo root, target name in ~/.local/bin)
REGISTRY: list[tuple[str, str]] = [
    ("skills/gen-tts/generate-tts.py", "gen-tts"),
    ("skills/harden-telegram/tools/telegram_debug.py", "tg-doctor"),
    ("skills/harden-telegram/tools/watchdog.py", "tg-watchdog"),
    ("skills/up-to-date/diagnose.py", "up-to-date-diag"),
    ("skills/up-to-date/hook_trust.py", "up-to-date-hook-trust"),
]


@dataclass
class PlanEntry:
    source: Path
    target: Path
    name: str
    action: str  # "create", "update", "ok", "skip-missing", "skip-not-exec"
    note: str = ""


def repo_root() -> Path:
    """Resolve the chop-conventions repo root from this script's location."""
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
        # Fallback: assume script sits at repo root
        return script_dir


def bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


def registry_source_set(root: Path) -> set[Path]:
    return {(root / rel).resolve() for rel, _ in REGISTRY}


def discover_drift(root: Path) -> list[Path]:
    """Find executable uv-shebang scripts under skills/**/tools/ not in REGISTRY."""
    registered = registry_source_set(root)
    drift: list[Path] = []
    tools_glob = root.glob("skills/*/tools/*.py")
    for candidate in tools_glob:
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if resolved in registered:
            continue
        # Must have executable bit
        if not os.access(candidate, os.X_OK):
            continue
        # Must have uv-script shebang
        try:
            with candidate.open("r", encoding="utf-8", errors="replace") as f:
                first_line = f.readline()
        except OSError:
            continue
        if "uv run" in first_line:
            drift.append(resolved)
    return sorted(drift)


def plan_install(root: Path, bin: Path) -> list[PlanEntry]:
    plan: list[PlanEntry] = []
    for rel, name in REGISTRY:
        source = (root / rel).resolve()
        target = bin / name

        if not source.exists():
            plan.append(
                PlanEntry(
                    source=source,
                    target=target,
                    name=name,
                    action="skip-missing",
                    note=f"source does not exist: {source}",
                )
            )
            continue

        exec_ok = os.access(source, os.X_OK)
        note = "" if exec_ok else "source missing executable bit"

        if target.is_symlink():
            try:
                existing = Path(os.readlink(target))
                existing_resolved = (target.parent / existing).resolve()
            except OSError:
                existing_resolved = None
            if existing_resolved == source:
                plan.append(
                    PlanEntry(
                        source=source,
                        target=target,
                        name=name,
                        action="ok",
                        note=note,
                    )
                )
                continue
            plan.append(
                PlanEntry(
                    source=source,
                    target=target,
                    name=name,
                    action="update",
                    note=note,
                )
            )
            continue

        if target.exists():
            plan.append(
                PlanEntry(
                    source=source,
                    target=target,
                    name=name,
                    action="skip-not-exec",
                    note=f"{target} exists and is not a symlink — leaving alone",
                )
            )
            continue

        plan.append(
            PlanEntry(
                source=source, target=target, name=name, action="create", note=note
            )
        )
    return plan


def apply_install(plan: list[PlanEntry], dry_run: bool) -> list[PlanEntry]:
    """Mutate filesystem per plan; returns the same list."""
    if not dry_run:
        bin_dir().mkdir(parents=True, exist_ok=True)
    for entry in plan:
        if entry.action in {"create", "update"}:
            if dry_run:
                continue
            # Use ln -sf semantics: remove-then-symlink
            try:
                if entry.target.is_symlink() or entry.target.exists():
                    entry.target.unlink()
                entry.target.symlink_to(entry.source)
            except OSError as e:
                entry.note = (entry.note + f"; symlink failed: {e}").lstrip("; ")
                entry.action = "fail"
    return plan


def plan_uninstall(root: Path, bin: Path) -> list[PlanEntry]:
    """Only remove symlinks whose resolved target is inside this repo."""
    root_resolved = root.resolve()
    plan: list[PlanEntry] = []
    if not bin.exists():
        return plan
    for entry_path in sorted(bin.iterdir()):
        if not entry_path.is_symlink():
            continue
        try:
            link_target = Path(os.readlink(entry_path))
            resolved = (entry_path.parent / link_target).resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            continue
        plan.append(
            PlanEntry(
                source=resolved,
                target=entry_path,
                name=entry_path.name,
                action="remove",
            )
        )
    return plan


def apply_uninstall(plan: list[PlanEntry], dry_run: bool) -> list[PlanEntry]:
    for entry in plan:
        if dry_run:
            continue
        try:
            entry.target.unlink()
        except OSError as e:
            entry.note = f"unlink failed: {e}"
            entry.action = "fail"
    return plan


def which(name: str) -> str | None:
    return shutil.which(name)


# --- CLI entry-point (lazy import of typer/rich so tests stay stdlib-pure) ---


def _build_app():  # pragma: no cover - thin CLI wrapper
    import typer
    from rich.console import Console
    from rich.table import Table

    app = typer.Typer(
        add_completion=False,
        help="Install chop-conventions skill CLIs as ~/.local/bin symlinks.",
    )

    @app.callback(invoke_without_command=True)
    def main(
        ctx: typer.Context,
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show planned changes without writing."
        ),
        uninstall: bool = typer.Option(
            False, "--uninstall", help="Remove symlinks that resolve into this repo."
        ),
        quiet: bool = typer.Option(
            False, "--quiet", help="Minimal output (for hook use)."
        ),
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        console = Console()
        root = repo_root()
        bin = bin_dir()

        if uninstall:
            plan = plan_uninstall(root, bin)
            apply_uninstall(plan, dry_run)
            if quiet:
                # One line per change
                for entry in plan:
                    marker = "WOULD" if dry_run else "REMOVED"
                    if entry.action == "fail":
                        marker = "FAIL"
                    console.print(f"{marker} {entry.target}")
                if not plan:
                    console.print("No repo-owned symlinks to remove.")
                return

            if not plan:
                console.print(
                    f"[yellow]No symlinks in {bin} point into {root}.[/yellow]"
                )
                return
            table = Table(title="Uninstall plan" + (" (dry-run)" if dry_run else ""))
            table.add_column("Action")
            table.add_column("Target")
            table.add_column("Resolves to")
            table.add_column("Note")
            for entry in plan:
                action_word = "WOULD REMOVE" if dry_run else entry.action.upper()
                table.add_row(
                    action_word, str(entry.target), str(entry.source), entry.note
                )
            console.print(table)
            return

        # Install mode
        plan = plan_install(root, bin)
        apply_install(plan, dry_run)

        # Drift detection
        drift = discover_drift(root)

        if quiet:
            changed = [e for e in plan if e.action in {"create", "update"}]
            for entry in changed:
                marker = "WOULD" if dry_run else "LINKED"
                console.print(f"{marker} {entry.target} -> {entry.source}")
            warns = [e for e in plan if e.action in {"skip-missing", "fail"}]
            for entry in warns:
                console.print(
                    f"[yellow]WARN {entry.name}: {entry.note}[/yellow]", soft_wrap=True
                )
            if drift:
                console.print(
                    f"[yellow]WARN {len(drift)} unlinked tool(s) found under skills/**/tools/ — add to REGISTRY or rename.[/yellow]"
                )
            return

        table = Table(
            title="chop-conventions install-tools" + (" (dry-run)" if dry_run else "")
        )
        table.add_column("Name")
        table.add_column("Action")
        table.add_column("Target")
        table.add_column("Source")
        table.add_column("Note")
        for entry in plan:
            action_word = entry.action
            if dry_run and action_word in {"create", "update"}:
                action_word = f"would-{action_word}"
            table.add_row(
                entry.name,
                action_word,
                str(entry.target),
                str(entry.source.relative_to(root))
                if str(entry.source).startswith(str(root))
                else str(entry.source),
                entry.note,
            )
        console.print(table)

        # which-check for successfully-installed names
        if not dry_run:
            ok_names = [
                e.name
                for e in plan
                if e.action in {"create", "update", "ok"} and e.source.exists()
            ]
            if ok_names:
                wtable = Table(title="which check")
                wtable.add_column("Name")
                wtable.add_column("Resolved")
                for name in ok_names:
                    resolved = which(name) or "[red]NOT ON PATH[/red]"
                    wtable.add_row(name, str(resolved))
                console.print(wtable)
                if bin.as_posix() not in os.environ.get("PATH", ""):
                    console.print(
                        f"[yellow]NOTE:[/yellow] {bin} is not on $PATH in this shell — add it to your rc file."
                    )

        if drift:
            console.print(
                f"\n[yellow]Drift warning:[/yellow] {len(drift)} executable uv-shebang script(s) under skills/**/tools/ not in REGISTRY:"
            )
            for path in drift:
                try:
                    rel = path.relative_to(root)
                except ValueError:
                    rel = path
                console.print(f"  - {rel}")
            console.print("[yellow]Add to REGISTRY in install-tools.py.[/yellow]")

    return app


if __name__ == "__main__":
    _build_app()()
