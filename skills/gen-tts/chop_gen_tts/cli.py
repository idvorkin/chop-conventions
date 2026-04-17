# ABOUTME: Python-only Gemini 3.1 Flash TTS generator — single clip or parallel batch.
# ABOUTME: Stdlib-only core (urllib, wave, struct); Typer for CLI. No requests/httpx/jq/curl deps.
# ABOUTME: In batch mode, augments the input JSON with _duration_s debug fields.
#
# Packaged entry point (registered via `uv tool install ./skills/gen-tts/`):
#   gen-tts single --text "Hello [short pause] world." --output greeting.wav
#   gen-tts single --text-file script.txt --output read.wav --voice Kore
#   gen-tts single --style-preset freud --speed 1.8 --text "..." --output out.wav
#   echo "piped text" | gen-tts single --output out.wav
#
# Batch mode (parallel):
#   gen-tts batch lines.json
#   gen-tts batch lines.json --speed 1.2   # default for jobs missing 'speed'
#
# lines.json format:
#   [{"text": "...", "output": "file.wav", "voice": "Kore", "speed": 1.0}, ...]

import base64
import json
import os
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

DEFAULT_VOICE = "Charon"  # Deeper storyteller baritone; matches Larry's vibe.
# Kept in sync with tts-voice.txt. If you change the shipped default voice,
# change both and mention it in SKILL.md's voice table.

DEFAULT_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-3.1-flash-tts-preview:generateContent"
)

# Gemini TTS returns PCM: 16-bit signed little-endian, 24kHz, mono by default.
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_CHANNELS = 1
DEFAULT_BITS_PER_SAMPLE = 16


# ----- Data types ---------------------------------------------------------


@dataclass
class TTSJob:
    text: str
    output: str
    voice: str
    style_prompt: str | None = None  # Director's-notes prefix; see resolve_style()
    speed: float = 1.0  # Post-process tempo multiplier via ffmpeg atempo. 1.0 = no change.


@dataclass
class TTSResult:
    output: str
    success: bool
    error: str | None
    duration_s: float


# ----- Environment --------------------------------------------------------


def resolve_skill_dir() -> Path:
    """Return the gen-tts skill directory — canonical location for tts-voice.txt and voices/.

    Resolution order:
      1. $GEN_TTS_SKILL_DIR (explicit override for unusual layouts).
      2. $CHOP_CONVENTIONS_ROOT/skills/gen-tts/ if CHOP_CONVENTIONS_ROOT is set
         and contains the expected files.
      3. The repo-default path ~/gits/chop-conventions/skills/gen-tts/.
      4. The package's own parent dir (fallback for source-tree invocation —
         useful when the package is imported directly, not `uv tool install`ed).

    Having an env-var-first policy makes the installed CLI portable: the
    user can point at any checkout via `GEN_TTS_SKILL_DIR=…` without
    reinstalling. When unset, the conventional path works without ceremony.
    """
    env_override = os.environ.get("GEN_TTS_SKILL_DIR")
    if env_override:
        return Path(env_override).expanduser()
    chop_root = os.environ.get("CHOP_CONVENTIONS_ROOT")
    if chop_root:
        candidate = Path(chop_root).expanduser() / "skills" / "gen-tts"
        if (candidate / "tts-voice.txt").is_file() or (candidate / "voices").is_dir():
            return candidate
    default = Path.home() / "gits" / "chop-conventions" / "skills" / "gen-tts"
    if (default / "tts-voice.txt").is_file() or (default / "voices").is_dir():
        return default
    # Last resort: the module's parent dir. Works when the package is run
    # from the source tree (e.g. `python -m chop_gen_tts.cli`).
    return Path(__file__).resolve().parent.parent


def load_env(env_file: str = "~/.env") -> None:
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


# ----- Voice + style resolution ------------------------------------------


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


def resolve_style(
    skill_dir: Path,
    style_prompt: str | None,
    style_preset: str | None,
    style_file: str | None,
) -> str | None:
    """Resolve a style directive (director's-notes prefix) from CLI args.

    Precedence (first non-None wins): --style-prompt > --style-preset > --style-file.
    --style-preset NAME looks up voices/<NAME>.txt and returns its non-comment
    body as a single paragraph. This is how multi-line preset files like
    voices/freud.txt and voices/soprano.txt ship — they're style directives,
    not Gemini voice IDs.
    """
    if style_prompt:
        return style_prompt
    if style_preset:
        preset_path = skill_dir / "voices" / f"{style_preset}.txt"
        if not preset_path.exists():
            raise FileNotFoundError(f"Style preset not found: {preset_path}")
        return _read_style_body(preset_path)
    if style_file:
        path = Path(style_file).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Style file not found: {path}")
        return _read_style_body(path)
    return None


def _read_style_body(path: Path) -> str:
    """Return the file body with '#' comment lines and blank lines stripped,
    collapsed to a single space-separated paragraph."""
    body_lines: list[str] = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        body_lines.append(stripped)
    return " ".join(body_lines)


def compose_prompt(text: str, style: str | None) -> str:
    """Prepend the style directive to the text as a director's-note prefix.

    Matches the shape the bash layer used — a blank line between the style
    paragraph and `Spoken text: <text>`. Gemini honors this as director's
    notes separate from the spoken content.
    """
    if not style:
        return text
    return f"{style}\n\nSpoken text: {text}"


# ----- HTTP + response parsing -------------------------------------------


class TTSError(Exception):
    """Caller-facing TTS failure — message should be printable as-is."""


def _extract_sample_rate(mime_type: str) -> int:
    """Parse sample rate from a mime type like 'audio/L16;codec=pcm;rate=24000'."""
    if "rate=" not in mime_type:
        return DEFAULT_SAMPLE_RATE
    tail = mime_type.split("rate=", 1)[1]
    candidate = tail.split(";", 1)[0].strip()
    if candidate.isdigit():
        return int(candidate)
    return DEFAULT_SAMPLE_RATE


def _find_audio_part(response: dict) -> tuple[str, str] | None:
    """Return (base64_data, mime_type) for the first inlineData audio part, or None."""
    candidates = response.get("candidates") or []
    if not candidates:
        return None
    content = candidates[0].get("content") or {}
    for part in content.get("parts") or []:
        inline = part.get("inlineData") or {}
        mime = inline.get("mimeType") or ""
        if mime.startswith("audio/"):
            data = inline.get("data") or ""
            if data:
                return data, mime
    return None


def _format_api_error(response: dict) -> str | None:
    """If the response describes an error condition, return a user-facing message.

    Distinguishes three error shapes the bash layer surfaced separately:
      1. Top-level `error.message` — classic 4xx/5xx API error.
      2. `promptFeedback.blockReason` — input-side safety filter.
      3. `candidates[0].finishReason` of SAFETY / PROHIBITED_CONTENT /
         RECITATION / LANGUAGE / OTHER — output-side safety / truncation.
    """
    error = response.get("error") or {}
    if error.get("message"):
        return f"API Error: {error['message']}"

    block_reason = (response.get("promptFeedback") or {}).get("blockReason")
    candidates = response.get("candidates") or []
    finish_reason = candidates[0].get("finishReason") if candidates else None

    # finishReason check — only error on the abnormal ones.
    if finish_reason and finish_reason not in ("STOP", "MAX_TOKENS"):
        msg = (
            f"Gemini TTS refused to generate audio (finishReason={finish_reason})"
        )
        if block_reason:
            msg += f"\n  promptFeedback.blockReason={block_reason}"
        msg += (
            "\n  Most common cause: prosody tags like [excited] / [whisper]"
            "\n  combined with certain content trip the safety filter."
            "\n  Try retrying without the directorial tag, or rephrase."
        )
        return msg

    if block_reason:
        msg = f"Gemini TTS refused the prompt (blockReason={block_reason})"
        ratings = (response.get("promptFeedback") or {}).get("safetyRatings")
        if ratings:
            msg += f"\n  safetyRatings={json.dumps(ratings, separators=(',', ':'))}"
        return msg

    return None


def _post_tts(api_url: str, payload: bytes, api_key: str, timeout: float) -> tuple[int, bytes]:
    """POST JSON payload, return (http_status, body_bytes).

    Auth via x-goog-api-key header (not ?key= query string) so the key never
    leaks into URL access logs, shell traces, or proxy logs.
    """
    req = urllib.request.Request(
        api_url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        # HTTPError is a Response-like — read body for the error parser.
        body = e.read() if hasattr(e, "read") else b""
        return e.code, body


def call_gemini_tts(
    text: str,
    voice: str,
    api_url: str,
    api_key: str,
    timeout: float = 120.0,
) -> tuple[bytes, int]:
    """Call the Gemini TTS API and return (raw_pcm_bytes, sample_rate).

    Retries once on transient network errors / HTTP 5xx, matching what the
    bash layer did. Raises TTSError with a caller-facing message on failure.
    """
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice},
                }
            },
        },
    }
    payload = json.dumps(body).encode("utf-8")

    last_err: str | None = None
    status = 0
    raw = b""
    for attempt in (1, 2):
        try:
            status, raw = _post_tts(api_url, payload, api_key, timeout)
            # Retry on transient 5xx only; 4xx is a hard error.
            if 500 <= status < 600 and attempt == 1:
                print(
                    f"Transient HTTP {status}, retrying after 2s...",
                    file=sys.stderr,
                )
                time.sleep(2.0)
                continue
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = str(e)
            if attempt == 1:
                print(
                    f"Transient network error ({last_err}), retrying after 2s...",
                    file=sys.stderr,
                )
                time.sleep(2.0)
                continue
            raise TTSError(f"HTTP request failed after retry: {last_err}") from e

    # Parse body as JSON; the API always returns JSON even on error.
    try:
        response = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        preview = raw[:2048].decode("utf-8", errors="replace")
        raise TTSError(f"HTTP {status}: non-JSON response\n{preview}")

    # Structured-error checks (priority over raw status when body parses).
    api_err = _format_api_error(response)
    if api_err:
        raise TTSError(api_err)

    # Non-2xx with no structured error — raise with raw body preview.
    if not (200 <= status < 300):
        preview = json.dumps(response)[:2048]
        raise TTSError(f"HTTP {status} from Gemini TTS\n{preview}")

    # Extract audio.
    found = _find_audio_part(response)
    if not found:
        finish_reason = ""
        candidates = response.get("candidates") or []
        if candidates:
            finish_reason = candidates[0].get("finishReason") or ""
        preview = json.dumps(response)[:2048]
        raise TTSError(
            f"No audio data in response (finishReason={finish_reason or '<none>'})\n{preview}"
        )

    b64_data, mime_type = found
    try:
        pcm_bytes = base64.b64decode(b64_data)
    except Exception as e:
        raise TTSError(f"Failed to base64-decode audio: {e}")

    if len(pcm_bytes) < 1000:
        raise TTSError(
            f"Decoded audio is only {len(pcm_bytes)} bytes — likely truncated or empty"
        )

    return pcm_bytes, _extract_sample_rate(mime_type)


# ----- WAV assembly ------------------------------------------------------


def pcm_to_wav(
    pcm_bytes: bytes,
    output_path: str,
    sample_rate: int,
    channels: int = DEFAULT_CHANNELS,
    bits_per_sample: int = DEFAULT_BITS_PER_SAMPLE,
) -> None:
    """Wrap raw PCM in a canonical little-endian WAV header.

    WAV header is 44 bytes for PCM:
      RIFF<size>WAVEfmt <16><1><channels><rate><byterate><blockalign><bps>data<size>
    Using struct directly (not `wave` module) so we control the exact bytes
    and match what the bash layer produced.
    """
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm_bytes)

    header = b"RIFF" + struct.pack("<I", data_size + 36) + b"WAVE"
    header += b"fmt " + struct.pack(
        "<IHHIIHH",
        16,  # fmt chunk size
        1,  # PCM format
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
    )
    header += b"data" + struct.pack("<I", data_size)

    with open(output_path, "wb") as f:
        f.write(header + pcm_bytes)


# ----- Post-processing ---------------------------------------------------


def atempo_filter_chain(speed: float) -> str:
    """Build an ffmpeg -filter:a expression for the requested speed.

    ffmpeg's atempo filter accepts 0.5–100.0, but quality degrades outside
    [0.5, 2.0]. For speeds outside that band we chain atempo filters so each
    individual step stays in range.
    """
    if speed < 0.5 or speed > 100.0:
        raise ValueError(f"speed must be in [0.5, 100.0], got {speed}")
    if 0.5 <= speed <= 2.0:
        return f"atempo={speed}"
    # Chain 2.0-multipliers until the remainder fits, then apply it.
    filters: list[str] = []
    remainder = speed
    while remainder > 2.0:
        filters.append("atempo=2.0")
        remainder /= 2.0
    while remainder < 0.5:
        filters.append("atempo=0.5")
        remainder *= 2.0
    filters.append(f"atempo={remainder}")
    return ",".join(filters)


def post_process_speed(wav_path: str, speed: float) -> None:
    """Apply an ffmpeg atempo pass in-place. No-op for speed == 1.0."""
    if speed == 1.0:
        return
    tmp_path = wav_path + ".speed.tmp.wav"
    cmd = [
        "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
        "-i", wav_path,
        "-filter:a", atempo_filter_chain(speed),
        tmp_path,
    ]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:
        raise TTSError(
            "ffmpeg not found on PATH — required for --speed post-processing"
        ) from exc
    except subprocess.CalledProcessError as exc:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise TTSError(f"ffmpeg atempo failed (speed={speed}): {exc}") from exc
    os.replace(tmp_path, wav_path)


# ----- Single-job driver -------------------------------------------------


def generate_one(job: TTSJob, api_url: str, api_key: str) -> TTSResult:
    """Generate a single audio file end-to-end (API call + WAV wrap)."""
    style_tag = " +style" if job.style_prompt else ""
    print(
        f"Generating: {job.output} (voice={job.voice}{style_tag})",
        file=sys.stderr,
    )
    t0 = time.monotonic()
    try:
        text = compose_prompt(job.text, job.style_prompt)
        pcm_bytes, sample_rate = call_gemini_tts(text, job.voice, api_url, api_key)
        pcm_to_wav(pcm_bytes, job.output, sample_rate)
        if job.speed != 1.0:
            post_process_speed(job.output, job.speed)
    except TTSError as e:
        duration_s = round(time.monotonic() - t0, 1)
        return TTSResult(
            output=job.output, success=False, error=str(e), duration_s=duration_s
        )
    except OSError as e:
        duration_s = round(time.monotonic() - t0, 1)
        return TTSResult(
            output=job.output,
            success=False,
            error=f"I/O error writing {job.output}: {e}",
            duration_s=duration_s,
        )

    duration_s = round(time.monotonic() - t0, 1)
    wav_bytes = os.path.getsize(job.output)
    byte_rate = sample_rate * DEFAULT_CHANNELS * DEFAULT_BITS_PER_SAMPLE // 8
    audio_seconds = round(len(pcm_bytes) / byte_rate, 2) if byte_rate else 0.0
    print(
        f"Saved: {job.output} ({wav_bytes} bytes, ~{audio_seconds}s, "
        f"{sample_rate}Hz mono {DEFAULT_BITS_PER_SAMPLE}-bit)",
        file=sys.stderr,
    )
    return TTSResult(output=job.output, success=True, error=None, duration_s=duration_s)


# ----- CLI ---------------------------------------------------------------


def _build_app():
    """Wire Typer app. Called only from __main__ so tests skip the typer import."""
    import typer
    from typing import Optional

    app = typer.Typer(
        help="Generate speech audio via Gemini 3.1 Flash TTS (single or batch).",
        add_completion=False,
        no_args_is_help=True,
    )

    @app.command()
    def single(
        text: Optional[str] = typer.Option(None, help="Text to synthesize"),
        text_file: Optional[str] = typer.Option(
            None, "--text-file", help="Read text from file (alternative to --text)"
        ),
        output: str = typer.Option(
            ..., help="Output WAV filename (e.g., greeting.wav)"
        ),
        voice: Optional[str] = typer.Option(
            None,
            help=(
                "Voice preset name (resolves voices/<name>.txt single-line) or "
                "literal Gemini voice (e.g. Kore, Puck, Charon). Default: read "
                "tts-voice.txt"
            ),
        ),
        style_prompt: Optional[str] = typer.Option(
            None,
            "--style-prompt",
            help=(
                "Director's-notes style prefix prepended to the text (e.g. "
                "'Speak in a warm Newcastle accent.')"
            ),
        ),
        style_preset: Optional[str] = typer.Option(
            None,
            "--style-preset",
            help=(
                "Name of a multi-line style file under voices/<name>.txt (e.g. "
                "freud, soprano). Mutually exclusive with --style-prompt / --style-file"
            ),
        ),
        style_file: Optional[str] = typer.Option(
            None,
            "--style-file",
            help=(
                "Path to a multi-line style-directive file (comment lines stripped). "
                "Mutually exclusive with --style-prompt / --style-preset"
            ),
        ),
        speed: float = typer.Option(
            1.0,
            "--speed",
            help=(
                "Post-process tempo multiplier via ffmpeg atempo. 1.0=no change, "
                "1.8 pairs well with the freud preset. Quality stays best in "
                "[0.5, 2.0]; values outside that band chain atempo filters."
            ),
        ),
        api_url: str = typer.Option(
            DEFAULT_API_URL,
            "--api-url",
            help="Override Gemini endpoint URL",
        ),
    ) -> None:
        """Synthesize one clip from --text, --text-file, or stdin."""
        if text and text_file:
            print("Error: Use either --text or --text-file, not both", file=sys.stderr)
            raise typer.Exit(1)

        style_flags = [style_prompt, style_preset, style_file]
        if sum(1 for s in style_flags if s) > 1:
            print(
                "Error: Pass at most one of --style-prompt / --style-preset / --style-file",
                file=sys.stderr,
            )
            raise typer.Exit(1)

        skill_dir = resolve_skill_dir()
        load_env()

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            print(
                "Error: GOOGLE_API_KEY not found in environment or ~/.env",
                file=sys.stderr,
            )
            raise typer.Exit(1)

        # Resolve text: --text > --text-file > stdin
        if text is not None:
            resolved_text = text
        elif text_file is not None:
            resolved_text = Path(text_file).read_text().strip()
        elif not sys.stdin.isatty():
            resolved_text = sys.stdin.read().strip()
        else:
            print(
                "Error: Provide --text, --text-file, or pipe text on stdin",
                file=sys.stderr,
            )
            raise typer.Exit(1)

        if not resolved_text:
            print("Error: no text provided (pass as arg or via stdin)", file=sys.stderr)
            raise typer.Exit(2)

        resolved_voice = resolve_voice_preset(skill_dir, voice)
        try:
            resolved_style = resolve_style(
                skill_dir, style_prompt, style_preset, style_file
            )
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            raise typer.Exit(2)

        job = TTSJob(
            text=resolved_text,
            output=output,
            voice=resolved_voice,
            style_prompt=resolved_style,
            speed=speed,
        )
        result = generate_one(job, api_url, api_key)
        if not result.success:
            print(f"Error: {result.error}", file=sys.stderr)
            raise typer.Exit(1)
        print(result.output)
        print(f"Generated in {result.duration_s}s", file=sys.stderr)

    @app.command()
    def batch(
        json_file: str = typer.Argument(help="JSON file with jobs to generate in parallel"),
        voice: Optional[str] = typer.Option(
            None,
            help=(
                "Voice preset name (resolves voices/<name>.txt single-line) or "
                "literal Gemini voice (e.g. Kore, Puck, Charon). Default: read "
                "tts-voice.txt"
            ),
        ),
        style_prompt: Optional[str] = typer.Option(
            None,
            "--style-prompt",
            help=(
                "Director's-notes style prefix prepended to the text (e.g. "
                "'Speak in a warm Newcastle accent.')"
            ),
        ),
        style_preset: Optional[str] = typer.Option(
            None,
            "--style-preset",
            help=(
                "Name of a multi-line style file under voices/<name>.txt (e.g. "
                "freud, soprano). Mutually exclusive with --style-prompt / --style-file"
            ),
        ),
        style_file: Optional[str] = typer.Option(
            None,
            "--style-file",
            help=(
                "Path to a multi-line style-directive file (comment lines stripped). "
                "Mutually exclusive with --style-prompt / --style-preset"
            ),
        ),
        api_url: str = typer.Option(
            DEFAULT_API_URL,
            "--api-url",
            help="Override Gemini endpoint URL",
        ),
        max_workers: int = typer.Option(
            4, "--max-workers", help="Parallel batch worker count"
        ),
        speed: float = typer.Option(
            1.0,
            "--speed",
            help=(
                "Default post-process tempo for jobs that don't specify their "
                "own 'speed' key in the batch JSON."
            ),
        ),
    ) -> None:
        """Generate clips in parallel from a JSON manifest."""
        style_flags = [style_prompt, style_preset, style_file]
        if sum(1 for s in style_flags if s) > 1:
            print(
                "Error: Pass at most one of --style-prompt / --style-preset / --style-file",
                file=sys.stderr,
            )
            raise typer.Exit(1)

        skill_dir = resolve_skill_dir()
        load_env()

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            print(
                "Error: GOOGLE_API_KEY not found in environment or ~/.env",
                file=sys.stderr,
            )
            raise typer.Exit(1)

        batch_path = Path(json_file)
        if not batch_path.exists():
            print(f"Error: Batch file not found: {batch_path}", file=sys.stderr)
            raise typer.Exit(1)

        with open(batch_path) as f:
            raw_jobs = json.load(f)

        if not raw_jobs:
            print("Error: No jobs in batch file", file=sys.stderr)
            raise typer.Exit(1)

        # Map output filename -> raw dict for augmenting with debug info
        job_by_output = {d["output"]: d for d in raw_jobs}
        jobs: list[TTSJob] = []
        try:
            cli_style = resolve_style(
                skill_dir, style_prompt, style_preset, style_file
            )
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            raise typer.Exit(2)
        for d in raw_jobs:
            resolved_voice = resolve_voice_preset(skill_dir, d.get("voice"))
            try:
                job_style = resolve_style(
                    skill_dir,
                    d.get("style_prompt"),
                    d.get("style_preset"),
                    d.get("style_file"),
                )
            except FileNotFoundError as e:
                print(f"Error: {e} (job output={d.get('output')})", file=sys.stderr)
                raise typer.Exit(2)
            resolved_style = job_style if job_style is not None else cli_style
            job_speed = d.get("speed", speed)
            jobs.append(
                TTSJob(
                    text=d["text"],
                    output=d["output"],
                    voice=resolved_voice,
                    style_prompt=resolved_style,
                    speed=job_speed,
                )
            )

        print(
            f"Generating {len(jobs)} audio clips in parallel (max_workers={max_workers})...",
            file=sys.stderr,
        )
        failures: list[tuple[str, str | None]] = []

        batch_t0 = time.monotonic()
        workers = min(max_workers, len(jobs))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(generate_one, j, api_url, api_key): j for j in jobs
            }
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

        # Atomic write: tempfile in same dir + os.replace, so a crash mid-write
        # leaves the original batch file intact rather than a half-written one.
        tmp_fd, tmp_path = None, None
        try:
            import tempfile

            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=batch_path.parent, prefix=batch_path.name + ".", suffix=".tmp"
            )
            with os.fdopen(tmp_fd, "w") as f:
                tmp_fd = None  # fdopen took ownership
                json.dump(raw_jobs, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, batch_path)
            tmp_path = None
        finally:
            if tmp_fd is not None:
                os.close(tmp_fd)
            if tmp_path is not None and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        if failures:
            print(
                f"\n{len(failures)}/{len(jobs)} failed ({batch_duration}s total)",
                file=sys.stderr,
            )
            raise typer.Exit(1)
        print(
            f"\nAll {len(jobs)} clips generated ({batch_duration}s total)",
            file=sys.stderr,
        )

    return app


def main() -> None:
    """Console-script entry point. Wired via `[project.scripts] gen-tts = ...`."""
    _build_app()()


if __name__ == "__main__":
    main()
