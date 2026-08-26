#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer>=0.12"]
# ///
# ABOUTME: ElevenLabs TTS → Telegram voice note. Text in, opus/ogg out, in Larry's voice.
# ABOUTME: Stdlib-only core (urllib, base64, json, socket); Typer for CLI. IPv4-forced; ffmpeg for ogg.
# ABOUTME: Optional --verify does a blind-listen accent check via Gemini before the clip is trusted.
#
#   larry-voice.py say --text "On it, Igor." --output /tmp/reply.ogg
#   larry-voice.py say --text-file script.txt --output /tmp/reply.ogg --verify --strict
#   echo "piped text" | larry-voice.py say --output /tmp/reply.ogg
#   larry-voice.py verify /tmp/reply.ogg --text "On it, Igor."   # judge an existing clip
#
# Deliver the .ogg via the Telegram MCP reply tool: reply(files=["/tmp/reply.ogg"]).

import base64
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# ----- Defaults -----------------------------------------------------------

# Charlie — the ElevenLabs premade voice approved for Larry (Igor, 2026-08-16).
# Premade => works on the free tier (library/cloned voices are 400/402-blocked).
DEFAULT_VOICE_ID = "IKne3meq5aSn9XLyUdCD"
DEFAULT_VOICE_NAME = "Charlie"
DEFAULT_MODEL = "eleven_flash_v2_5"

# A short natural-language description of what Charlie SHOULD sound like, fed to
# the blind-listen judge so it can flag a wrong accent / robotic delivery.
DEFAULT_VOICE_DESC = "a calm, warm, natural British (English) man's voice"

ELEVEN_BASE_URL = "https://api.elevenlabs.io/v1/text-to-speech"
GEMINI_JUDGE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)

# Where the API key may live, in priority order. secretBox.json is Igor's
# machine-local store; ELEVEN_SECRETBOX overrides the path for portability.
DEFAULT_SECRETBOX = "~/gits/igor2/secretBox.json"
ELEVEN_KEY_NAME = (
    "ELEVEN_API_KEY"  # NOT ELEVENLABS_API_KEY (wrong guess cost time twice)
)

MIN_AUDIO_BYTES = 1000  # anything smaller is a truncated / empty synth, not audio.


class VoiceError(Exception):
    """Caller-facing failure — message should be printable as-is."""


# ----- Key resolution (pure) ---------------------------------------------


def parse_env_lines(lines) -> dict:
    """Parse KEY=VALUE lines (an ~/.env file) into a dict. Pure/testable."""
    out: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def resolve_api_key(
    environ: dict,
    env_data: dict | None = None,
    secretbox_data: dict | None = None,
    key_name: str = ELEVEN_KEY_NAME,
) -> str | None:
    """Resolve the ElevenLabs key from (1) process env, (2) ~/.env, (3) secretBox.

    Pure: all three sources are passed in as dicts so this is unit-testable
    without touching the filesystem.
    """
    for source in (environ, env_data or {}, secretbox_data or {}):
        val = source.get(key_name)
        if val:
            return val.strip()
    return None


def key_fingerprint(key: str) -> str:
    """Safe-to-log identity for a secret: length + 4-char prefix, never the body."""
    return f"len={len(key)} prefix={key[:4]}…"


def load_key_from_disk(
    env_file: str = "~/.env",
    secretbox_path: str | None = None,
    key_name: str = ELEVEN_KEY_NAME,
) -> str | None:
    """Filesystem wrapper around resolve_api_key. Reads ~/.env and secretBox.json."""
    env_data: dict = {}
    env_path = Path(env_file).expanduser()
    if env_path.exists():
        env_data = parse_env_lines(env_path.read_text().splitlines())

    secretbox_data: dict = {}
    sb_path = Path(
        secretbox_path or os.environ.get("ELEVEN_SECRETBOX", DEFAULT_SECRETBOX)
    )
    sb_path = sb_path.expanduser()
    if sb_path.exists():
        try:
            secretbox_data = json.loads(sb_path.read_text())
        except (json.JSONDecodeError, OSError):
            secretbox_data = {}

    return resolve_api_key(os.environ, env_data, secretbox_data, key_name)


# ----- IPv4 forcing -------------------------------------------------------


def filter_ipv4(addrinfo_results) -> list:
    """Keep only AF_INET entries from a getaddrinfo result list. Pure/testable.

    This VM is IPv4-only; api.elevenlabs.io often resolves AAAA first and the
    IPv6 connect fails with `[Errno 101] Network is unreachable`. Dropping the
    v6 records forces urllib down the working path.
    """
    return [ai for ai in addrinfo_results if ai[0] == socket.AF_INET]


_ORIG_GETADDRINFO = socket.getaddrinfo


def force_ipv4() -> None:
    """Install an IPv4-only getaddrinfo shim process-wide. Idempotent."""

    def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        results = _ORIG_GETADDRINFO(host, port, socket.AF_INET, type, proto, flags)
        return filter_ipv4(results)

    socket.getaddrinfo = _ipv4_getaddrinfo


# ----- ElevenLabs request (pure builders) --------------------------------


def tts_url(voice_id: str, base: str = ELEVEN_BASE_URL) -> str:
    return f"{base}/{voice_id}"


def build_tts_payload(text: str, model: str = DEFAULT_MODEL) -> dict:
    """The ElevenLabs text-to-speech JSON body. Pure/testable."""
    return {"text": text, "model_id": model}


def explain_eleven_error(status: int, body: str, voice_id: str) -> str:
    """Turn an ElevenLabs HTTP error into an actionable message.

    Free-tier accounts reject library/cloned voices with 400/401/402 — the
    error path must say WHY, not just echo the status. Charlie is premade so
    this normally never fires, but a swapped --voice can trip it.
    """
    msg = f"ElevenLabs returned HTTP {status} for voice {voice_id}."
    if status in (400, 401, 402):
        msg += (
            "\n  Free-tier accounts can only synth PREMADE voices — library"
            "\n  and cloned voice IDs are blocked (400/402). Charlie"
            f"\n  ({DEFAULT_VOICE_ID}) is premade; if you passed a different"
            "\n  voice, switch back to a premade one or upgrade the plan."
        )
    if status in (401, 403):
        msg += f"\n  Also verify the {ELEVEN_KEY_NAME} key is present and valid."
    if body:
        msg += f"\n  Response body: {body[:1000]}"
    return msg


# ----- ElevenLabs request (network) --------------------------------------


def _post_bytes(
    url: str, payload: bytes, headers: dict, timeout: float, opener=None
) -> tuple[int, bytes, dict]:
    """POST and return (status, body_bytes, response_headers). Injectable opener."""
    req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    _open = opener or urllib.request.urlopen
    try:
        with _open(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        return e.code, body, dict(getattr(e, "headers", {}) or {})


def synth_mp3(
    text: str,
    voice_id: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    timeout: float = 120.0,
    opener=None,
) -> bytes:
    """Call ElevenLabs TTS, return raw mp3 bytes. Raises VoiceError on failure."""
    url = tts_url(voice_id)
    payload = json.dumps(build_tts_payload(text, model)).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
        "xi-api-key": api_key,
    }
    status, body, resp_headers = _post_bytes(url, payload, headers, timeout, opener)

    if not (200 <= status < 300):
        text_body = body.decode("utf-8", errors="replace")
        raise VoiceError(explain_eleven_error(status, text_body, voice_id))

    ctype = resp_headers.get("Content-Type") or resp_headers.get("content-type") or ""
    if "audio" not in ctype and body[:3] != b"ID3" and body[:2] != b"\xff\xfb":
        # Server returned 200 but JSON (an error envelope) instead of audio.
        preview = body.decode("utf-8", errors="replace")[:1000]
        raise VoiceError(f"Expected audio, got Content-Type={ctype!r}: {preview}")

    if len(body) < MIN_AUDIO_BYTES:
        raise VoiceError(
            f"ElevenLabs returned only {len(body)} bytes — truncated / empty synth."
        )
    return body


# ----- ffmpeg mp3 -> ogg/opus --------------------------------------------


def ffmpeg_ogg_cmd(mp3_path: str, ogg_path: str) -> list:
    """ffmpeg argv converting mp3 -> mono opus-in-ogg (Telegram voice-note format)."""
    return [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        mp3_path,
        "-ac",
        "1",
        "-c:a",
        "libopus",
        "-b:a",
        "32k",
        ogg_path,
    ]


def mp3_to_ogg(mp3_path: str, ogg_path: str, run=None) -> None:
    """Transcode mp3 -> ogg/opus in place via ffmpeg. Injectable run for tests."""
    run = run or subprocess.run
    cmd = ffmpeg_ogg_cmd(mp3_path, ogg_path)
    try:
        run(cmd, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise VoiceError(
            "ffmpeg not found on PATH — required for ogg conversion."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or b""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise VoiceError(f"ffmpeg mp3->ogg failed: {stderr[:1000]}") from exc
    if not os.path.exists(ogg_path) or os.path.getsize(ogg_path) < MIN_AUDIO_BYTES:
        size = os.path.getsize(ogg_path) if os.path.exists(ogg_path) else 0
        raise VoiceError(
            f"ogg output is only {size} bytes — conversion produced no audio."
        )


# ----- Blind-listen verify (Gemini judge) --------------------------------

# "Measure, don't hardcode" for audio: instead of assuming the synth sounded
# right, we play it back to a model and ask. Two user corrections in the source
# session were "that didn't sound right / listen to it yourself" — this closes
# that loop before the clip is delivered.


def build_judge_prompt(expected_text: str, voice_desc: str = DEFAULT_VOICE_DESC) -> str:
    return (
        "You are an audio QA judge. Listen to the attached speech clip and "
        "decide whether it is acceptable to send as a voice reply.\n\n"
        f"It should sound like {voice_desc}, speaking clearly and naturally, "
        "with no robotic artifacts, no wrong accent, and no garbled or missing "
        "words.\n\n"
        f'The intended words are: "{expected_text}"\n\n'
        "Respond with ONLY a JSON object, no prose, no code fences:\n"
        '{"ok": true|false, "reason": "<one short sentence>"}'
    )


def build_judge_payload(prompt: str, audio_b64: str, mime: str = "audio/ogg") -> dict:
    return {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": mime, "data": audio_b64}},
                ]
            }
        ]
    }


def _extract_judge_text(response: dict) -> str:
    candidates = response.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts).strip()


def parse_judge_verdict(response: dict) -> tuple[bool, str]:
    """Parse the Gemini judge response into (ok, reason). Lenient about fences."""
    err = (response.get("error") or {}).get("message")
    if err:
        raise VoiceError(f"Judge API error: {err}")
    raw = _extract_judge_text(response)
    if not raw:
        raise VoiceError("Judge returned no text to parse.")
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # strip ```json ... ``` fences
        cleaned = cleaned.split("```", 2)[1] if cleaned.count("```") >= 2 else cleaned
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise VoiceError(f"Judge verdict not JSON: {raw[:300]}")
    verdict = json.loads(cleaned[start : end + 1])
    return bool(verdict.get("ok")), str(verdict.get("reason", "")).strip()


def verify_audio(
    audio_path: str,
    expected_text: str,
    google_key: str,
    voice_desc: str = DEFAULT_VOICE_DESC,
    timeout: float = 120.0,
    opener=None,
) -> tuple[bool, str]:
    """Blind-listen check: send the clip to Gemini, return (ok, reason)."""
    data = Path(audio_path).read_bytes()
    audio_b64 = base64.b64encode(data).decode("ascii")
    mime = "audio/ogg" if audio_path.endswith(".ogg") else "audio/mpeg"
    prompt = build_judge_prompt(expected_text, voice_desc)
    payload = json.dumps(build_judge_payload(prompt, audio_b64, mime)).encode("utf-8")
    headers = {"Content-Type": "application/json", "x-goog-api-key": google_key}
    status, body, _ = _post_bytes(GEMINI_JUDGE_URL, payload, headers, timeout, opener)
    try:
        response = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise VoiceError(f"Judge HTTP {status}: non-JSON response {body[:500]!r}")
    return parse_judge_verdict(response)


# ----- End-to-end driver -------------------------------------------------


@dataclass
class SayResult:
    output: str
    bytes_written: int
    verified: bool | None  # None = not checked, True/False = judge verdict
    verify_reason: str | None


def synth_to_ogg(
    text: str,
    ogg_path: str,
    voice_id: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    keep_mp3: bool = False,
) -> int:
    """text -> mp3 (ElevenLabs) -> ogg (ffmpeg). Returns ogg byte size."""
    mp3_path = ogg_path + ".mp3" if not ogg_path.endswith(".mp3") else ogg_path
    mp3_bytes = synth_mp3(text, voice_id, api_key, model)
    Path(mp3_path).write_bytes(mp3_bytes)
    if ogg_path.endswith(".mp3"):
        return len(mp3_bytes)  # caller wanted raw mp3
    try:
        mp3_to_ogg(mp3_path, ogg_path)
    finally:
        if not keep_mp3 and os.path.exists(mp3_path):
            os.unlink(mp3_path)
    return os.path.getsize(ogg_path)


# ----- CLI ---------------------------------------------------------------


def _resolve_text(text, text_file) -> str:
    if text is not None:
        return text
    if text_file is not None:
        return Path(text_file).read_text().strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def _resolve_voice_id(skill_dir: Path, voice_arg: str | None) -> str:
    """--voice: a voices/<name>.txt alias (first non-comment line) or a literal id."""
    if voice_arg is None:
        return DEFAULT_VOICE_ID
    preset = skill_dir / "voices" / f"{voice_arg}.txt"
    if preset.exists():
        for line in preset.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return voice_arg


def _build_app():
    """Wire Typer app. Imported only under __main__ so tests skip the typer dep."""

    import typer

    app = typer.Typer(
        help="ElevenLabs TTS → Telegram voice note (Larry's voice).",
        add_completion=False,
        no_args_is_help=True,
    )

    @app.command()
    def say(
        text: str | None = typer.Option(None, help="Text to speak"),
        text_file: str | None = typer.Option(
            None, "--text-file", help="Read text from a file (alternative to --text)"
        ),
        output: str = typer.Option(
            ..., help="Output path. .ogg (Telegram voice note) or .mp3 for raw."
        ),
        voice: str | None = typer.Option(
            None,
            help=(
                "Voice: a voices/<name>.txt alias or a literal ElevenLabs voice id. "
                f"Default: Charlie ({DEFAULT_VOICE_ID})."
            ),
        ),
        model: str = typer.Option(DEFAULT_MODEL, help="ElevenLabs model id"),
        verify: bool = typer.Option(
            False,
            "--verify",
            help="Blind-listen accent check via Gemini before trusting the clip",
        ),
        strict: bool = typer.Option(
            False,
            "--strict",
            help="With --verify, exit nonzero if the judge rejects the clip",
        ),
        voice_desc: str = typer.Option(
            DEFAULT_VOICE_DESC,
            "--voice-desc",
            help="What the voice should sound like (for --verify)",
        ),
        keep_mp3: bool = typer.Option(
            False, "--keep-mp3", help="Keep the intermediate .mp3 next to the .ogg"
        ),
    ) -> None:
        """Synthesize one clip in Larry's voice and write an .ogg (or .mp3)."""
        force_ipv4()
        skill_dir = Path(__file__).resolve().parent

        resolved_text = _resolve_text(text, text_file)
        if not resolved_text:
            print(
                "Error: provide --text, --text-file, or pipe text on stdin",
                file=sys.stderr,
            )
            raise typer.Exit(2)

        api_key = load_key_from_disk()
        if not api_key:
            print(
                f"Error: {ELEVEN_KEY_NAME} not found in env, ~/.env, or secretBox.json",
                file=sys.stderr,
            )
            raise typer.Exit(1)
        print(f"{ELEVEN_KEY_NAME}: {key_fingerprint(api_key)}", file=sys.stderr)

        voice_id = _resolve_voice_id(skill_dir, voice)
        print(
            f"Synthesizing → {output} (voice={voice_id}, model={model})",
            file=sys.stderr,
        )
        try:
            size = synth_to_ogg(
                resolved_text, output, voice_id, api_key, model, keep_mp3
            )
        except VoiceError as e:
            print(f"Error: {e}", file=sys.stderr)
            raise typer.Exit(1)
        print(f"Saved: {output} ({size} bytes)", file=sys.stderr)

        verified: bool | None = None
        reason: str | None = None
        if verify:
            google_key = os.environ.get("GOOGLE_API_KEY")
            if not google_key:
                load_env_google = load_key_from_disk(key_name="GOOGLE_API_KEY")
                google_key = load_env_google
            if not google_key:
                print(
                    "Error: --verify needs GOOGLE_API_KEY (env or ~/.env)",
                    file=sys.stderr,
                )
                raise typer.Exit(1)
            try:
                verified, reason = verify_audio(
                    output, resolved_text, google_key, voice_desc
                )
            except VoiceError as e:
                print(f"Verify error: {e}", file=sys.stderr)
                raise typer.Exit(1)
            verdict = "OK" if verified else "REJECTED"
            print(f"Blind-listen: {verdict} — {reason}", file=sys.stderr)
            if not verified and strict:
                print(
                    "Failing under --strict (judge rejected the clip).", file=sys.stderr
                )
                raise typer.Exit(3)

        print(output)  # stdout: the deliverable path

    @app.command()
    def verify(
        audio: str = typer.Argument(help="Audio file (.ogg / .mp3) to judge"),
        text: str = typer.Option(..., help="The words the clip is supposed to say"),
        voice_desc: str = typer.Option(DEFAULT_VOICE_DESC, "--voice-desc"),
        strict: bool = typer.Option(
            False, "--strict", help="Exit nonzero on rejection"
        ),
    ) -> None:
        """Blind-listen judge an existing audio file (no synth)."""
        force_ipv4()
        google_key = os.environ.get("GOOGLE_API_KEY") or load_key_from_disk(
            key_name="GOOGLE_API_KEY"
        )
        if not google_key:
            print("Error: GOOGLE_API_KEY not found (env or ~/.env)", file=sys.stderr)
            raise typer.Exit(1)
        try:
            ok, reason = verify_audio(audio, text, google_key, voice_desc)
        except VoiceError as e:
            print(f"Error: {e}", file=sys.stderr)
            raise typer.Exit(1)
        print(json.dumps({"ok": ok, "reason": reason}))
        if not ok and strict:
            raise typer.Exit(3)

    return app


if __name__ == "__main__":
    _build_app()()
