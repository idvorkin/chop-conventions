#!/bin/bash
# Generate an image using the Gemini image generation API.
#
# Usage: gemini-image.sh <prompt> <output-file> [api-url]
#
# Environment:
#   GEMINI_API_KEY  (required) — your Gemini API key
#
# The script sends the prompt to the Gemini image generation endpoint,
# extracts the base64-encoded image from the response, and decodes it
# to the output file. If the output file ends in .webp and cwebp is
# available, the image is converted; otherwise it falls back to .png.

set -euo pipefail

PROMPT="${1:?Usage: gemini-image.sh <prompt> <output-file> [api-url]}"
OUTPUT="${2:?Usage: gemini-image.sh <prompt> <output-file> [api-url]}"
API_URL="${3:-https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent}"

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
    echo "Error: GEMINI_API_KEY environment variable is not set" >&2
    exit 1
fi

# Build the request payload
PAYLOAD=$(jq -n \
    --arg prompt "$PROMPT" \
    '{
        contents: [{
            parts: [{ text: $prompt }]
        }],
        generationConfig: {
            responseModalities: ["TEXT", "IMAGE"]
        }
    }')

echo "Calling Gemini API..." >&2

# Make the API call
RESPONSE=$(curl -s -X POST \
    "${API_URL}?key=${GEMINI_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")

# Check for errors in the response
ERROR=$(echo "$RESPONSE" | jq -r '.error.message // empty' 2>/dev/null)
if [[ -n "$ERROR" ]]; then
    echo "API Error: $ERROR" >&2
    exit 1
fi

# Extract base64 image data — find the first inline_data part with an image mime type
IMAGE_DATA=$(echo "$RESPONSE" | jq -r '
    [.candidates[0].content.parts[] | select(.inlineData.mimeType | startswith("image/"))] |
    first | .inlineData.data // empty')

if [[ -z "$IMAGE_DATA" ]]; then
    echo "Error: No image data in response" >&2
    echo "Response preview:" >&2
    echo "$RESPONSE" | jq '.candidates[0].content.parts[] | keys' 2>/dev/null >&2 || echo "$RESPONSE" | head -20 >&2
    exit 1
fi

# Extract the mime type to determine the native format
MIME_TYPE=$(echo "$RESPONSE" | jq -r '
    [.candidates[0].content.parts[] | select(.inlineData.mimeType | startswith("image/"))] |
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
