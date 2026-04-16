#!/usr/bin/env python3
# ABOUTME: Wrapper around gemini-tts.sh for TTS generation (single or batch).
# ABOUTME: Handles env loading, voice preset resolution, and parallel batch execution.
# ABOUTME: In batch mode, augments the input JSON with _duration_s debug fields.
#
# Single mode:
#   generate-tts.py --text "Hello [short pause] world." --output greeting.wav
#   generate-tts.py --text-file script.txt --output read.wav --voice Kore
#
# Batch mode (parallel):
#   generate-tts.py --batch lines.json
#
# lines.json format:
#   [{"text": "...", "output": "file.wav", "voice": "Kore"}, ...]

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

DEFAULT_VOICE = "Kore"  # Firm, observer-coach tone; matches Larry's vibe.


@dataclass
class TTSJob:
    text: str
    output: str
    voice: str


@dataclass
class TTSResult:
    output: str
    success: bool
    error: str | None
    duration_s: float


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
        print("Error: Could not resolve CHOP_ROOT via git", file=sys.stderr)
        sys.exit(1)
    return Path(result.stdout.strip())


def load_env(env_file="~/.env"):
    """Load KEY=VALUE pairs from env file into os.environ (does not override existing)."""
    path = Path(env_file).expanduser()
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def read_default_voice(skill_dir: Path) -> str:
    """Read default voice from tts-voice.txt if present; else fall back to DEFAULT_VOICE."""
    voice_file = skill_dir / "tts-voice.txt"
    if voice_file.exists():
        for line in voice_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return DEFAULT_VOICE


def resolve_voice_preset(skill_dir: Path, voice_arg: str | None) -> str:
    """Resolve --voice against voices/<name>.txt presets, raw voice name, or the default file.

    Resolution order:
      1. If voice_arg is None -> read tts-voice.txt (default).
      2. If voices/<voice_arg>.txt exists -> read first non-comment line.
      3. Else treat voice_arg as a literal Gemini voice name (e.g. "Kore").
    """
    if voice_arg is None:
        return read_default_voice(skill_dir)

    preset = skill_dir / "voices" / f"{voice_arg}.txt"
    if preset.exists():
        for line in preset.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return voice_arg


def generate_one(job: TTSJob, gemini_script: str) -> TTSResult:
    """Generate a single audio file via the bash helper."""
    cmd = [
        "bash",
        gemini_script,
        job.text,
        "--output",
        job.output,
        "--voice",
        job.voice,
    ]

    print(f"Generating: {job.output} (voice={job.voice})", file=sys.stderr)
    t0 = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True)
    duration_s = round(time.monotonic() - t0, 1)

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or "unknown error"
        return TTSResult(output=job.output, success=False, error=err, duration_s=duration_s)

    if result.stderr:
        print(result.stderr, file=sys.stderr)

    return TTSResult(output=job.output, success=True, error=None, duration_s=duration_s)


def main():
    parser = argparse.ArgumentParser(
        description="Generate speech audio via Gemini 3.1 Flash TTS (single or batch)",
    )

    # Batch mode
    parser.add_argument(
        "--batch",
        metavar="JSON",
        default=None,
        help="JSON file with jobs to generate in parallel",
    )

    # Single mode
    parser.add_argument("--text", default=None, help="Text to synthesize")
    parser.add_argument(
        "--text-file",
        default=None,
        help="Read text from file (alternative to --text)",
    )
    parser.add_argument(
        "--output", default=None, help="Output WAV filename (e.g., greeting.wav)"
    )

    # Shared options
    parser.add_argument(
        "--voice",
        default=None,
        help="Voice preset name (resolves voices/<name>.txt) or literal Gemini voice (e.g. Kore, Puck, Charon). Default: read tts-voice.txt",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Parallel batch worker count (default: 4)",
    )

    args = parser.parse_args()

    is_batch = args.batch is not None
    is_single = args.text is not None or args.text_file is not None
    if not is_batch and not is_single:
        parser.error("Provide --batch JSON or --text/--text-file plus --output")
    if is_batch and is_single:
        parser.error("Cannot combine --batch with --text/--text-file")
    if is_single and not args.output:
        parser.error("Single mode requires --output")
    if args.text and args.text_file:
        parser.error("Use either --text or --text-file, not both")

    chop_root = resolve_chop_root()
    skill_dir = chop_root / "skills" / "gen-tts"
    gemini_script = str(skill_dir / "gemini-tts.sh")

    load_env()

    if not os.environ.get("GOOGLE_API_KEY"):
        print(
            "Error: GOOGLE_API_KEY not found in environment or ~/.env",
            file=sys.stderr,
        )
        sys.exit(1)

    if is_single:
        if args.text_file:
            text = Path(args.text_file).read_text().strip()
        else:
            text = args.text
        voice = resolve_voice_preset(skill_dir, args.voice)
        job = TTSJob(text=text, output=args.output, voice=voice)
        result = generate_one(job, gemini_script)
        if not result.success:
            print(f"Error: {result.error}", file=sys.stderr)
            sys.exit(1)
        print(result.output)
        print(f"Generated in {result.duration_s}s", file=sys.stderr)
        return

    # Batch mode
    batch_path = Path(args.batch)
    if not batch_path.exists():
        print(f"Error: Batch file not found: {batch_path}", file=sys.stderr)
        sys.exit(1)

    with open(batch_path) as f:
        raw_jobs = json.load(f)

    if not raw_jobs:
        print("Error: No jobs in batch file", file=sys.stderr)
        sys.exit(1)

    # Map output filename -> raw dict for augmenting with debug info
    job_by_output = {d["output"]: d for d in raw_jobs}
    jobs: list[TTSJob] = []
    for d in raw_jobs:
        voice = resolve_voice_preset(skill_dir, d.get("voice"))
        jobs.append(TTSJob(text=d["text"], output=d["output"], voice=voice))

    print(
        f"Generating {len(jobs)} audio clips in parallel (max_workers={args.max_workers})...",
        file=sys.stderr,
    )
    failures: list[tuple[str, str | None]] = []

    batch_t0 = time.monotonic()
    workers = min(args.max_workers, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(generate_one, j, gemini_script): j for j in jobs}
        for future in as_completed(futures):
            result = future.result()
            if result.output in job_by_output:
                job_by_output[result.output]["_duration_s"] = result.duration_s
            if result.success:
                print(result.output)
            else:
                failures.append((result.output, result.error))
                print(f"FAILED: {result.output} — {result.error}", file=sys.stderr)

    batch_duration = round(time.monotonic() - batch_t0, 1)

    with open(batch_path, "w") as f:
        json.dump(raw_jobs, f, indent=2)

    if failures:
        print(
            f"\n{len(failures)}/{len(jobs)} failed ({batch_duration}s total)",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        f"\nAll {len(jobs)} clips generated ({batch_duration}s total)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
