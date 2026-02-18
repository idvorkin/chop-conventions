#!/bin/bash
# ABOUTME: Generates images via the Gemini image generation API.
# ABOUTME: Supports reference images for character consistency.
#
# Usage: gemini-image.sh <prompt> <output-file> [api-url] [ref-image...]
#
# Environment:
#   GOOGLE_API_KEY   (required) — your Google API key
#   ASPECT_RATIO     (optional) — aspect ratio, e.g. "3:4" (default: 3:4)
#
# Reference images are passed as additional positional arguments after
# the API URL. They are sent as inline_data parts alongside the text
# prompt to maintain character consistency across generations.

set -euo pipefail

PROMPT="${1:?Usage: gemini-image.sh <prompt> <output-file> [api-url] [ref-image...]}"
OUTPUT="${2:?Usage: gemini-image.sh <prompt> <output-file> [api-url] [ref-image...]}"
API_URL="${3:-https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent}"
REF_IMAGES=()
if [[ $# -gt 3 ]]; then
    shift 3
    REF_IMAGES=("$@")
fi
ASPECT_RATIO="${ASPECT_RATIO:-3:4}"

if [[ -z "${GOOGLE_API_KEY:-}" ]]; then
    echo "Error: GOOGLE_API_KEY environment variable is not set" >&2
    exit 1
fi

# Use temp files to avoid shell argument length limits with large base64 data
PARTS_FILE=$(mktemp /tmp/gemini-parts-XXXXXX.json)
PAYLOAD_FILE=$(mktemp /tmp/gemini-payload-XXXXXX.json)
RESPONSE_FILE=$(mktemp /tmp/gemini-response-XXXXXX.json)
trap 'rm -f "$PARTS_FILE" "$PAYLOAD_FILE" "$RESPONSE_FILE"' EXIT

# Build the parts array: text prompt first, then any reference images
jq -n --arg prompt "$PROMPT" '[{ text: $prompt }]' > "$PARTS_FILE"

for ref in "${REF_IMAGES[@]}"; do
    if [[ ! -f "$ref" ]]; then
        echo "Warning: Reference image not found, skipping: $ref" >&2
        continue
    fi
    # Detect mime type from extension
    ref_ext="${ref##*.}"
    ref_ext="${ref_ext,,}"
    case "$ref_ext" in
        png)  ref_mime="image/png" ;;
        jpg|jpeg) ref_mime="image/jpeg" ;;
        webp) ref_mime="image/webp" ;;
        *)    ref_mime="image/png" ;;
    esac
    REF_B64_FILE=$(mktemp /tmp/gemini-ref-XXXXXX.b64)
    base64 -w0 "$ref" > "$REF_B64_FILE" 2>/dev/null || base64 "$ref" > "$REF_B64_FILE"
    jq --arg mime "$ref_mime" --rawfile data "$REF_B64_FILE" \
        '. + [{ inlineData: { mimeType: $mime, data: $data } }]' \
        "$PARTS_FILE" > "${PARTS_FILE}.tmp" && mv "${PARTS_FILE}.tmp" "$PARTS_FILE"
    rm -f "$REF_B64_FILE"
    echo "Attached reference image: $ref" >&2
done

# Build the request payload with imageConfig for aspect ratio
jq -n \
    --slurpfile parts "$PARTS_FILE" \
    --arg aspect "$ASPECT_RATIO" \
    '{
        contents: [{
            parts: $parts[0]
        }],
        generationConfig: {
            responseModalities: ["TEXT", "IMAGE"],
            imageConfig: {
                aspectRatio: $aspect
            }
        }
    }' > "$PAYLOAD_FILE"

echo "Calling Gemini API..." >&2

# Make the API call using file input to avoid argument length limits
curl -s -X POST \
    "${API_URL}?key=${GOOGLE_API_KEY}" \
    -H "Content-Type: application/json" \
    -d @"$PAYLOAD_FILE" > "$RESPONSE_FILE"
RESPONSE=$(cat "$RESPONSE_FILE")

# Check for errors in the response
ERROR=$(echo "$RESPONSE" | jq -r '.error.message // empty' 2>/dev/null)
if [[ -n "$ERROR" ]]; then
    echo "API Error: $ERROR" >&2
    exit 1
fi

# Extract base64 image data — find the first inline_data part with an image mime type
IMAGE_DATA=$(echo "$RESPONSE" | jq -r '
    [.candidates[0].content.parts[] | select(.inlineData.mimeType // "" | startswith("image/"))] |
    first | .inlineData.data // empty')

if [[ -z "$IMAGE_DATA" ]]; then
    echo "Error: No image data in response" >&2
    echo "Response preview:" >&2
    echo "$RESPONSE" | jq '.candidates[0].content.parts[] | keys' 2>/dev/null >&2 || echo "$RESPONSE" | head -20 >&2
    exit 1
fi

# Extract the mime type to determine the native format
MIME_TYPE=$(echo "$RESPONSE" | jq -r '
    [.candidates[0].content.parts[] | select(.inlineData.mimeType // "" | startswith("image/"))] |
    first | .inlineData.mimeType // "image/png"')

# Determine the native extension from the mime type
case "$MIME_TYPE" in
    image/png)  NATIVE_EXT="png" ;;
    image/jpeg) NATIVE_EXT="jpg" ;;
    image/webp) NATIVE_EXT="webp" ;;
    *)          NATIVE_EXT="png" ;;
esac

# Determine desired output format from the filename
OUTPUT_EXT="${OUTPUT##*.}"
OUTPUT_EXT="${OUTPUT_EXT,,}"  # lowercase

# Decode the image to a temp file first
TMPFILE=$(mktemp "/tmp/gemini-img-XXXXXX.${NATIVE_EXT}")
echo "$IMAGE_DATA" | base64 -d > "$TMPFILE"

# Convert if needed
if [[ "$OUTPUT_EXT" == "webp" && "$NATIVE_EXT" != "webp" ]]; then
    if command -v cwebp &>/dev/null; then
        cwebp -q 90 "$TMPFILE" -o "$OUTPUT" 2>/dev/null
        rm -f "$TMPFILE"
    else
        # Fall back: save as png with the requested name but warn
        echo "Warning: cwebp not found, saving as ${NATIVE_EXT} instead" >&2
        FALLBACK="${OUTPUT%.*}.${NATIVE_EXT}"
        mv "$TMPFILE" "$FALLBACK"
        OUTPUT="$FALLBACK"
    fi
elif [[ "$OUTPUT_EXT" == "$NATIVE_EXT" || "$OUTPUT_EXT" == "webp" && "$NATIVE_EXT" == "webp" ]]; then
    mv "$TMPFILE" "$OUTPUT"
else
    # Different format requested but no converter — save as native
    mv "$TMPFILE" "$OUTPUT"
fi

echo "Saved: $OUTPUT" >&2
