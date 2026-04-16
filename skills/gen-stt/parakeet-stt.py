#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# ABOUTME: Local speech-to-text via NVIDIA Parakeet TDT 0.6B ONNX — single file or batch.
# ABOUTME: Auto-transcodes non-16kHz/non-mono audio via ffmpeg; wraps every subprocess
# ABOUTME: with `nice -n 19 ionice -c 3 env OMP_NUM_THREADS=2 ORT_NUM_THREADS=2 MKL_NUM_THREADS=2`.
# ABOUTME: No API key — uses the local nemo-parakeet-tdt-0.6b-v2 ONNX model via onnx-asr.
#
# Single mode:
#   parakeet-stt.py --input clip.wav --output transcript.txt
#   parakeet-stt.py --input clip.ogg --json --output transcript.json
#
# Batch mode (parallel):
#   parakeet-stt.py --batch-dir /path/to/audio/dir
#   parakeet-stt.py --batch-dir /path/to/audio/dir --json --max-workers 2
#   parakeet-stt.py --batch-files a.wav b.m4a c.ogg
#
# Mandatory nice-wrap (BAKED IN — there is no code path that skips it):
#   nice -n 19 ionice -c 3 env OMP_NUM_THREADS=2 ORT_NUM_THREADS=2 MKL_NUM_THREADS=2 <cmd>
# Rationale: `nice` alone does NOT cap CPU; thread caps are the real knob.
# Empirically 2 threads is FASTER than onnxruntime's default on consumer CPUs
# because the default thrashes.

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTS = {".wav", ".ogg", ".oga", ".mp3", ".m4a", ".flac", ".aac", ".opus"}

DEFAULT_MODEL = "nemo-parakeet-tdt-0.6b-v2"

# Prefix applied to EVERY subprocess that touches ffmpeg / onnx-asr. Baked in
# so callers cannot accidentally skip it.
NICE_PREFIX: list[str] = [
    "nice", "-n", "19",
    "ionice", "-c", "3",
    "env",
    "OMP_NUM_THREADS=2",
    "ORT_NUM_THREADS=2",
    "MKL_NUM_THREADS=2",
]


@dataclass
class STTJob:
    input_path: Path
    output_path: Path  # final transcript destination
    json_mode: bool
    keep_wav: bool
    model: str


@dataclass
class STTResult:
    input_path: Path
    output_path: Path
    success: bool
    text: str | None
    error: str | None
    elapsed_s: float
    duration_s: float | None


def _niced_run(
    args: list[str],
    *,
    capture_output: bool = True,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command with the mandatory nice/ionice/thread-cap prefix.

    ALL heavy subprocesses (ffmpeg, uvx onnx-asr, ffprobe-on-transcoded-wav)
    must go through this helper so the nice-wrap is provably impossible to
    skip.
    """
    full = NICE_PREFIX + list(args)
    return subprocess.run(
        full,
        capture_output=capture_output,
        text=True,
        check=check,
    )


def _is_16k_mono_wav(path: Path) -> bool:
    """Return True iff `path` is already 16kHz mono 16-bit PCM WAV.

    Uses the stdlib `wave` module — avoids a subprocess for the common case.
    Returns False on any read error; the caller will then transcode.
    """
    try:
        with wave.open(str(path), "rb") as w:
            framerate = w.getframerate()
            channels = w.getnchannels()
            sampwidth = w.getsampwidth()  # bytes per sample
            comptype = w.getcomptype()
        return (
            framerate == 16000
            and channels == 1
            and sampwidth == 2  # 16-bit PCM
            and comptype == "NONE"
        )
    except (wave.Error, EOFError, OSError):
        return False


def _probe_duration_s(path: Path) -> float | None:
    """Best-effort duration probe.

    For native 16kHz-mono-pcm16 WAVs we can read it from the header via
    stdlib `wave`. Otherwise fall back to ffprobe. Returns None if both
    paths fail.
    """
    try:
        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
            framerate = w.getframerate()
            if framerate > 0:
                return round(frames / framerate, 2)
    except (wave.Error, EOFError, OSError):
        pass

    try:
        # ffprobe itself is cheap — no nice-wrap needed, but keeping it plain
        # also avoids the env-var cost for a sub-second call.
        res = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            return round(float(res.stdout.strip()), 2)
    except (OSError, ValueError):
        pass
    return None


def _transcode_to_16k_mono(input_path: Path, wav_path: Path) -> None:
    """Transcode any ffmpeg-readable audio to 16kHz mono 16-bit PCM WAV.

    Raises RuntimeError with ffmpeg stderr on failure.
    """
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    res = _niced_run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(input_path),
        "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
        str(wav_path),
    ])
    if res.returncode != 0:
        raise RuntimeError(
            f"ffmpeg transcode failed (rc={res.returncode}):\n{res.stderr.strip()}"
        )


def _run_onnx_asr(wav_path: Path, model: str) -> tuple[str, str]:
    """Invoke onnx-asr on an already-16kHz-mono WAV.

    Returns (transcript, stderr_tail). Raises RuntimeError on non-zero rc
    or empty transcript, including the captured stderr tail so failure
    modes (missing model, runtime errors) are visible.
    """
    res = _niced_run([
        "uvx",
        "--with", "onnxruntime",
        "--with", "huggingface_hub",
        "onnx-asr", model, str(wav_path),
    ])
    stderr_tail = (res.stderr or "")[-2048:]

    if res.returncode != 0:
        raise RuntimeError(
            f"onnx-asr exited {res.returncode}\n"
            f"--- onnx-asr stderr (last 2KB) ---\n{stderr_tail}"
        )

    transcript = (res.stdout or "").rstrip("\n")
    if not transcript:
        raise RuntimeError(
            "onnx-asr returned empty transcript\n"
            f"--- onnx-asr stderr (last 2KB) ---\n{stderr_tail}"
        )
    return transcript, stderr_tail


def transcribe_one(job: STTJob, work_root: Path) -> STTResult:
    """Transcribe a single audio file end-to-end.

    Handles transcode (skipping when the input is already 16kHz mono pcm16
    WAV), ASR invocation, and persistence. Returns STTResult; never raises
    on expected failure — captures the error string instead so batch mode
    can aggregate cleanly.
    """
    label = job.input_path.name
    print(f"Transcribing: {job.input_path}", file=sys.stderr)
    t0 = time.monotonic()

    # Per-job work dir so parallel jobs don't collide on the intermediate WAV.
    work_dir = work_root / f"job-{job.input_path.stem}-{int(t0 * 1000)}"
    work_dir.mkdir(parents=True, exist_ok=True)

    wav_file: Path
    transcoded = False
    try:
        if job.input_path.suffix.lower() == ".wav" and _is_16k_mono_wav(job.input_path):
            wav_file = job.input_path
        else:
            wav_file = work_dir / "input16k.wav"
            print(f"  [{label}] Transcoding to 16kHz mono WAV...", file=sys.stderr)
            _transcode_to_16k_mono(job.input_path, wav_file)
            transcoded = True

        duration_s = _probe_duration_s(wav_file)

        print(f"  [{label}] Running Parakeet ASR (model={job.model})...", file=sys.stderr)
        transcript, _stderr_tail = _run_onnx_asr(wav_file, job.model)

        elapsed_s = round(time.monotonic() - t0, 1)
        print(f"  [{label}] Transcribed in {elapsed_s}s", file=sys.stderr)

        # Persist output.
        job.output_path.parent.mkdir(parents=True, exist_ok=True)
        if job.json_mode:
            payload = {
                "text": transcript,
                "duration_s": duration_s,
                "model": job.model,
                "elapsed_s": elapsed_s,
            }
            persisted = json.dumps(payload, indent=2)
        else:
            persisted = transcript
        job.output_path.write_text(persisted + "\n")

        return STTResult(
            input_path=job.input_path,
            output_path=job.output_path,
            success=True,
            text=persisted,
            error=None,
            elapsed_s=elapsed_s,
            duration_s=duration_s,
        )
    except RuntimeError as e:
        elapsed_s = round(time.monotonic() - t0, 1)
        return STTResult(
            input_path=job.input_path,
            output_path=job.output_path,
            success=False,
            text=None,
            error=str(e),
            elapsed_s=elapsed_s,
            duration_s=None,
        )
    finally:
        if transcoded:
            if job.keep_wav:
                print(f"  [{label}] Kept intermediate WAV: {wav_file}", file=sys.stderr)
            else:
                try:
                    if wav_file.exists():
                        wav_file.unlink()
                    # Remove work_dir if now empty.
                    if work_dir.exists() and not any(work_dir.iterdir()):
                        work_dir.rmdir()
                except OSError:
                    pass


def discover_audio_files(dir_path: Path) -> list[Path]:
    """Return all audio files in `dir_path` (non-recursive), sorted."""
    if not dir_path.is_dir():
        return []
    return sorted(
        p for p in dir_path.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )


def default_output_path(
    input_path: Path, json_mode: bool, output_dir: Path | None
) -> Path:
    """Default transcript destination: `<stem>.txt` (or `.json`) alongside input.

    The audio suffix is replaced, not appended — `clip.wav` becomes `clip.txt`.
    """
    suffix = ".json" if json_mode else ".txt"
    target_dir = output_dir if output_dir is not None else input_path.parent
    return target_dir / f"{input_path.stem}{suffix}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Transcribe audio via local Parakeet TDT 0.6B ONNX "
            "(single or batch). No API key required."
        ),
    )

    # Single mode
    parser.add_argument(
        "--input",
        default=None,
        help="Single audio file to transcribe",
    )
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
        nargs="+",
        default=None,
        metavar="PATH",
        help="Space-separated list of audio files to transcribe in parallel",
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
        "--keep-wav",
        action="store_true",
        help="Retain the intermediate transcoded WAV file (debugging)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"onnx-asr model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help=(
            "Parallel batch worker count (default: 1 — empirically faster than 2 "
            "on consumer CPUs because OMP_NUM_THREADS=2 × N_workers still thrashes)."
        ),
    )

    args = parser.parse_args()

    modes = sum(bool(x) for x in (args.input, args.batch_dir, args.batch_files))
    if modes == 0:
        parser.error("Provide --input, --batch-dir, or --batch-files")
    if modes > 1:
        parser.error("Use exactly one of --input / --batch-dir / --batch-files")

    output_dir = Path(args.output_dir).expanduser() if args.output_dir else None

    # Shared work-root for intermediate transcoded WAVs.
    work_root = Path("/tmp") / f"parakeet-stt-{int(time.time() * 1000)}"
    work_root.mkdir(parents=True, exist_ok=True)

    try:
        # --- Single mode ---------------------------------------------------
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
                input_path=input_path,
                output_path=output_path,
                json_mode=args.json,
                keep_wav=args.keep_wav,
                model=args.model,
            )
            result = transcribe_one(job, work_root)
            if not result.success:
                print(f"Error: {result.error}", file=sys.stderr)
                sys.exit(1)
            # Stdout contract: transcript text (or JSON) → stdout, so the tool
            # composes in pipelines, e.g. `TEXT=$(parakeet-stt.py --input x.wav)`.
            # The saved path is echoed to stderr instead.
            print(result.text or "")
            print(f"Saved: {result.output_path}", file=sys.stderr)
            print(f"Transcribed in {result.elapsed_s}s", file=sys.stderr)
            return

        # --- Batch mode ----------------------------------------------------
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
            for raw in args.batch_files:
                raw = raw.strip()
                if not raw:
                    continue
                p = Path(raw).expanduser()
                if not p.exists():
                    print(f"Error: batch file not found: {p}", file=sys.stderr)
                    sys.exit(1)
                inputs.append(p)
            if not inputs:
                print(
                    "Error: --batch-files was empty",
                    file=sys.stderr,
                )
                sys.exit(2)

        if args.max_workers < 1:
            print(
                f"Error: --max-workers must be >= 1 (got {args.max_workers})",
                file=sys.stderr,
            )
            sys.exit(2)

        jobs: list[STTJob] = [
            STTJob(
                input_path=p,
                output_path=default_output_path(p, args.json, output_dir),
                json_mode=args.json,
                keep_wav=args.keep_wav,
                model=args.model,
            )
            for p in inputs
        ]

        print(
            f"Transcribing {len(jobs)} files in parallel "
            f"(max_workers={args.max_workers})...",
            file=sys.stderr,
        )

        failures: list[tuple[Path, str | None]] = []
        batch_t0 = time.monotonic()
        workers = min(args.max_workers, len(jobs))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(transcribe_one, j, work_root): j for j in jobs
            }
            for future in as_completed(futures):
                result = future.result()
                if result.success:
                    print(str(result.output_path))
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

        if args.json:
            summary = {
                "total": len(jobs),
                "succeeded": len(jobs) - len(failures),
                "batch_elapsed_s": batch_duration,
                "outputs": [str(j.output_path) for j in jobs],
            }
            print(json.dumps(summary))

        print(
            f"\nAll {len(jobs)} files transcribed ({batch_duration}s total)",
            file=sys.stderr,
        )
    finally:
        # Best-effort cleanup of shared work root (individual jobs clean
        # their own files unless --keep-wav was set).
        try:
            if work_root.exists() and not any(work_root.iterdir()):
                work_root.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    main()
