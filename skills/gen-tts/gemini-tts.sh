#!/bin/bash
# ABOUTME: Generates speech audio via the Gemini 3.1 Flash TTS API.
# ABOUTME: Accepts text (arg or stdin), writes a WAV file to --output.
#
# Usage:
#   gemini-tts.sh "text to speak" --output /tmp/out.wav [--voice NAME] [--api-url URL]
#   echo "text to speak" | gemini-tts.sh --output /tmp/out.wav
#
# Environment:
#   GOOGLE_API_KEY   (required) — your Google API key; auto-sourced from ~/.env if unset
#
# Response format: Gemini returns raw 16-bit signed PCM at 24kHz mono, base64
# encoded in candidates[0].content.parts[*].inlineData.data. We wrap it in a
# canonical WAV header so the output file is directly playable.

set -euo pipefail

TEXT=""
OUTPUT=""
VOICE="Charon"
API_URL="https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-tts-preview:generateContent"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        --voice)
            VOICE="$2"
            shift 2
            ;;
        --api-url)
            API_URL="$2"
            shift 2
            ;;
        --help|-h)
            sed -n '3,14p' "$0"
            exit 0
            ;;
        --*)
            echo "Unknown flag: $1" >&2
            exit 2
            ;;
        *)
            if [[ -z "$TEXT" ]]; then
                TEXT="$1"
            else
                echo "Unexpected positional arg: $1" >&2
                exit 2
            fi
            shift
            ;;
    esac
done

# If no text arg, read stdin (when stdin is a pipe/file, not a tty)
if [[ -z "$TEXT" && ! -t 0 ]]; then
    TEXT=$(cat)
fi

if [[ -z "$TEXT" ]]; then
    echo "Error: no text provided (pass as arg or via stdin)" >&2
    exit 2
fi

if [[ -z "$OUTPUT" ]]; then
    echo "Error: --output <path.wav> is required" >&2
    exit 2
fi

# Auto-source ~/.env if API key is not already set
if [[ -z "${GOOGLE_API_KEY:-}" && -f ~/.env ]]; then
    source ~/.env
fi

if [[ -z "${GOOGLE_API_KEY:-}" ]]; then
    echo "Error: GOOGLE_API_KEY environment variable is not set" >&2
    exit 1
fi

command -v jq >/dev/null 2>&1 || { echo "Error: jq is required" >&2; exit 1; }
command -v base64 >/dev/null 2>&1 || { echo "Error: base64 is required" >&2; exit 1; }

WORK_DIR=$(mktemp -d /tmp/gemini-tts-XXXXXX)
trap 'rm -rf "$WORK_DIR"' EXIT
PAYLOAD_FILE="$WORK_DIR/payload.json"
RESPONSE_FILE="$WORK_DIR/response.json"
PCM_FILE="$WORK_DIR/audio.pcm"

jq -n \
    --arg text "$TEXT" \
    --arg voice "$VOICE" \
    '{
        contents: [{
            parts: [{ text: $text }]
        }],
        generationConfig: {
            responseModalities: ["AUDIO"],
            speechConfig: {
                voiceConfig: {
                    prebuiltVoiceConfig: {
                        voiceName: $voice
                    }
                }
            }
        }
    }' > "$PAYLOAD_FILE"

echo "Calling Gemini TTS API (voice=$VOICE)..." >&2

curl -s -X POST \
    "${API_URL}?key=${GOOGLE_API_KEY}" \
    -H "Content-Type: application/json" \
    -d @"$PAYLOAD_FILE" > "$RESPONSE_FILE"

ERROR=$(jq -r '.error.message // empty' "$RESPONSE_FILE" 2>/dev/null)
if [[ -n "$ERROR" ]]; then
    echo "API Error: $ERROR" >&2
    echo "Full response (first 2KB):" >&2
    head -c 2048 "$RESPONSE_FILE" >&2
    echo >&2
    exit 1
fi

# Extract base64 audio data — find the first inlineData part with an audio mime type
AUDIO_DATA=$(jq -r '
    [.candidates[0].content.parts[]? | select((.inlineData.mimeType // "") | startswith("audio/"))]
    | first | .inlineData.data // empty' "$RESPONSE_FILE")

MIME_TYPE=$(jq -r '
    [.candidates[0].content.parts[]? | select((.inlineData.mimeType // "") | startswith("audio/"))]
    | first | .inlineData.mimeType // "audio/pcm"' "$RESPONSE_FILE")

if [[ -z "$AUDIO_DATA" ]]; then
    echo "Error: No audio data in response" >&2
    echo "Response preview:" >&2
    jq '.candidates[0] // .' "$RESPONSE_FILE" 2>/dev/null | head -40 >&2 || head -c 2048 "$RESPONSE_FILE" >&2
    exit 1
fi

# Decode base64 to raw PCM
echo "$AUDIO_DATA" | base64 -d > "$PCM_FILE"
PCM_BYTES=$(wc -c < "$PCM_FILE")
if [[ "$PCM_BYTES" -lt 1000 ]]; then
    echo "Error: Decoded audio is only ${PCM_BYTES} bytes — likely truncated or empty" >&2
    exit 1
fi

# Gemini TTS returns PCM: 16-bit signed little-endian, 24kHz, mono.
# Parse declared params from mime type if present (e.g. "audio/L16;codec=pcm;rate=24000").
SAMPLE_RATE=24000
case "$MIME_TYPE" in
    *rate=*)
        candidate="${MIME_TYPE##*rate=}"
        candidate="${candidate%%;*}"
        if [[ "$candidate" =~ ^[0-9]+$ ]]; then
            SAMPLE_RATE="$candidate"
        fi
        ;;
esac

# Wrap PCM in a WAV container.
# WAV header: 44 bytes for PCM mono 16-bit.
#   RIFF<size>WAVEfmt <16><1><1><rate><byterate><blockalign><16>data<size>
CHANNELS=1
BITS_PER_SAMPLE=16
BYTE_RATE=$(( SAMPLE_RATE * CHANNELS * BITS_PER_SAMPLE / 8 ))
BLOCK_ALIGN=$(( CHANNELS * BITS_PER_SAMPLE / 8 ))
DATA_SIZE=$PCM_BYTES
RIFF_SIZE=$(( DATA_SIZE + 36 ))

# Use python for little-endian header pack — portable across bash versions and no `printf` byte-order surprises.
python3 - "$OUTPUT" "$PCM_FILE" "$SAMPLE_RATE" "$CHANNELS" "$BITS_PER_SAMPLE" <<'PYEOF'
import struct
import sys
out, pcm, rate, channels, bps = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
with open(pcm, "rb") as f:
    data = f.read()
byte_rate = rate * channels * bps // 8
block_align = channels * bps // 8
header = b"RIFF" + struct.pack("<I", len(data) + 36) + b"WAVE"
header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, byte_rate, block_align, bps)
header += b"data" + struct.pack("<I", len(data))
with open(out, "wb") as f:
    f.write(header + data)
PYEOF

WAV_BYTES=$(wc -c < "$OUTPUT")
DURATION_S=$(python3 -c "print(round($DATA_SIZE / $BYTE_RATE, 2))")
echo "Saved: $OUTPUT (${WAV_BYTES} bytes, ~${DURATION_S}s, ${SAMPLE_RATE}Hz mono 16-bit)" >&2
