#!/usr/bin/env python3
# ABOUTME: Wrapper around parakeet-stt.sh for local speech-to-text (single or batch).
# ABOUTME: No API key needed — uses the local NVIDIA Parakeet TDT 0.6B ONNX model.
# ABOUTME: In batch mode, writes .txt (or .json) alongside each input file.
#
# Single mode:
#   generate-stt.py --input clip.wav --output transcript.txt
#   generate-stt.py --input clip.ogg --json --output transcript.json
#
# Batch mode (parallel):
#   generate-stt.py --batch-dir /path/to/audio/dir
#   generate-stt.py --batch-dir /path/to/audio/dir --json --max-workers 2
#   generate-stt.py --batch-files a.wav,b.m4a,c.ogg

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTS = {".wav", ".ogg", ".oga", ".mp3", ".m4a", ".flac", ".aac", ".opus"}


@dataclass
class STTJob:
    input_path: str
    output_path: str  # final transcript destination (alongside input by default)
    json_mode: bool


@dataclass
class STTResult:
    input_path: str
    output_path: str
    success: bool
    text: str | None
    error: str | None
    elapsed_s: float


def resolve_chop_root():
    """Resolve CHOP_ROOT from this script's location in the repo."""
    script_dir = Path(__file__).resolve().parent
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        cwd=script_dir,
    )
    if result.returncode != 0:
        # Fallback: assume <script_dir>/../.. is the repo root.
        return script_dir.parent.parent
    return Path(result.stdout.strip())


def transcribe_one(job: STTJob, parakeet_script: str) -> STTResult:
    """Run the bash helper against one audio file and capture its stdout."""
    cmd = ["bash", parakeet_script, job.input_path]
    if job.json_mode:
        cmd.append("--json")

    print(f"Transcribing: {job.input_path}", file=sys.stderr)
    t0 = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed_s = round(time.monotonic() - t0, 1)

    # Forward the bash helper's stderr (progress lines) to our stderr for visibility.
    if result.stderr:
        # Indent slightly so batch output is readable.
        for line in result.stderr.rstrip().splitlines():
            print(f"  [{Path(job.input_path).name}] {line}", file=sys.stderr)

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or "unknown error"
        return STTResult(
            input_path=job.input_path,
            output_path=job.output_path,
            success=False,
            text=None,
            error=err,
            elapsed_s=elapsed_s,
        )

    text = result.stdout.rstrip("\n")
    # Persist to disk at the requested path.
    Path(job.output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(job.output_path).write_text(text + "\n")

    return STTResult(
        input_path=job.input_path,
        output_path=job.output_path,
        success=True,
        text=text,
        error=None,
        elapsed_s=elapsed_s,
    )


def discover_audio_files(dir_path: Path) -> list[Path]:
    """Return all files in dir_path whose suffix matches AUDIO_EXTS (non-recursive)."""
    if not dir_path.is_dir():
        return []
    return sorted(
        p for p in dir_path.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )


def default_output_path(input_path: Path, json_mode: bool, output_dir: Path | None) -> Path:
    """Compute the transcript destination for an input audio file.

    By default, writes <input>.txt (or .json) in the same directory. If
    --output-dir is given, writes <input stem>.txt in that dir instead.
    """
    suffix = ".json" if json_mode else ".txt"
    if output_dir is not None:
        return output_dir / f"{input_path.stem}{suffix}"
    return input_path.with_suffix(input_path.suffix + suffix)


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe audio via local Parakeet TDT 0.6B ONNX (single or batch). No API key required.",
    )

    # Single mode
    parser.add_argument("--input", default=None, help="Single audio file to transcribe")
    parser.add_argument(
        "--output",
        default=None,
        help="Output transcript path (single mode). Defaults to <input>.txt / .json.",
    )

    # Batch mode
    parser.add_argument(
        "--batch-dir",
        default=None,
        metavar="DIR",
        help="Directory of audio files to transcribe in parallel",
    )
    parser.add_argument(
        "--batch-files",
        default=None,
        metavar="CSV",
        help="Comma-separated list of audio files to transcribe in parallel",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help="Batch mode: write transcripts to this dir instead of alongside inputs",
    )

    # Shared
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit {text, duration_s, model, elapsed_s} JSON instead of plain text",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=2,
        help="Parallel batch worker count (default: 2; keep low — Parakeet is CPU-heavy)",
    )

    args = parser.parse_args()

    modes = sum(bool(x) for x in (args.input, args.batch_dir, args.batch_files))
    if modes == 0:
        parser.error("Provide --input, --batch-dir, or --batch-files")
    if modes > 1:
        parser.error("Use exactly one of --input / --batch-dir / --batch-files")

    chop_root = resolve_chop_root()
    skill_dir = chop_root / "skills" / "gen-stt"
    parakeet_script = str(skill_dir / "parakeet-stt.sh")
    if not Path(parakeet_script).exists():
        print(
            f"Error: parakeet-stt.sh not found at {parakeet_script}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Make sure the bash helper is executable (handles fresh checkouts).
    os.chmod(parakeet_script, 0o755)

    output_dir = Path(args.output_dir).expanduser() if args.output_dir else None

    # --- Single mode --------------------------------------------------------
    if args.input:
        input_path = Path(args.input).expanduser()
        if not input_path.exists():
            print(f"Error: input file not found: {input_path}", file=sys.stderr)
            sys.exit(1)
        output_path = (
            Path(args.output).expanduser()
            if args.output
            else default_output_path(input_path, args.json, output_dir)
        )
        job = STTJob(
            input_path=str(input_path),
            output_path=str(output_path),
            json_mode=args.json,
        )
        result = transcribe_one(job, parakeet_script)
        if not result.success:
            print(f"Error: {result.error}", file=sys.stderr)
            sys.exit(1)
        print(result.output_path)
        print(f"Transcribed in {result.elapsed_s}s", file=sys.stderr)
        return

    # --- Batch mode ---------------------------------------------------------
    inputs: list[Path] = []
    if args.batch_dir:
        batch_dir = Path(args.batch_dir).expanduser()
        inputs = discover_audio_files(batch_dir)
        if not inputs:
            print(
                f"Error: no audio files found under {batch_dir} "
                f"(accepted: {sorted(AUDIO_EXTS)})",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        for raw in args.batch_files.split(","):
            raw = raw.strip()
            if not raw:
                continue
            p = Path(raw).expanduser()
            if not p.exists():
                print(f"Error: batch file not found: {p}", file=sys.stderr)
                sys.exit(1)
            inputs.append(p)

    jobs: list[STTJob] = [
        STTJob(
            input_path=str(p),
            output_path=str(default_output_path(p, args.json, output_dir)),
            json_mode=args.json,
        )
        for p in inputs
    ]

    print(
        f"Transcribing {len(jobs)} files in parallel "
        f"(max_workers={args.max_workers})...",
        file=sys.stderr,
    )

    failures: list[tuple[str, str | None]] = []
    batch_t0 = time.monotonic()
    workers = min(args.max_workers, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(transcribe_one, j, parakeet_script): j for j in jobs}
        for future in as_completed(futures):
            result = future.result()
            if result.success:
                print(result.output_path)
            else:
                failures.append((result.input_path, result.error))
                print(
                    f"FAILED: {result.input_path} — {result.error}",
                    file=sys.stderr,
                )

    batch_duration = round(time.monotonic() - batch_t0, 1)

    if failures:
        print(
            f"\n{len(failures)}/{len(jobs)} failed ({batch_duration}s total)",
            file=sys.stderr,
        )
        sys.exit(1)

    # Optional: emit a batch summary JSON if --json requested.
    if args.json:
        summary = {
            "total": len(jobs),
            "succeeded": len(jobs) - len(failures),
            "batch_elapsed_s": batch_duration,
            "outputs": [j.output_path for j in jobs],
        }
        print(json.dumps(summary))

    print(
        f"\nAll {len(jobs)} files transcribed ({batch_duration}s total)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
