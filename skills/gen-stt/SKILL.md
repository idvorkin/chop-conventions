---
name: gen-stt
description: "Transcribe audio to text locally via NVIDIA Parakeet TDT 0.6B ONNX — single clip or batch parallel, no API key"
argument-hint: "<audio-file-or-dir> [--output path.txt] [--json] [--batch-dir dir]"
allowed-tools: Bash, Read, Write, Glob, Grep
---

# Transcribe Speech with Parakeet (Local STT)

Turn audio files into text via the local `nemo-parakeet-tdt-0.6b-v2`
ONNX model. This is the **LOCAL path** — no API key, no network after
first model download, and no per-minute billing. The trade vs. cloud
STT (Whisper API, AssemblyAI, Google STT) is CPU time: ~30s wall for
5s of audio on Igor's 8-core dev box with 2-thread cap; competitive
accuracy on clean English.

Sibling skill: `gen-tts` (Gemini TTS). Together they form a full
voice-in / voice-out pipeline.

## Arguments

Parse the user's input for:

- **Input**: Audio file path (`--input`), directory of audio files
  (`--batch-dir`), or space-separated list (`--batch-files`). Accepted
  formats: WAV, OGG, OGA, MP3, M4A, FLAC, AAC, OPUS — anything ffmpeg
  can read. Non-WAV (or non-16kHz-mono) inputs are auto-transcoded to
  16 kHz mono 16-bit PCM WAV (Parakeet's training sample rate) in a
  temp dir; already-16kHz-mono WAV inputs skip the transcode.
- **`--output path`**: Single-mode transcript destination. Defaults to
  `<input>.txt` (or `.json`) alongside the input.
- **`--output-dir DIR`**: Batch-mode transcript destination dir.
- **`--json`**: Emit `{text, duration_s, model, elapsed_s}` JSON
  instead of plain text.
- **`--keep-wav`**: Retain the intermediate transcoded WAV for
  debugging. Has no effect when the input was already 16kHz mono.
- **`--model NAME`**: Override the default
  `nemo-parakeet-tdt-0.6b-v2` onnx-asr model.
- **`--max-workers N`**: Parallel batch workers. Default **1**
  (serial). Aggregate CPU = `max_workers × per-process thread cap (2)`,
  so `--max-workers=2` already drives 4 threads and often runs *slower
  per file* than serial on consumer CPUs (measured 2026-04-16: 3 files
  at `--max-workers=2` took 114s vs ~60s projected serial). Only raise
  on a box with spare cores where wall-clock matters more than per-file
  latency.

## Configuration

- **Auth**: None. This is the local path.
- **Model cache**: `~/.cache/huggingface/` — first invocation downloads
  ~2 GB. Subsequent runs are fully offline and fast.
- **Canonical script**: `parakeet-stt.py` — single Python entry point
  handling transcode detection, ffmpeg invocation, onnx-asr dispatch,
  and parallel batch via `ThreadPoolExecutor`. Uses PEP-723 inline
  metadata with stdlib only (`subprocess`, `pathlib`, `wave`, `json`,
  `argparse`, `concurrent.futures`), launched via `uv run --script`.

## Model & Pipeline

- Model: `nemo-parakeet-tdt-0.6b-v2` (600M parameters; NVIDIA NeMo ASR)
- Backend: `uvx --with onnxruntime --with huggingface_hub onnx-asr`
- Audio contract: 16 kHz mono PCM\_S16LE WAV — the script detects
  already-conforming WAVs via the stdlib `wave` module and skips the
  transcode; otherwise it invokes `ffmpeg -ar 16000 -ac 1 -acodec
  pcm_s16le`.

## Mandatory Nice-Wrap

**Every Parakeet + ffmpeg invocation is wrapped** with:

```text
nice -n 19 ionice -c 3 env OMP_NUM_THREADS=2 ORT_NUM_THREADS=2 MKL_NUM_THREADS=2 <cmd>
```

The prefix is baked into a single `_niced_run()` helper in
`parakeet-stt.py`; there is no code path that invokes ffmpeg or
onnx-asr without it. Rationale:

- `nice` alone does NOT cap CPU; it only yields on contention. On an
  idle machine, onnxruntime will happily saturate all 8 cores.
- The thread-cap env vars are the real knob. Empirically **2 threads
  is faster than the default** on consumer CPUs because the default
  thrashes. (Measured 2026-04-11: load avg 0.75 → 7.27 running
  fastembed without the cap.)
- Leaves 6 cores for interactive work on dev VMs.

Verify on a live run with `awk '{print $19}' /proc/<pid>/stat` (nice
level) and `ionice -p <pid>` (IO class `idle`).

## Usage

### Single file

```bash
GEN_STT="$(git -C ~/gits/chop-conventions rev-parse --show-toplevel)/skills/gen-stt/parakeet-stt.py"
"$GEN_STT" --input /tmp/clip.ogg --output /tmp/transcript.txt
```

### Single file — JSON output

```bash
"$GEN_STT" --input /tmp/clip.wav --json --output /tmp/transcript.json
```

Produces:

```json
{
  "text": "Hello from Larry. This is the voice pipeline test.",
  "duration_s": 5.4,
  "model": "nemo-parakeet-tdt-0.6b-v2",
  "elapsed_s": 29
}
```

### Batch (parallel) — directory

```bash
"$GEN_STT" --batch-dir /tmp/voice-memos --output-dir /tmp/transcripts
```

Every audio file under `/tmp/voice-memos` gets a `<stem>.txt` in
`/tmp/transcripts` (suffix is replaced, not appended). With `--json`,
writes `<stem>.json` and emits a summary JSON on stdout. Without
`--output-dir`, transcripts land alongside each input as `<stem>.txt`.

### Batch — explicit file list

```bash
"$GEN_STT" --batch-files /tmp/a.ogg /tmp/b.m4a /tmp/c.wav --json
```

## Round-Trip Integration Test

Since `gen-tts` and `gen-stt` are exact siblings, the TTS → STT loop is
a tight end-to-end test:

```bash
GEN_TTS=~/gits/chop-conventions/skills/gen-tts/generate-tts.py
GEN_STT=~/gits/chop-conventions/skills/gen-stt/parakeet-stt.py

"$GEN_TTS" --text "Hello from Larry. This is the voice pipeline test." --output /tmp/roundtrip.wav
"$GEN_STT" --input /tmp/roundtrip.wav --output /tmp/roundtrip.txt
cat /tmp/roundtrip.txt
# → "Hello from Larry. This is the voice pipeline test."
```

## Error Handling

- **Missing ffmpeg**: Required for any non-16kHz-mono input. The script
  surfaces ffmpeg's stderr on failure. Install via `brew install
  ffmpeg` or `apt install ffmpeg`.
- **Missing uvx**: Required to fetch `onnx-asr`. Install via
  `brew install uv` or `pip install uv`.
- **First-run download delay**: The initial Parakeet model pull from
  HuggingFace is ~2 GB and can take 1-5 minutes depending on network.
  Subsequent runs reuse the cache. Warn the user before the first run
  on a fresh machine.
- **Empty transcript**: If `onnx-asr` returns nothing (silent audio,
  unsupported encoding that ffmpeg salvaged into unusable WAV), the
  script raises with the last 2KB of onnx-asr stderr for diagnosis
  rather than writing an empty file.
- **CPU saturation**: Default `--max-workers=1` (serial). Raising it is
  rarely a win: the per-process thread cap stays at 2, so aggregate CPU is
  `max_workers × 2`, and onnxruntime cold-start overhead per worker means
  parallel runs often finish slower per file than serial. Keep
  `max_workers ≤ cores/2` if you do raise it.

## Safety

- This is local — no API calls, no billing, no rate limits. Safe to
  batch-transcribe hundreds of files.
- Do NOT check transcripts that contain sensitive content into a
  public repo; route them to `/tmp/` or a consumer repo's private
  assets dir.
- Parakeet is English-only. For multilingual audio, prefer Whisper
  (available via the same `onnx-asr` binary — swap `--model
  whisper-base`).
