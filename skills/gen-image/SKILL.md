---
name: gen-image
description: "Analyze content and generate illustrations via Gemini image API"
argument-hint: "<post-or-topic> [--style 'description'] [--ref 'image-path'] [--api-url 'gemini-endpoint']"
allowed-tools: Bash, Read, Write, Glob, Grep, AskUserQuestion, WebFetch
---

# Generate Illustrations with Gemini

Analyze a blog post or topic, propose illustrations, and generate them via the Gemini image generation API.

## Arguments

Parse the user's input for:

- **Target**: A file path (e.g., `_d/four-healths.md`) or a freeform topic (e.g., "meditation benefits")
- **`--style 'description'`**: Override the default illustration style entirely
- **`--ref 'path'`**: One or more reference images for character consistency (can be repeated). When using the default raccoon style, **always** pass the canonical reference image (see below) unless the user opts out
- **`--api-url 'url'`**: Override the Gemini API endpoint (default below)
- **`--count N`**: Max number of images to generate (default: 3)
- **`--aspect 'W:H'`**: Aspect ratio via `imageConfig` (default: 3:4, portrait). Valid values: `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9`

## Configuration

- **Default API URL**: `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent`
- **Auth**: Reads `GOOGLE_API_KEY` from environment
- **Helper script**: `gemini-image.sh` in the same directory as this skill

### Default Style (Raccoon)

When no `--style` is given, use this base style:

> Cute anthropomorphic raccoon character with chibi proportions (oversized head, small body), dark raccoon mask markings around eyes, big friendly dark eyes, small black nose, round brown ears with lighter inner ear, soft brown felt/plush fur, striped ringed tail with brown and dark brown bands. Wearing big round rainbow-colored glasses (frames cycle through red, orange, yellow, green, blue, purple), green t-shirt with bold white text, blue denim shorts, IMPORTANT: mismatched Crocs shoes — one BLUE Croc on the left foot and one YELLOW Croc on the right foot (never the same color on both feet). Soft plush 3D/vinyl toy illustration style, studio softbox lighting, clean warm pastel background, subtle vintage film grain, children's book style. Full body.

When `--style` is provided, it **replaces** the default entirely (it is not appended).

### Canonical Reference Image

For raccoon-style generations, always pass a reference image to maintain character consistency across generations. The canonical reference is:

```
~/gits/blog7/images/raccoon-nerd.webp
```

This image contains the clearest full-body shot with all defining traits visible. Pass it as a reference image to every raccoon generation unless the user explicitly opts out.

## Workflow

### Phase 1: Read & Analyze Content

If the target is a file path:

1. Read the file
2. Identify the main themes/sections
3. Note any existing images (look for `blob_image`, `local_image`, `image_float` includes, and raw markdown images)
4. Identify sections that would benefit from illustrations — prioritize sections that have no images yet

If the target is a freeform topic:

1. Use it directly as the theme for image generation
2. Skip the content analysis and go straight to Phase 2

### Phase 2: Design Illustrations

For each illustration opportunity, prepare:

- **Section**: Which part of the post it enhances (or "standalone" for topic-based)
- **Filename**: Following the project convention — `raccoon-{descriptor}.webp` for raccoon style, `{descriptor}.webp` otherwise
- **Prompt**: A detailed generation prompt that combines the style + the specific scene/action
  - For raccoon style, include a `Shirt text: 'SOMETHING'` directive relevant to the section
  - Include the aspect ratio in the prompt (e.g., "portrait orientation, 3:4 aspect ratio")

Present **at most** `--count` illustrations (default 3).

### Phase 3: Confirm with User

Present the illustration plan as a table:

| #   | Section | Filename                | Prompt Summary                                  |
| --- | ------- | ----------------------- | ----------------------------------------------- |
| 1   | Health  | raccoon-kettlebell.webp | Raccoon lifting kettlebell, shirt: "FIT FELLOW" |
| 2   | Family  | raccoon-picnic.webp     | Raccoon at family picnic, shirt: "FAMILY TIME"  |

Ask the user to approve, modify, or remove items before generating. Use `AskUserQuestion` to confirm.

### Phase 4: Generate Images

For each approved illustration:

1. Verify `GOOGLE_API_KEY` is set:

   ```bash
   [[ -n "${GOOGLE_API_KEY:-}" ]] && echo "API key is set" || echo "ERROR: GOOGLE_API_KEY not set"
   ```

2. Locate the helper script:

   ```bash
   # The helper script is in the same directory as this skill
   SKILL_DIR="$(dirname "$(find ~/gits/chop-conventions/skills/gen-image -name 'gemini-image.sh' -print -quit)")"
   ```

3. Resolve the reference image path (for raccoon style):

   ```bash
   REF_IMAGE="$(ls ~/gits/blog7/images/raccoon-nerd.webp 2>/dev/null || ls ~/gits/blog*/images/raccoon-nerd.webp 2>/dev/null | head -1)"
   ```

4. Generate each image (with reference image and aspect ratio):

   ```bash
   ASPECT_RATIO="3:4" bash "$SKILL_DIR/gemini-image.sh" \
     "Generate this character in a new scene: THE FULL PROMPT HERE" \
     "/path/to/output/filename.webp" \
     "API_URL_IF_OVERRIDDEN" \
     "$REF_IMAGE"
   ```

   Without reference images (custom style):

   ```bash
   ASPECT_RATIO="3:4" bash "$SKILL_DIR/gemini-image.sh" \
     "THE FULL PROMPT HERE" \
     "/path/to/output/filename.webp" \
     "API_URL_IF_OVERRIDDEN"
   ```

5. After each image is generated, show it to the user by reading the file with the Read tool (which renders images inline)

6. If generation fails, report the error and ask if the user wants to retry with a modified prompt or skip

### Phase 5: Insert References (Optional)

Ask the user if they want the images inserted into the post. If yes:

- For images stored in the blog's `assets/images/` directory, use:

  ```
  {% include local_image_float_right.html src="filename.webp" %}
  ```

- For images stored in the external blob repo (`idvorkin/blob`), use:

  ```
  {% include blob_image_float_right.html src="blog/filename.webp" %}
  ```

- Insert the include tag just below the relevant section header (after any front matter or introductory text)

If the target was a freeform topic (not a file), skip this phase — just tell the user where the files were saved.

## Output Directory

- If editing a blog post, save images to the same directory convention the blog uses (ask user if unsure)
- If freeform topic, save to `~/tmp/` and tell the user the paths

## Error Handling

- **Missing API key**: Stop immediately, tell the user to `export GOOGLE_API_KEY=...`
- **API error**: Show the error message, suggest checking the API key or endpoint
- **No jq**: The helper script requires `jq` — check and suggest `brew install jq` or `apt install jq`
- **No cwebp**: Images will be saved as PNG instead of WebP — inform the user

## Safety

- Always confirm before generating (API calls cost money)
- Never generate more than 5 images in a single run without explicit user approval
- Show each generated image to the user for review
