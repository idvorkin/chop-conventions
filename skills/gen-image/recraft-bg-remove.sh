#!/usr/bin/env bash
# recraft-bg-remove.sh — strip image background via Recraft API
# Usage: recraft-bg-remove.sh <input.png|webp|jpg> <output.png>
#
# Reads RECRAFT_API_TOKEN from ~/.env if not already in env.
# Costs ~$0.01/call. See https://www.recraft.ai/docs/api-reference/endpoints
# for limits (5MB, 16MP, 4096 max dim, 256 min dim, PNG/JPG/WEBP).
set -euo pipefail

if [ -z "${RECRAFT_API_TOKEN:-}" ] && [ -f ~/.env ]; then
    set -a
    # shellcheck disable=SC1090
    . ~/.env
    set +a
fi
: "${RECRAFT_API_TOKEN:?RECRAFT_API_TOKEN not set in ~/.env or environment}"

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <input> <output>" >&2
    exit 1
fi

INPUT="$1"
OUTPUT="$2"

if [ ! -f "$INPUT" ]; then
    echo "Input file not found: $INPUT" >&2
    exit 1
fi

# Use b64_json so we get bytes back in one round trip (no transient URL race).
response=$(curl -sS --fail-with-body -X POST \
    https://external.api.recraft.ai/v1/images/removeBackground \
    -H "Authorization: Bearer $RECRAFT_API_TOKEN" \
    -F "file=@${INPUT}" \
    -F "response_format=b64_json")

b64=$(echo "$response" | jq -er '.image.b64_json')
echo "$b64" | base64 -d > "$OUTPUT"

# Sanity: file should exist + be > 1KB
if [ ! -s "$OUTPUT" ] || [ "$(stat -c%s "$OUTPUT")" -lt 1024 ]; then
    echo "Output looks empty or truncated: $OUTPUT" >&2
    exit 1
fi

echo "OK: $OUTPUT ($(stat -c%s "$OUTPUT") bytes)"
