"""bulk-up-to-date — run the up-to-date diagnose against N repos in parallel.

Input: a list of absolute repo paths (argv, --input-file JSON, or stdin JSON).

Output: JSON array, one entry per repo:
    [{repo, diagnose_json, error?}, ...]

Under the hood — prefers the packaged CLI introduced by PR #169:

    up-to-date-diag    (on $PATH after `uv tool install chop-up-to-date`)

Falls back to the script shebang invocation if the packaged CLI is not
installed:

    ~/.claude/skills/up-to-date/diagnose.py

The discovery happens once at module load — later calls don't re-probe.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .common import DEFAULT_MAX_WORKERS, emit_json, log, parallel_map, read_inputs


def resolve_diagnose_cmd() -> list[str]:
    """Pick the best available diagnose invocation.

    Priority:
      1. `up-to-date-diag` on $PATH (PR #169 packaged CLI).
      2. `~/.claude/skills/up-to-date/diagnose.py` (shebang script).

    Returns a prefix command list ready to take extra args appended.
    Raises FileNotFoundError if neither option is available.
    """
    packaged = shutil.which("up-to-date-diag")
    if packaged:
        return [packaged]
    home = Path(os.environ.get("HOME", "/"))
    script = home / ".claude" / "skills" / "up-to-date" / "diagnose.py"
    if script.is_file() and os.access(script, os.X_OK):
        return [str(script)]
    raise FileNotFoundError(
        "neither `up-to-date-diag` on $PATH nor "
        "~/.claude/skills/up-to-date/diagnose.py is available"
    )


def diagnose_repo(
    repo_path: str,
    *,
    run: Any = None,
    resolve: Any = resolve_diagnose_cmd,
) -> dict:
    """Invoke the resolved diagnose CLI with `cwd=<repo>`.

    `diagnose.py` prints a single JSON blob to stdout and doesn't take
    any path arg — it introspects the current working directory. We
    pass `cwd=repo_path` so the diagnosis applies to the right repo.

    `run=None` resolves to `subprocess.run` at call time so tests can
    patch the module global.
    """
    if run is None:
        run = subprocess.run
    p = Path(repo_path).expanduser()
    if not p.is_dir():
        return {"repo": repo_path, "diagnose_json": None, "error": "not a directory"}
    if not (p / ".git").exists():
        return {
            "repo": str(p),
            "diagnose_json": None,
            "error": "not a git repository (no .git present)",
        }
    try:
        cmd = resolve()
    except FileNotFoundError as exc:
        return {"repo": str(p), "diagnose_json": None, "error": str(exc)}
    try:
        result = run(cmd, capture_output=True, text=True, timeout=120, cwd=str(p))
    except Exception as exc:  # noqa: BLE001
        return {
            "repo": str(p),
            "diagnose_json": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if result.returncode != 0:
        return {
            "repo": str(p),
            "diagnose_json": None,
            "error": (result.stderr or "").strip() or f"diagnose exited {result.returncode}",
        }
    try:
        payload = json.loads(result.stdout or "null")
    except json.JSONDecodeError as exc:
        return {
            "repo": str(p),
            "diagnose_json": None,
            "error": f"invalid diagnose JSON: {exc}",
        }
    return {"repo": str(p), "diagnose_json": payload}


def run_cli(
    repo_paths: list[str],
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
    pretty: bool = False,
) -> int:
    if not repo_paths:
        log("error: no repo paths provided")
        return 2
    log(f"diagnosing {len(repo_paths)} repo(s) with max_workers={max_workers}")
    results = parallel_map(repo_paths, diagnose_repo, max_workers=max_workers)
    emit_json(results, pretty=pretty)
    return 0


def _build_app():  # pragma: no cover
    import typer

    app = typer.Typer(
        add_completion=False,
        help=(
            "Run the up-to-date diagnose against N repos in parallel. Accepts "
            "positional absolute repo paths, --input-file, or stdin JSON."
        ),
    )

    @app.callback(invoke_without_command=True)
    def cli(
        ctx: typer.Context,
        repo_paths: list[str] = typer.Argument(
            None,
            help="Absolute repo paths. Omit to read from --input-file or stdin.",
            metavar="[REPO-PATH ...]",
        ),
        input_file: str = typer.Option(
            None, "--input-file", help="JSON file: array of absolute repo paths."
        ),
        max_workers: int = typer.Option(
            DEFAULT_MAX_WORKERS, "--max-workers", min=1,
            help="Max parallel diagnose calls.",
        ),
        pretty: bool = typer.Option(False, "--pretty", help="Pretty-print JSON output."),
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        try:
            items = read_inputs(repo_paths, input_file)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            log(f"error: {exc}")
            raise typer.Exit(2)
        raise typer.Exit(run_cli(items, max_workers=max_workers, pretty=pretty))

    return app


def main() -> None:
    _build_app()()


if __name__ == "__main__":
    main()
