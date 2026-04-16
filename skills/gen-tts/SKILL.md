---
name: gen-tts
description: "Synthesize speech audio via Gemini 3.1 Flash TTS — single clip, batch parallel, or voice preset"
argument-hint: "<text-or-file> [--voice NAME] [--output path.wav] [--batch file.json]"
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
  1. `voices/<NAME>.txt` in this skill dir — first non-comment line is the literal Gemini `voiceName`
  2. Literal voice name (e.g. `Kore`, `Puck`, `Charon`) passed straight through
  3. Default: read `tts-voice.txt` (`Kore` ships as the default)
- **`--output path.wav`**: Where to save the WAV file
- **`--batch file.json`**: Parallel batch mode (see shape below)
- **`--api-url URL`** (bash only): Override the Gemini endpoint

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

| Voice | Vibe | Use for |
| --- | --- | --- |
| `Kore` (default) | Firm, observer-coach | Larry-style grounding, review reads |
| `Puck` | Upbeat, playful | Kickoff messages, celebratory clips |
| `Charon` | Deeper storyteller | Long-form narration |
| `Aoede` | Warm, soft | Empathic/comfort lines |

Full catalog in `tts-voice.txt`. To lock in a named preset separately
from the default, drop `voices/<name>.txt` next to this file with the
literal voiceName on its first non-comment line.

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

### Batch (parallel)

Write a JSON file:

```json
[
  { "text": "Good morning. [short pause] Ready to start?", "output": "/tmp/morning.wav", "voice": "Kore" },
  { "text": "[excited] You nailed that workout!", "output": "/tmp/celebrate.wav", "voice": "Puck" },
  { "text": "[whisper] Time to wind down.", "output": "/tmp/bedtime.wav" }
]
```

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
- **Empty audio**: If the decoded PCM is < 1KB, the script fails fast rather than emitting a useless WAV.
- **Missing deps**: Requires `jq`, `base64`, `curl`, `python3`.

## Safety

- Always confirm before batch runs > ~10 clips (API calls cost money).
- TTS is a preview API — rate limits and pricing can change with no notice.
- Do NOT check generated WAV files into this repo; save under `/tmp/` or the consumer repo's assets dir.
