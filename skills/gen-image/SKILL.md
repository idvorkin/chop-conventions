---
name: gen-image
description: "Analyze content and generate illustrations via Gemini image API"
argument-hint: "<post-or-topic> [--style 'description'] [--api-url 'gemini-endpoint']"
allowed-tools: Bash, Read, Write, Glob, Grep, AskUserQuestion, WebFetch
---

# Generate Illustrations with Gemini

Analyze a blog post or topic, propose illustrations, and generate them via the Gemini image generation API.

## Arguments

Parse the user's input for:

- **Target**: A file path (e.g., `_d/four-healths.md`) or a freeform topic (e.g., "meditation benefits")
- **`--style 'description'`**: Override the default illustration style entirely
- **`--api-url 'url'`**: Override the Gemini API endpoint (default below)
- **`--count N`**: Max number of images to generate (default: 3)
- **`--aspect 'W:H'`**: Aspect ratio hint in the prompt (default: 3:4, portrait)

## Configuration

- **Default API URL**: `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent`
- **Auth**: Reads `GOOGLE_API_KEY` from environment
- **Helper script**: `gemini-image.sh` in the same directory as this skill

### Default Style (Raccoon)

When no `--style` is given, use this base style:

> Cute anthropomorphic raccoon character, big rainbow round glasses, green t-shirt with bold white text, blue left Croc and yellow right Croc, soft plush 3D/vinyl illustration, big friendly eyes, studio softbox lighting, clean pastel background, subtle vintage film grain, children's book style. Full body.

When `--style` is provided, it **replaces** the default entirely (it is not appended).

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

3. Generate each image:

   ```bash
   bash "$SKILL_DIR/gemini-image.sh" \
     "THE FULL PROMPT HERE" \
     "/path/to/output/filename.webp" \
     "API_URL_IF_OVERRIDDEN"
   ```

4. After each image is generated, show it to the user by reading the file with the Read tool (which renders images inline)

5. If generation fails, report the error and ask if the user wants to retry with a modified prompt or skip

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
