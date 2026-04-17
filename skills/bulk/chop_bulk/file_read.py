"""bulk-file-read — read N text files in parallel.

Input: a list of absolute file paths (argv, --input-file JSON, or stdin JSON).

Output: JSON mapping path -> metadata:
    {path: {size_bytes, content_utf8_or_null, error_or_null}}

Files larger than `--max-bytes` (default 1 MB) are NOT loaded: the
entry carries `{size_bytes, content_utf8_or_null: null,
error: "skipped: exceeds max-bytes"}`. Non-UTF-8 files emit
`content_utf8_or_null: null` with the decode error.

Purpose: quick multi-file text inventory without individual `Read`
calls. Skip-rather-than-load on big files keeps the output JSON sane.
"""

import json
import os
from pathlib import Path

from .common import DEFAULT_MAX_WORKERS, emit_json, log, parallel_map, read_inputs

DEFAULT_MAX_BYTES = 1_000_000  # 1 MB


def _read_one(
    path_str: str,
    max_bytes: int,
) -> dict:
    """Open/read one file. Always returns a dict; never raises."""
    result: dict = {
        "path": path_str,
        "size_bytes": None,
        "content_utf8_or_null": None,
        "error_or_null": None,
    }
    try:
        p = Path(path_str)
    except TypeError as exc:
        result["error_or_null"] = f"invalid path: {exc}"
        return result
    try:
        stat = os.stat(p)
    except OSError as exc:
        result["error_or_null"] = f"stat failed: {exc}"
        return result
    result["size_bytes"] = stat.st_size
    if stat.st_size > max_bytes:
        result["error_or_null"] = (
            f"skipped: exceeds max-bytes ({stat.st_size} > {max_bytes})"
        )
        return result
    try:
        with open(p, "rb") as f:
            raw = f.read()
    except OSError as exc:
        result["error_or_null"] = f"read failed: {exc}"
        return result
    try:
        result["content_utf8_or_null"] = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        result["error_or_null"] = f"not UTF-8: {exc}"
    return result


def read_file_worker(path_str: str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    """Top-level worker fn. `parallel_map` expects one positional arg, so the
    `max_bytes` default is baked in here; the CLI passes it as a closure.
    """
    return _read_one(path_str, max_bytes)


def run_cli(
    file_paths: list[str],
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    pretty: bool = False,
) -> int:
    if not file_paths:
        log("error: no file paths provided")
        return 2
    log(
        f"reading {len(file_paths)} file(s) with max_workers={max_workers} "
        f"max_bytes={max_bytes}"
    )

    # Closure so parallel_map's single-arg worker contract is preserved.
    def worker(path_str: str) -> dict:
        return _read_one(path_str, max_bytes)

    results = parallel_map(file_paths, worker, max_workers=max_workers)
    # Normalize into `{path: {...}}` mapping while still capturing
    # duplicate paths (later wins, with a synthetic flag).
    out: dict[str, dict] = {}
    for entry in results:
        path = entry.get("path")
        if path is None:
            # Worker-raise fallback path: common.parallel_map wrapped it.
            out.setdefault("_error", []).append(entry)  # type: ignore[arg-type]
            continue
        trimmed = {
            "size_bytes": entry.get("size_bytes"),
            "content_utf8_or_null": entry.get("content_utf8_or_null"),
            "error_or_null": entry.get("error_or_null"),
        }
        out[path] = trimmed
    emit_json(out, pretty=pretty)
    return 0


def _build_app():  # pragma: no cover
    import typer

    app = typer.Typer(
        add_completion=False,
        help=(
            "Read N text files in parallel. Accepts positional absolute paths, "
            "--input-file, or stdin JSON. Files above --max-bytes are skipped."
        ),
    )

    @app.callback(invoke_without_command=True)
    def cli(
        ctx: typer.Context,
        file_paths: list[str] = typer.Argument(
            None,
            help="Absolute file paths. Omit to read from --input-file or stdin.",
            metavar="[FILE-PATH ...]",
        ),
        input_file: str = typer.Option(
            None, "--input-file", help="JSON file: array of absolute file paths."
        ),
        max_workers: int = typer.Option(
            DEFAULT_MAX_WORKERS, "--max-workers", min=1,
            help="Max parallel file reads.",
        ),
        max_bytes: int = typer.Option(
            DEFAULT_MAX_BYTES, "--max-bytes", min=1,
            help="Skip (don't load) files larger than this. Default 1 MB.",
        ),
        pretty: bool = typer.Option(False, "--pretty", help="Pretty-print JSON output."),
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        try:
            items = read_inputs(file_paths, input_file)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            log(f"error: {exc}")
            raise typer.Exit(2)
        raise typer.Exit(
            run_cli(
                items,
                max_workers=max_workers,
                max_bytes=max_bytes,
                pretty=pretty,
            )
        )

    return app


def main() -> None:
    _build_app()()


if __name__ == "__main__":
    main()
