#!/bin/bash
# ABOUTME: Transcribes an audio file to text via the local NVIDIA Parakeet TDT 0.6B ONNX model.
# ABOUTME: Auto-transcodes OGG/M4A/MP3/etc. to 16kHz mono WAV via ffmpeg, then runs onnx-asr.
#
# Usage:
#   parakeet-stt.sh <input-audio> [--output path.txt] [--json] [--keep-wav]
#   parakeet-stt.sh /tmp/clip.ogg --output /tmp/transcript.txt
#   parakeet-stt.sh /tmp/clip.wav --json > /tmp/out.json
#
# Environment:
#   None — this is the LOCAL path (no API key, no network after first model download).
#
# Performance:
#   ALL invocations of Parakeet + ffmpeg are wrapped with
#     nice -n 19 ionice -c 3 env OMP_NUM_THREADS=2 ORT_NUM_THREADS=2 MKL_NUM_THREADS=2
#   Empirically 2 threads is FASTER than onnxruntime's default on consumer CPUs
#   because the default thrashes. This is baked into the script so callers
#   can't forget.
#
# Model: nemo-parakeet-tdt-0.6b-v2 (600M params; first invocation downloads ~2GB
#   into ~/.cache/huggingface, subsequent runs are offline and fast).

set -euo pipefail

INPUT=""
OUTPUT=""
JSON_MODE=0
KEEP_WAV=0
MODEL="nemo-parakeet-tdt-0.6b-v2"

NICE_PREFIX=(nice -n 19 ionice -c 3 env OMP_NUM_THREADS=2 ORT_NUM_THREADS=2 MKL_NUM_THREADS=2)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        --json)
            JSON_MODE=1
            shift
            ;;
        --keep-wav)
            KEEP_WAV=1
            shift
            ;;
        --model)
            MODEL="$2"
            shift 2
            ;;
        --help|-h)
            sed -n '3,22p' "$0"
            exit 0
            ;;
        --*)
            echo "Unknown flag: $1" >&2
            exit 2
            ;;
        *)
            if [[ -z "$INPUT" ]]; then
                INPUT="$1"
            else
                echo "Unexpected positional arg: $1" >&2
                exit 2
            fi
            shift
            ;;
    esac
done

if [[ -z "$INPUT" ]]; then
    echo "Error: no input audio file provided" >&2
    exit 2
fi

if [[ ! -f "$INPUT" ]]; then
    echo "Error: input file not found: $INPUT" >&2
    exit 1
fi

command -v uvx >/dev/null 2>&1 || { echo "Error: uvx is required (install via 'brew install uv' or 'pip install uv')" >&2; exit 1; }

# Determine if we need to transcode. onnx-asr accepts WAV PCM_16/24/32 at any sample rate
# but Parakeet is trained on 16kHz mono, and ffprobe gives us truth on sample rate + channels.
NEEDS_TRANSCODE=1
if [[ "$INPUT" == *.wav || "$INPUT" == *.WAV ]]; then
    if command -v ffprobe >/dev/null 2>&1; then
        PROBE=$(ffprobe -v error -select_streams a:0 \
            -show_entries stream=sample_rate,channels,codec_name \
            -of default=noprint_wrappers=1:nokey=1 "$INPUT" 2>/dev/null || true)
        # PROBE is three lines: codec, sample_rate, channels
        CODEC=$(echo "$PROBE" | sed -n '1p')
        RATE=$(echo "$PROBE" | sed -n '2p')
        CH=$(echo "$PROBE" | sed -n '3p')
        if [[ "$CODEC" == "pcm_s16le" && "$RATE" == "16000" && "$CH" == "1" ]]; then
            NEEDS_TRANSCODE=0
        fi
    fi
fi

WORK_DIR=$(mktemp -d /tmp/parakeet-stt-XXXXXX)
cleanup() {
    if [[ "$KEEP_WAV" -eq 0 ]]; then
        rm -rf "$WORK_DIR"
    else
        echo "Kept intermediate WAV dir: $WORK_DIR" >&2
    fi
}
trap cleanup EXIT

if [[ "$NEEDS_TRANSCODE" -eq 1 ]]; then
    command -v ffmpeg >/dev/null 2>&1 || { echo "Error: ffmpeg is required for non-WAV inputs" >&2; exit 1; }
    WAV_FILE="$WORK_DIR/input16k.wav"
    echo "Transcoding to 16kHz mono WAV..." >&2
    "${NICE_PREFIX[@]}" ffmpeg -y -hide_banner -loglevel error \
        -i "$INPUT" -ar 16000 -ac 1 -acodec pcm_s16le "$WAV_FILE"
else
    WAV_FILE="$INPUT"
fi

# Capture WAV duration for JSON output (best-effort).
DURATION_S=""
if command -v ffprobe >/dev/null 2>&1; then
    DURATION_S=$(ffprobe -v error -show_entries format=duration \
        -of default=noprint_wrappers=1:nokey=1 "$WAV_FILE" 2>/dev/null || true)
fi

echo "Running Parakeet ASR (model=$MODEL)..." >&2
T0=$(date +%s)
# onnx-asr writes the transcript to stdout; warnings (cpuid etc.) and progress
# go to stderr. Capture stderr so we can surface it only on failure instead of
# silently swallowing genuine errors.
ASR_ERR="$WORK_DIR/asr.err"
set +e
TRANSCRIPT=$("${NICE_PREFIX[@]}" uvx --with onnxruntime --with huggingface_hub \
    onnx-asr "$MODEL" "$WAV_FILE" 2>"$ASR_ERR")
ASR_RC=$?
set -e
T1=$(date +%s)
ELAPSED=$(( T1 - T0 ))

if [[ "$ASR_RC" -ne 0 ]]; then
    echo "Error: onnx-asr exited $ASR_RC" >&2
    if [[ -s "$ASR_ERR" ]]; then
        echo "--- onnx-asr stderr (last 2KB) ---" >&2
        tail -c 2048 "$ASR_ERR" >&2
    fi
    exit 1
fi

if [[ -z "$TRANSCRIPT" ]]; then
    echo "Error: Parakeet returned empty transcript" >&2
    if [[ -s "$ASR_ERR" ]]; then
        echo "--- onnx-asr stderr (last 2KB) ---" >&2
        tail -c 2048 "$ASR_ERR" >&2
    fi
    exit 1
fi

echo "Transcribed in ${ELAPSED}s" >&2

if [[ "$JSON_MODE" -eq 1 ]]; then
    OUT=$(python3 - "$TRANSCRIPT" "$DURATION_S" "$MODEL" "$ELAPSED" <<'PYEOF'
import json, sys
text, duration, model, elapsed = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
obj = {
    "text": text,
    "duration_s": float(duration) if duration else None,
    "model": model,
    "elapsed_s": int(elapsed),
}
print(json.dumps(obj, indent=2))
PYEOF
)
    if [[ -n "$OUTPUT" ]]; then
        printf '%s\n' "$OUT" > "$OUTPUT"
        echo "Saved: $OUTPUT" >&2
    else
        printf '%s\n' "$OUT"
    fi
else
    if [[ -n "$OUTPUT" ]]; then
        printf '%s\n' "$TRANSCRIPT" > "$OUTPUT"
        echo "Saved: $OUTPUT" >&2
    else
        printf '%s\n' "$TRANSCRIPT"
    fi
fi
