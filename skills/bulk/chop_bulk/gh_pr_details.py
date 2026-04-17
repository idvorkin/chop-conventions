"""bulk-gh-pr-details — fetch N PRs' metadata in parallel.

Input: a list of `owner/repo#N` pairs (argv, --input-file JSON array,
or stdin JSON array).

Output: JSON array with
    {repo, number, title, state, mergeable, mergeStateStatus, url, error?}
one entry per input, in input order.

Under the hood:
    gh pr view --repo <r> <n> --json title,state,mergeable,mergeStateStatus,url

Each `gh` call runs on a ThreadPoolExecutor (default 8 workers). A
failure on one PR is captured inline as `{..., "error": "..."}`; the
batch never partial-fails.

Typer lives inside `_build_app()` so tests import the pure-function
layer without `ModuleNotFoundError` on systems lacking typer.
"""

import json
import re
import subprocess
from typing import Any

from .common import DEFAULT_MAX_WORKERS, emit_json, log, parallel_map, read_inputs

GH_PR_FIELDS = "title,state,mergeable,mergeStateStatus,url"

_SPEC_RE = re.compile(r"^\s*(?P<repo>[^#\s]+)#(?P<num>\d+)\s*$")


def parse_spec(spec: str) -> tuple[str, int]:
    """Parse an `owner/repo#N` spec into `(repo, number)`.

    Raises ValueError on malformed input so the worker can capture it
    as a per-item error.
    """
    m = _SPEC_RE.match(spec)
    if not m:
        raise ValueError(
            f"invalid PR spec {spec!r}: expected 'owner/repo#N' (e.g. 'idvorkin/chop-conventions#169')"
        )
    repo = m.group("repo")
    if repo.count("/") != 1:
        raise ValueError(
            f"invalid repo {repo!r} in spec {spec!r}: expected 'owner/repo'"
        )
    return repo, int(m.group("num"))


def fetch_pr(
    spec: str,
    *,
    run: Any = None,
) -> dict:
    """Fetch one PR's metadata via `gh pr view`.

    Returns a dict shaped `{repo, number, title, state, mergeable,
    mergeStateStatus, url}`. On any failure (parse, subprocess, JSON
    decode), returns `{repo, number, error}` — the batch never
    partial-fails.

    `run` is injected so tests can mock `subprocess.run` without
    monkey-patching the module global. Default of None resolves to
    `subprocess.run` at call time — so tests can `patch("chop_bulk.
    gh_pr_details.subprocess.run", ...)` and have the patched symbol
    picked up on every call (patching the function's default arg
    won't work because defaults bind at def-time).
    """
    if run is None:
        run = subprocess.run
    try:
        repo, number = parse_spec(spec)
    except ValueError as exc:
        return {"repo": None, "number": None, "error": str(exc)}
    cmd = [
        "gh",
        "pr",
        "view",
        "--repo",
        repo,
        str(number),
        "--json",
        GH_PR_FIELDS,
    ]
    try:
        result = run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"repo": repo, "number": number, "error": f"{type(exc).__name__}: {exc}"}
    if result.returncode != 0:
        return {
            "repo": repo,
            "number": number,
            "error": (result.stderr or "").strip() or f"gh exited {result.returncode}",
        }
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {"repo": repo, "number": number, "error": f"invalid gh JSON: {exc}"}
    return {
        "repo": repo,
        "number": number,
        "title": payload.get("title"),
        "state": payload.get("state"),
        "mergeable": payload.get("mergeable"),
        "mergeStateStatus": payload.get("mergeStateStatus"),
        "url": payload.get("url"),
    }


def run_cli(
    specs: list[str],
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
    pretty: bool = False,
) -> int:
    """Fan out, collect, emit. Returns process exit code."""
    if not specs:
        log("error: no PR specs provided")
        return 2
    log(f"fetching {len(specs)} PR(s) with max_workers={max_workers}")
    results = parallel_map(specs, fetch_pr, max_workers=max_workers)
    emit_json(results, pretty=pretty)
    return 0


def _build_app():  # pragma: no cover — thin Typer wrapper
    import typer

    app = typer.Typer(
        add_completion=False,
        help=(
            "Fetch N PRs' metadata in parallel. Accepts positional owner/repo#N specs, "
            "--input-file, or stdin JSON."
        ),
    )

    @app.callback(invoke_without_command=True)
    def cli(
        ctx: typer.Context,
        specs: list[str] = typer.Argument(
            None,
            help="PR specs as 'owner/repo#N'. Omit to read from --input-file or stdin.",
            metavar="[owner/repo#N ...]",
        ),
        input_file: str = typer.Option(
            None,
            "--input-file",
            help="Path to a JSON file containing an array of 'owner/repo#N' strings.",
        ),
        max_workers: int = typer.Option(
            DEFAULT_MAX_WORKERS,
            "--max-workers",
            min=1,
            help="Max parallel `gh pr view` calls.",
        ),
        pretty: bool = typer.Option(
            False, "--pretty", help="Pretty-print the JSON output."
        ),
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        try:
            items = read_inputs(specs, input_file)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            log(f"error: {exc}")
            raise typer.Exit(2)
        raise typer.Exit(run_cli(items, max_workers=max_workers, pretty=pretty))

    return app


def main() -> None:
    _build_app()()


if __name__ == "__main__":
    main()
