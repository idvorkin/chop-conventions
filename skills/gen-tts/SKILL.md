---
name: gen-tts
description: "Synthesize speech audio via Gemini 3.1 Flash TTS — single clip, batch parallel, voice + style presets"
argument-hint: "single --text <text> --output path.wav | batch file.json [--voice NAME] [--style-preset NAME]"
allowed-tools: Bash, Read, Write, Glob, Grep, AskUserQuestion
---

# Generate Speech with Gemini TTS

Turn text into natural-sounding speech via Google's Gemini 3.1 Flash TTS
preview. Supports single-clip generation, parallel batch mode, prosody
tags (`[whisper]`, `[short pause]`, `[excited]`, …), and voice preset
files.

## Arguments

Parse the user's input for:

- **Target**: Raw text (quoted), or a path to a text file (`--text-file`)
- **`--voice NAME`**: Voice preset name. Resolves in this order:
  1. `voices/<NAME>.txt` in this skill dir — first non-comment line is the literal Gemini `voiceName` (for single-line voice-ID presets)
  2. Literal voice name (e.g. `Kore`, `Puck`, `Charon`) passed straight through
  3. Default: read `tts-voice.txt` (`Charon` ships as the default)
- **`--style-prompt TEXT`**: Director's-notes prefix prepended to the text (e.g. `"Speak slowly, with a warm Newcastle accent."`). Gemini honors these notes separately from the voice ID.
- **`--style-preset NAME`**: Load a multi-line style directive from `voices/<NAME>.txt` (e.g. `freud`, `soprano`). Comment lines stripped, body collapsed to one paragraph, prepended as a director's note. Mutually exclusive with `--style-prompt` / `--style-file`.
- **`--style-file PATH`**: Like `--style-preset` but takes an explicit file path (for styles kept outside the skill dir).
- **`--output path.wav`**: Where to save the WAV file
- **`batch file.json`**: Parallel batch mode subcommand (see shape below)
- **`--api-url URL`**: Override the Gemini endpoint (rarely needed; useful for testing against a proxy)

`--voice` (Gemini voice ID) and `--style-*` (director's notes prepended to
the text) are independent and compose — e.g. `--voice Charon --style-preset
freud` pairs the Charon baritone with Freud's Viennese pacing.

## Configuration

- **Auth**: `GOOGLE_API_KEY` — auto-loaded from `~/.env` by `generate-tts.py` (same var as `gen-image`). Passed as an `x-goog-api-key` header so the key never leaks into URL access logs, shell traces, or proxy logs.
- **Default voice**: Read from `tts-voice.txt` in this skill's directory
- **Single entry point**: `generate-tts.py` — handles the HTTP call, WAV assembly, env loading, voice resolution, and parallel batch execution. Stdlib core with Typer for CLI (no `requests` / `httpx` / `jq` / `curl` deps); the PEP-723 shebang (`#!/usr/bin/env -S uv run --script`) means it runs without a local venv. Two subcommands: `single` (one clip) and `batch` (parallel from JSON manifest).

## Model & Endpoint

- Model: `gemini-3.1-flash-tts-preview`
- Endpoint: `POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-tts-preview:generateContent`
- Output: base64-encoded raw PCM (16-bit signed LE, 24 kHz, mono) in
  `candidates[0].content.parts[*].inlineData.data`. `generate-tts.py`
  wraps it in a canonical WAV header (via `struct.pack`) so the `.wav`
  output is directly playable by any audio tool. Sample rate is parsed
  from the response MIME type (`audio/L16;codec=pcm;rate=24000`) with
  24000 as the fallback.

## Directorial Tags

Embed these inline in the text to steer delivery (Gemini interprets them
rather than speaking them verbatim):

- **Dynamics**: `[whisper]`, `[shouting]`, `[sigh]`, `[gasp]`,
  `[laughs]`, `[crying]`
- **Pacing**: `[short pause]`, `[long pause]`
- **Emotion**: `[excited]`, `[tired]`, `[sarcastic]`, `[amazed]`,
  `[angry]`, `[confused]`, `[nervous]`, `[calm]`, `[warm]`

The model also accepts narrative "director's notes" prefixed to the
text — e.g. `"Speak slowly, with a warm Newcastle accent: <text>"` — per
Simon Willison's 2026-04-15 write-up. Use either approach; they compose.

## Voices

30 prebuilt voices ship with the model. Ones we've found useful:

| Voice              | Vibe                                  | Use for                                    |
| ------------------ | ------------------------------------- | ------------------------------------------ |
| `Charon` (default) | Deeper storyteller baritone           | Larry-style grounding, long-form narration |
| `Kore`             | Firm, observer-coach (reads feminine) | Review reads when that vibe fits           |
| `Puck`             | Upbeat, playful                       | Kickoff messages, celebratory clips        |
| `Aoede`            | Warm, soft                            | Empathic/comfort lines                     |

Full catalog in `tts-voice.txt`. To lock in a named preset separately
from the default, drop `voices/<name>.txt` next to this file with the
literal voiceName on its first non-comment line.

### `voices/<name>.txt` — two shapes

Files in `voices/` serve two distinct purposes:

1. **Single-line voice-ID alias** — first non-comment line is a literal
   Gemini voice name (`Kore`, `Charon`, `Puck`, etc.). Used via
   `--voice <name>`. The rest of the file is commentary on when to pick it.
2. **Multi-line style directive** — a character/tone description
   (Freud's Viennese pacing, Tony Soprano's gravel, etc.). Used via
   `--style-preset <name>`. Comment lines are stripped; the body is
   prepended to the text as a director's note. These do NOT pick a
   Gemini voice — pair them with `--voice Charon` (or whichever voice
   ID suits the character) for best results.

Shipped style presets: `freud`, `soprano`.

## Usage

The script is executable and uses a `uv`-powered PEP-723 shebang — invoke it directly, no `python3` prefix needed:

```bash
GEN_TTS="$(git -C ~/gits/chop-conventions rev-parse --show-toplevel)/skills/gen-tts/generate-tts.py"
```

### Single clip — text arg

```bash
"$GEN_TTS" single --text "Hello from Larry. [short pause] This is a voice pipeline test." --output /tmp/larry.wav
```

### Single clip — text file or stdin

```bash
"$GEN_TTS" single --text-file /tmp/script.txt --output /tmp/read.wav --voice Charon
echo "piped text" | "$GEN_TTS" single --output /tmp/piped.wav
```

### Single clip — character style preset

```bash
# Freud's Viennese analyst voice, baritone
"$GEN_TTS" single --text "Tell me about your mother." --voice Charon \
  --style-preset freud --output /tmp/freud.wav

# Inline director's notes (no preset)
"$GEN_TTS" single --text "Welcome aboard." --voice Puck \
  --style-prompt "Speak with the warmth of a flight attendant greeting family." \
  --output /tmp/welcome.wav
```

### Batch (parallel)

Write a JSON file:

```json
[
  {
    "text": "Good morning. [short pause] Ready to start?",
    "output": "/tmp/morning.wav",
    "voice": "Kore"
  },
  {
    "text": "[excited] You nailed that workout!",
    "output": "/tmp/celebrate.wav",
    "voice": "Puck"
  },
  { "text": "[whisper] Time to wind down.", "output": "/tmp/bedtime.wav" },
  {
    "text": "Tell me about your father.",
    "output": "/tmp/freud.wav",
    "voice": "Charon",
    "style_preset": "freud"
  }
]
```

Each job object accepts `text`, `output`, `voice`, plus any one of
`style_prompt` / `style_preset` / `style_file`. A `--style-*` flag on the
CLI provides a default that per-job entries can override.

Then:

```bash
"$GEN_TTS" batch /tmp/lines.json --max-workers 4
```

`_duration_s` is written back into each job object for debug visibility. The batch file is rewritten atomically (tempfile + `os.replace`) so a crash mid-write never corrupts the input.

## Error Handling

- **Missing API key**: auto-loads from `~/.env`. If still unset, set `GOOGLE_API_KEY` in env or `~/.env`.
- **API error**: top-level `error.message` (malformed request, quota exhausted, billing not enabled, TTS preview not enabled) is surfaced verbatim with an `API Error:` prefix.
- **Safety-filter rejection (input)**: `promptFeedback.blockReason` surfaces as `Gemini TTS refused the prompt (blockReason=...)` with the specific reason (e.g. `PROHIBITED_CONTENT`) plus any `safetyRatings` for diagnosis.
- **Safety-filter rejection (output)**: `candidates[0].finishReason` of `SAFETY` / `PROHIBITED_CONTENT` / `RECITATION` / `OTHER` (anything other than `STOP` / `MAX_TOKENS`) surfaces as `refused to generate audio (finishReason=...)`. Most common trigger: certain prosody tags (`[excited]`, `[whisper]`) combined with flavorful content trip the filter. Retry without the tag or rephrase.
- **Transient 5xx / network errors**: one automatic retry with a 2s delay before giving up. Failed-after-retry requests raise `HTTP request failed after retry: ...`.
- **Empty audio**: If the decoded PCM is < 1 KB, the script fails fast rather than emitting a useless WAV.
- **Dependencies**: Stdlib-only (`urllib.request`, `base64`, `struct`, `json`). No `jq` / `curl` / `requests` / `httpx` required — just a Python 3.11+ interpreter managed by `uv` (the PEP-723 shebang bootstraps it).

## Safety

- Always confirm before batch runs > ~10 clips (API calls cost money).
- TTS is a preview API — rate limits and pricing can change with no notice.
- Do NOT check generated WAV files into this repo; save under `/tmp/` or the consumer repo's assets dir.
