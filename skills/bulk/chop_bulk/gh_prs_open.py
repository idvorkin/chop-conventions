"""bulk-gh-prs-open — list open PRs across N repos in parallel.

Input: a list of `owner/repo` slugs (argv, --input-file JSON, or stdin JSON).

Output: JSON array, one entry per repo:
    {repo, open_prs: [{number, title, headRefName}], error?}

Under the hood:
    gh pr list --repo <r> --state open --json number,title,headRefName
"""

import json
import subprocess
from typing import Any

from .common import DEFAULT_MAX_WORKERS, emit_json, log, parallel_map, read_inputs

GH_PR_LIST_FIELDS = "number,title,headRefName"


def validate_slug(slug: str) -> str:
    """Return the slug if it looks like `owner/repo`, else raise ValueError."""
    s = slug.strip()
    if not s or s.count("/") != 1 or any(p == "" for p in s.split("/")):
        raise ValueError(
            f"invalid repo slug {slug!r}: expected 'owner/repo'"
        )
    return s


def fetch_open_prs(
    slug: str,
    *,
    run: Any = None,
) -> dict:
    """Fetch open PRs for one `owner/repo`. Failures capture as `error`.

    `run=None` resolves to `subprocess.run` at call time so tests can
    `patch('chop_bulk.gh_prs_open.subprocess.run', ...)` and have every
    call pick up the patched symbol.
    """
    if run is None:
        run = subprocess.run
    try:
        repo = validate_slug(slug)
    except ValueError as exc:
        return {"repo": slug, "open_prs": [], "error": str(exc)}
    cmd = [
        "gh",
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--json",
        GH_PR_LIST_FIELDS,
    ]
    try:
        result = run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"repo": repo, "open_prs": [], "error": f"{type(exc).__name__}: {exc}"}
    if result.returncode != 0:
        return {
            "repo": repo,
            "open_prs": [],
            "error": (result.stderr or "").strip() or f"gh exited {result.returncode}",
        }
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        return {"repo": repo, "open_prs": [], "error": f"invalid gh JSON: {exc}"}
    if not isinstance(payload, list):
        return {
            "repo": repo,
            "open_prs": [],
            "error": f"gh returned non-list JSON: {type(payload).__name__}",
        }
    open_prs = [
        {
            "number": item.get("number"),
            "title": item.get("title"),
            "headRefName": item.get("headRefName"),
        }
        for item in payload
    ]
    return {"repo": repo, "open_prs": open_prs}


def run_cli(
    slugs: list[str],
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
    pretty: bool = False,
) -> int:
    if not slugs:
        log("error: no repo slugs provided")
        return 2
    log(f"listing open PRs across {len(slugs)} repo(s) with max_workers={max_workers}")
    results = parallel_map(slugs, fetch_open_prs, max_workers=max_workers)
    emit_json(results, pretty=pretty)
    return 0


def _build_app():  # pragma: no cover
    import typer

    app = typer.Typer(
        add_completion=False,
        help=(
            "List open PRs across N repos in parallel. Accepts positional "
            "owner/repo slugs, --input-file, or stdin JSON."
        ),
    )

    @app.callback(invoke_without_command=True)
    def cli(
        ctx: typer.Context,
        slugs: list[str] = typer.Argument(
            None,
            help="Repo slugs as 'owner/repo'. Omit to read from --input-file or stdin.",
            metavar="[owner/repo ...]",
        ),
        input_file: str = typer.Option(
            None, "--input-file", help="JSON file: array of 'owner/repo' strings."
        ),
        max_workers: int = typer.Option(
            DEFAULT_MAX_WORKERS, "--max-workers", min=1,
            help="Max parallel `gh pr list` calls.",
        ),
        pretty: bool = typer.Option(False, "--pretty", help="Pretty-print JSON output."),
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        try:
            items = read_inputs(slugs, input_file)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            log(f"error: {exc}")
            raise typer.Exit(2)
        raise typer.Exit(run_cli(items, max_workers=max_workers, pretty=pretty))

    return app


def main() -> None:
    _build_app()()


if __name__ == "__main__":
    main()
