"""bulk-bd-show — fetch N beads' metadata in parallel.

Input: a list of bead IDs (argv, --input-file JSON, or stdin JSON).

Output: JSON array, one entry per bead:
    {id, title, status, priority, type, parent, blocks, blocked_by, error?}

Under the hood:
    bd show <id> --json

**Array-vs-object quirk**: `bd show --json` returns a single-element
array when the bead is found. Normalize with
`payload[0] if isinstance(payload, list) else payload`.
`parent`/`blocks`/`blocked_by` are derived from the bead's dependencies
list (see `normalize_bead`).
"""

import json
import subprocess
from typing import Any

from .common import DEFAULT_MAX_WORKERS, emit_json, log, parallel_map, read_inputs


def normalize_bead(raw: Any, requested_id: str) -> dict:
    """Pick the fields we surface from a raw `bd show` JSON payload.

    `bd show` returns a JSON array; the caller has already unwrapped it.
    Dependency links live under `raw['dependencies']` as a list of
    `{type, source, target}` entries. We re-slice that into
    `parent` (single string), `blocks`, and `blocked_by` lists.
    """
    if not isinstance(raw, dict):
        return {
            "id": requested_id,
            "error": f"bd show returned non-object: {type(raw).__name__}",
        }
    deps = raw.get("dependencies") or []
    parent: str | None = None
    blocks: list[str] = []
    blocked_by: list[str] = []
    for d in deps if isinstance(deps, list) else []:
        if not isinstance(d, dict):
            continue
        dtype = d.get("type")
        source = d.get("source")
        target = d.get("target")
        # `parent-child`: the *parent* is whichever end is NOT this bead.
        if dtype == "parent-child":
            other = source if target == raw.get("id") else target
            if other and parent is None:
                parent = other
        elif dtype == "blocks":
            # This bead (source) blocks target; or target blocks us.
            if source == raw.get("id") and target:
                blocks.append(target)
            elif target == raw.get("id") and source:
                blocked_by.append(source)
    return {
        "id": raw.get("id", requested_id),
        "title": raw.get("title"),
        "status": raw.get("status"),
        "priority": raw.get("priority"),
        "type": raw.get("issue_type") or raw.get("type"),
        "parent": parent,
        "blocks": blocks,
        "blocked_by": blocked_by,
    }


def fetch_bead(
    bead_id: str,
    *,
    run: Any = None,
) -> dict:
    """Fetch one bead via `bd show <id> --json`.

    Handles the `bd show` array-vs-object normalization. Failures
    capture as `error` in the result.

    `run=None` resolves to `subprocess.run` at call time so tests can
    `patch('chop_bulk.bd_show.subprocess.run', ...)` and have every
    call pick up the patched symbol. (Defaults bind at def-time; the
    module-global re-lookup is what makes patching work.)
    """
    if run is None:
        run = subprocess.run
    bid = str(bead_id).strip()
    if not bid:
        return {"id": bead_id, "error": "empty bead id"}
    cmd = ["bd", "show", bid, "--json"]
    try:
        result = run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"id": bid, "error": f"{type(exc).__name__}: {exc}"}
    if result.returncode != 0:
        return {
            "id": bid,
            "error": (result.stderr or "").strip() or f"bd exited {result.returncode}",
        }
    try:
        payload = json.loads(result.stdout or "null")
    except json.JSONDecodeError as exc:
        return {"id": bid, "error": f"invalid bd JSON: {exc}"}
    # bd show returns a JSON array with one element; unwrap.
    if isinstance(payload, list):
        if not payload:
            return {"id": bid, "error": "bd show returned empty array"}
        payload = payload[0]
    return normalize_bead(payload, bid)


def run_cli(
    bead_ids: list[str],
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
    pretty: bool = False,
) -> int:
    if not bead_ids:
        log("error: no bead ids provided")
        return 2
    log(f"fetching {len(bead_ids)} bead(s) with max_workers={max_workers}")
    results = parallel_map(bead_ids, fetch_bead, max_workers=max_workers)
    emit_json(results, pretty=pretty)
    return 0


def _build_app():  # pragma: no cover
    import typer

    app = typer.Typer(
        add_completion=False,
        help=(
            "Fetch N beads' metadata in parallel. Accepts positional bead IDs, "
            "--input-file, or stdin JSON."
        ),
    )

    @app.callback(invoke_without_command=True)
    def cli(
        ctx: typer.Context,
        bead_ids: list[str] = typer.Argument(
            None,
            help="Bead IDs. Omit to read from --input-file or stdin.",
            metavar="[BEAD-ID ...]",
        ),
        input_file: str = typer.Option(
            None, "--input-file", help="JSON file: array of bead IDs."
        ),
        max_workers: int = typer.Option(
            DEFAULT_MAX_WORKERS, "--max-workers", min=1,
            help="Max parallel `bd show` calls.",
        ),
        pretty: bool = typer.Option(False, "--pretty", help="Pretty-print JSON output."),
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        try:
            items = read_inputs(bead_ids, input_file)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            log(f"error: {exc}")
            raise typer.Exit(2)
        raise typer.Exit(run_cli(items, max_workers=max_workers, pretty=pretty))

    return app


def main() -> None:
    _build_app()()


if __name__ == "__main__":
    main()
