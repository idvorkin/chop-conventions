"""Shared helpers for the bulk-* CLIs.

Every bulk tool follows the same shape:
    1. Read a list of inputs — positional argv, or `--input-file path.json`,
       or stdin JSON when neither is given.
    2. Fan out on `ThreadPoolExecutor(max_workers=N)` with a per-item
       worker function.
    3. Capture per-item failures as `{..., "error": "..."}` in the result —
       never fail the whole batch.
    4. Emit the result list as JSON to stdout; log progress to stderr.

Kept stdlib-only so tests and pre-commit hooks (no uv) can import this
module directly. Typer lives only in each tool's `_build_app()`.
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable

DEFAULT_MAX_WORKERS = 8


def read_inputs(
    positional: list[str] | None,
    input_file: str | None,
) -> list[Any]:
    """Resolve the input list from (positional args | input-file | stdin).

    Priority order:
      1. `positional` — non-empty list of strings from argv.
      2. `input_file` — path to a JSON file containing a list.
      3. stdin — parsed as JSON, must decode to a list.

    Returns a Python list. Raises ValueError on malformed input so each
    CLI can surface a clean error message to the user.
    """
    if positional:
        return list(positional)
    if input_file:
        with open(input_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError(
                f"--input-file {input_file!r} must contain a JSON array; "
                f"got {type(data).__name__}"
            )
        return data
    # Fall back to stdin JSON. If stdin is a TTY we have nothing to read —
    # surface a clear error rather than blocking forever.
    if sys.stdin.isatty():
        raise ValueError(
            "no inputs: pass positional args, --input-file PATH, or pipe a JSON array on stdin"
        )
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("stdin was empty; pass positional args or --input-file PATH")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(
            f"stdin JSON must be an array; got {type(data).__name__}"
        )
    return data


def parallel_map(
    items: Iterable[Any],
    worker: Callable[[Any], dict],
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> list[dict]:
    """Run `worker(item)` for every item on a ThreadPoolExecutor.

    Worker functions must return a dict (one-object-per-input) and should
    capture their own exceptions as `{"error": "..."}` fields. If a worker
    raises anyway, the exception is caught here and emitted as an error
    entry so the batch never partial-fails.

    Order is preserved — results come out in the same order as `items`.
    """
    items_list = list(items)
    max_workers = max(1, min(max_workers, max(1, len(items_list))))
    results: list[dict | None] = [None] * len(items_list)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(worker, item): idx for idx, item in enumerate(items_list)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:  # noqa: BLE001 — tool boundary
                results[idx] = {"error": f"{type(exc).__name__}: {exc}"}
    # Remove the None placeholders (mypy/pyright happiness) — every slot
    # is filled by the loop above.
    return [r if r is not None else {"error": "no result"} for r in results]


def emit_json(payload: Any, pretty: bool) -> None:
    """Write `payload` as JSON to stdout with a trailing newline."""
    if pretty:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=False))
    else:
        sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")
    sys.stdout.flush()


def log(msg: str) -> None:
    """Write a progress/diagnostic line to stderr."""
    sys.stderr.write(msg)
    if not msg.endswith("\n"):
        sys.stderr.write("\n")
    sys.stderr.flush()
