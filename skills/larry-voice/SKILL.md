---
name: larry-voice
description: "Speak text in Larry's voice via ElevenLabs → a Telegram voice note (opus/ogg). Use for any voice reply / ElevenLabs TTS. Charlie voice, eleven_flash_v2_5, IPv4-forced, optional blind-listen accent check."
argument-hint: "say --text <text> --output reply.ogg [--verify --strict] | verify <file> --text <words>"
allowed-tools: Bash, Read, Write, Glob, Grep
---

# Larry's Voice — ElevenLabs TTS → Telegram Voice Note

Turn text into a spoken **voice note** in Larry's voice (ElevenLabs
premade voice _Charlie_), delivered as opus-in-ogg so Telegram renders
it as an inline, playable voice message. One Python entry point handles
the API call, IPv4 forcing, mp3→ogg conversion, safe key handling, and
an optional blind-listen accent check.

**This is the ElevenLabs path.** Larry's voice is Charlie on ElevenLabs
— it is **NOT** Gemini. For a Gemini/Google voice (narration, style
presets, prosody tags), use the sibling `gen-tts` skill instead. For the
reverse direction (audio → text), use `gen-stt`.

## When to use this skill

- Larry is sending a **voice reply** on Telegram and the incoming
  message was itself a voice message. **Match the reply format to the
  input** — voice reply only for a voice message; a text message gets a
  text reply. (Standing user rule; don't send unsolicited voice notes.)
- Any time you need ElevenLabs TTS in Igor's approved Larry voice.

## Quick start

```bash
LARRY_VOICE="$(git -C ~/gits/chop-conventions rev-parse --show-toplevel)/skills/larry-voice/larry-voice.py"

# Text → voice note
"$LARRY_VOICE" say --text "On it, Igor. Give me two minutes." --output /tmp/reply.ogg

# From a file, with the blind-listen accent check, failing hard if it sounds wrong
"$LARRY_VOICE" say --text-file /tmp/script.txt --output /tmp/reply.ogg --verify --strict

# Piped
echo "Morning. Ready when you are." | "$LARRY_VOICE" say --output /tmp/reply.ogg
```

Then **deliver via the Telegram MCP `reply` tool**, which renders an
`.ogg` as an inline voice note:

```
reply(chat_id=<id>, files=["/tmp/reply.ogg"])
```

Do NOT use `telegram_debug.py --send-file` or a parallel CLI path for
this — the MCP `reply` tool is the delivery mechanism.

## Configuration

- **Auth (ElevenLabs)**: key name is **`ELEVEN_API_KEY`** — _not_
  `ELEVENLABS_API_KEY` (that wrong guess cost time twice). The script
  resolves it in priority order: process env → `~/.env` →
  `secretBox.json` (`~/gits/igor2/secretBox.json`, overridable via
  `ELEVEN_SECRETBOX`). The key is **never echoed** — only a
  `len=… prefix=…` fingerprint is logged.
- **Auth (verify)**: `--verify` uses `GOOGLE_API_KEY` (env or `~/.env`)
  for the Gemini blind-listen judge. Sent as the `x-goog-api-key`
  header, never in the URL.
- **Voice**: Charlie, id `IKne3meq5aSn9XLyUdCD` (premade). Alias file
  `voices/charlie.txt` resolves via `--voice charlie`; any literal
  ElevenLabs voice id also works.
- **Model**: `eleven_flash_v2_5` (default; override with `--model`).
- **Output**: `.ogg` → mono opus-in-ogg (Telegram voice note). `.mp3`
  → the raw ElevenLabs mp3, no conversion. `ffmpeg` is required for the
  `.ogg` path.

## Guard rails (baked into the code, not left to memory)

- **IPv4 is forced** on every run (`force_ipv4()`). This VM is
  IPv4-only; `api.elevenlabs.io` frequently resolves AAAA first and the
  IPv6 connect dies with `[Errno 101] Network is unreachable`. The shim
  requests only `AF_INET` addresses, so the working path is always taken.
- **Free-tier voice rejections are explained, not just echoed.**
  ElevenLabs blocks library/cloned voices on the free tier (HTTP
  400/401/402). Charlie is premade so this normally never fires, but if
  you pass a different `--voice`, the error message says _why_ and points
  back to a premade voice.
- **Empty/truncated synths fail fast** — a sub-1KB response (or an ogg
  that ffmpeg produced with no audio) raises rather than delivering a
  dead file.

## Self-verify the accent (`--verify`)

Two corrections in the source session were _"that didn't sound right /
listen to it yourself."_ `--verify` closes that loop: it plays the
generated clip back to Gemini (`gemini-2.5-flash`) and asks whether it
sounds like the intended voice, saying the intended words, with no wrong
accent or artifacts. This is the repo's **"Measure, Don't Hardcode"**
rule applied to audio — verify the artifact, don't assume it.

- Without `--strict`, the verdict is printed to stderr and delivery is
  left to your judgment.
- With `--verify --strict`, a rejected clip **exits nonzero (3)** so a
  bad take never gets sent automatically.
- Judge an already-generated file without re-synthesizing:

  ```bash
  "$LARRY_VOICE" verify /tmp/reply.ogg --text "On it, Igor." --strict
  ```

The judge is advisory (an LLM listening), not infallible — treat a
`--strict` failure as "regenerate or listen yourself," not as gospel.

## Implementation

`larry-voice.py` — single `uv run --script` entry point (PEP-723
shebang, `typer>=0.12` the only dep; stdlib core: `urllib`, `base64`,
`json`, `socket`, `subprocess`). Typer is lazy-imported inside
`_build_app()` so the pure-function layer (key resolution, request
builders, IPv4 filter, ffmpeg argv, judge parsing) imports under system
Python for tests and pre-commit hooks without `ModuleNotFoundError`.

Pure functions are unit-tested in `test_larry_voice.py` with the HTTP
opener and `subprocess.run` mocked — **no test hits ElevenLabs, Gemini,
or ffmpeg**. Run:

```bash
uv run --with pytest python -m pytest skills/larry-voice/test_larry_voice.py -q
```

## Safety

- **Never commit generated audio or any key.** Write clips under `/tmp/`
  (or the consumer repo's private assets dir); the API key only ever
  appears as a fingerprint in logs.
- ElevenLabs TTS costs credits per character — the free tier has a
  monthly cap. Keep replies short.
