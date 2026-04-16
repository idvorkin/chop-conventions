#!/bin/bash
# ABOUTME: Generates speech audio via the Gemini 3.1 Flash TTS API.
# ABOUTME: Accepts text (arg or stdin), writes a WAV file to --output.
#
# Usage:
#   gemini-tts.sh "text to speak" --output /tmp/out.wav [--voice NAME] [--api-url URL]
#   gemini-tts.sh "text" --output /tmp/out.wav --style-prompt "Speak in a warm baritone."
#   gemini-tts.sh "text" --output /tmp/out.wav --style-file voices/freud.txt
#   echo "text to speak" | gemini-tts.sh --output /tmp/out.wav
#
# Flags:
#   --output PATH       (required) destination WAV file
#   --voice NAME        prebuilt Gemini voice (default: Charon)
#   --style-prompt TXT  director's-notes prefix prepended to the text
#   --style-file PATH   read a multiline style directive from a file (comment
#                       lines starting with '#' are stripped)
#   --api-url URL       override Gemini endpoint
#   --help / -h         show this help
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
STYLE_PROMPT=""
STYLE_FILE=""
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
        --style-prompt)
            STYLE_PROMPT="$2"
            shift 2
            ;;
        --style-file)
            STYLE_FILE="$2"
            shift 2
            ;;
        --api-url)
            API_URL="$2"
            shift 2
            ;;
        --help|-h)
            sed -n '3,26p' "$0"
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

# Build the style directive: --style-prompt wins if both supplied. Otherwise
# --style-file's non-comment lines concatenated. The directive is prepended
# to the text as a director's-note prefix, which Gemini honors (per
# simonw's 2026-04-15 Gemini TTS write-up).
if [[ -n "$STYLE_PROMPT" ]]; then
    STYLE="$STYLE_PROMPT"
elif [[ -n "$STYLE_FILE" ]]; then
    if [[ ! -f "$STYLE_FILE" ]]; then
        echo "Error: --style-file not found: $STYLE_FILE" >&2
        exit 2
    fi
    # Strip comment lines (leading #), keep the rest, collapse to one paragraph.
    STYLE=$(grep -v '^\s*#' "$STYLE_FILE" | sed '/^\s*$/d' | tr '\n' ' ' | sed 's/  */ /g; s/^ //; s/ $//')
else
    STYLE=""
fi

if [[ -n "$STYLE" ]]; then
    TEXT="${STYLE}

Spoken text: ${TEXT}"
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

# Auth via x-goog-api-key header instead of ?key= URL parameter, so the key
# never appears in shell tracing (`set -x`), curl's own verbose output, or
# proxy access logs. One retry on transient 5xx / network errors. Note:
# `set -x` will still expose the TEXT and headers; treat traces accordingly.
HTTP_STATUS_FILE="$WORK_DIR/http_status"
curl_once() {
    curl -sS \
        -o "$RESPONSE_FILE" \
        -w '%{http_code}' \
        -X POST \
        "$API_URL" \
        -H "Content-Type: application/json" \
        -H "x-goog-api-key: ${GOOGLE_API_KEY}" \
        -d @"$PAYLOAD_FILE" > "$HTTP_STATUS_FILE" 2> "$WORK_DIR/curl.err"
    return $?
}

CURL_RC=0
curl_once || CURL_RC=$?
HTTP_STATUS=$(cat "$HTTP_STATUS_FILE" 2>/dev/null || echo "")

# Retry once on transient failures: connection errors (curl_rc != 0) or 5xx.
if [[ "$CURL_RC" -ne 0 ]] || [[ "$HTTP_STATUS" =~ ^5[0-9][0-9]$ ]]; then
    echo "Transient failure (rc=$CURL_RC http=$HTTP_STATUS), retrying after 1.5s..." >&2
    sleep 1.5
    CURL_RC=0
    curl_once || CURL_RC=$?
    HTTP_STATUS=$(cat "$HTTP_STATUS_FILE" 2>/dev/null || echo "")
fi

if [[ "$CURL_RC" -ne 0 ]]; then
    echo "Error: curl failed (exit $CURL_RC)" >&2
    head -c 2048 "$WORK_DIR/curl.err" >&2
    echo >&2
    exit 1
fi

# Non-2xx HTTP status is an error — even if the body is JSON without .error
# (e.g. 4xx from a malformed header). Let the .error / safety-filter parsers
# below take priority when the body is structured, but surface raw 4xx/5xx
# when there's no parseable error message.
if [[ ! "$HTTP_STATUS" =~ ^2[0-9][0-9]$ ]]; then
    if ! jq -e '.error // empty' "$RESPONSE_FILE" >/dev/null 2>&1; then
        echo "Error: HTTP $HTTP_STATUS from Gemini TTS" >&2
        head -c 2048 "$RESPONSE_FILE" >&2
        echo >&2
        exit 1
    fi
    # else: fall through to the .error.message parser below, which gives a
    # cleaner message
fi

ERROR=$(jq -r '.error.message // empty' "$RESPONSE_FILE" 2>/dev/null)
if [[ -n "$ERROR" ]]; then
    echo "API Error: $ERROR" >&2
    echo "Full response (first 2KB):" >&2
    head -c 2048 "$RESPONSE_FILE" >&2
    echo >&2
    exit 1
fi

# Safety-filter rejection: Gemini returns HTTP 200 with no audio parts and
# either promptFeedback.blockReason (input blocked) or candidates[0].finishReason
# set to SAFETY / PROHIBITED_CONTENT / OTHER (output blocked). These used to
# surface as a generic "No audio data in response" — unhelpful for the caller
# because nothing is wrong with the code or network.
BLOCK_REASON=$(jq -r '.promptFeedback.blockReason // empty' "$RESPONSE_FILE" 2>/dev/null)
FINISH_REASON=$(jq -r '.candidates[0].finishReason // empty' "$RESPONSE_FILE" 2>/dev/null)
case "$FINISH_REASON" in
    STOP|MAX_TOKENS|"") ;;  # normal / truncated-but-ok / absent
    *)
        # SAFETY / PROHIBITED_CONTENT / RECITATION / LANGUAGE / OTHER etc.
        echo "Error: Gemini TTS refused to generate audio (finishReason=$FINISH_REASON)" >&2
        if [[ -n "$BLOCK_REASON" ]]; then
            echo "  promptFeedback.blockReason=$BLOCK_REASON" >&2
        fi
        echo "  Most common cause: prosody tags like [excited] / [whisper]" >&2
        echo "  combined with certain content trip the safety filter." >&2
        echo "  Try retrying without the directorial tag, or rephrase." >&2
        exit 1
        ;;
esac
if [[ -n "$BLOCK_REASON" ]]; then
    echo "Error: Gemini TTS refused the prompt (blockReason=$BLOCK_REASON)" >&2
    RATINGS=$(jq -c '.promptFeedback.safetyRatings // []' "$RESPONSE_FILE" 2>/dev/null)
    [[ -n "$RATINGS" && "$RATINGS" != "[]" ]] && echo "  safetyRatings=$RATINGS" >&2
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
    echo "Error: No audio data in response (finishReason=${FINISH_REASON:-<none>})" >&2
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
