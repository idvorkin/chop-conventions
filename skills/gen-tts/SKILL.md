---
name: gen-tts
description: "Synthesize speech audio via Gemini 3.1 Flash TTS — single clip, batch parallel, voice + style presets"
argument-hint: "<text-or-file> [--voice NAME] [--style-preset NAME] [--output path.wav] [--batch file.json]"
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
- **`--batch file.json`**: Parallel batch mode (see shape below)
- **`--api-url URL`** (bash only): Override the Gemini endpoint

`--voice` (Gemini voice ID) and `--style-*` (director's notes prepended to
the text) are independent and compose — e.g. `--voice Charon --style-preset
freud` pairs the Charon baritone with Freud's Viennese pacing.

## Configuration

- **Auth**: `GOOGLE_API_KEY` — auto-loaded from `~/.env` by `generate-tts.py` (same var as `gen-image`)
- **Default voice**: Read from `tts-voice.txt` in this skill's directory
- **Low-level script**: `gemini-tts.sh` handles single API calls (used internally by `generate-tts.py`)
- **Generation wrapper**: `generate-tts.py` handles env loading, voice resolution, and parallel batch execution

## Model & Endpoint

- Model: `gemini-3.1-flash-tts-preview`
- Endpoint: `POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-tts-preview:generateContent`
- Output: base64-encoded raw PCM (16-bit signed LE, 24 kHz, mono) in
  `candidates[0].content.parts[*].inlineData.data`. `gemini-tts.sh`
  wraps it in a canonical WAV header so the `.wav` output is directly
  playable by any audio tool.

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

### Single clip — text arg

```bash
GEN_TTS="$(git -C ~/gits/chop-conventions rev-parse --show-toplevel)/skills/gen-tts/generate-tts.py"
python3 "$GEN_TTS" --text "Hello from Larry. [short pause] This is a voice pipeline test." --output /tmp/larry.wav
```

### Single clip — text file

```bash
python3 "$GEN_TTS" --text-file /tmp/script.txt --output /tmp/read.wav --voice Charon
```

### Single clip — character style preset

```bash
# Freud's Viennese analyst voice, baritone
python3 "$GEN_TTS" --text "Tell me about your mother." --voice Charon \
  --style-preset freud --output /tmp/freud.wav

# Inline director's notes (no preset)
python3 "$GEN_TTS" --text "Welcome aboard." --voice Puck \
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
python3 "$GEN_TTS" --batch /tmp/lines.json --max-workers 4
```

`_duration_s` is written back into each job object for debug visibility.

### Bash-only (no Python wrapper)

```bash
bash ~/gits/chop-conventions/skills/gen-tts/gemini-tts.sh "text" --output /tmp/out.wav --voice Kore
echo "piped text" | bash .../gemini-tts.sh --output /tmp/out.wav
```

## Error Handling

- **Missing API key**: `generate-tts.py` auto-loads from `~/.env`. If still unset, set `GOOGLE_API_KEY` in env or `~/.env`.
- **API error**: The bash script prints the JSON error body and exits 1. Common causes: billing not enabled for the API key, TTS preview not enabled in the GCP project, quota exhausted.
- **Safety-filter rejection**: If Gemini returns `promptFeedback.blockReason` (input blocked) or `candidates[0].finishReason` of `SAFETY` / `PROHIBITED_CONTENT` / `RECITATION` / `OTHER` (output blocked), the bash script prints the specific reason and exits 1. Most common trigger: certain prosody tags (`[excited]`, `[whisper]`) combined with flavorful content trip the filter. Retry without the tag or rephrase the text.
- **Empty audio**: If the decoded PCM is < 1KB, the script fails fast rather than emitting a useless WAV.
- **Missing deps**: Requires `jq`, `base64`, `curl`, `python3`.

## Safety

- Always confirm before batch runs > ~10 clips (API calls cost money).
- TTS is a preview API — rate limits and pricing can change with no notice.
- Do NOT check generated WAV files into this repo; save under `/tmp/` or the consumer repo's assets dir.
