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

- **Auth**: `GOOGLE_API_KEY` — auto-loaded from `~/.env` by `generate.py`
- **Default style**: Read from `raccoon-style.txt` (in this skill's directory) by `generate.py`
- **Reference image**: Auto-resolved by `generate.py` (searches `~/gits/blog*/images/raccoon-nerd.webp`)
- **Low-level script**: `gemini-image.sh` handles single API calls (used internally by `generate.py`)
- **Generation wrapper**: `../image-explore/generate.py` handles env loading, style, ref image, and parallel batch execution

When `--style` is provided, it **replaces** the default raccoon style entirely (it is not appended).

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

Use `generate.py` from the `image-explore` skill. It handles env loading (`~/.env`), raccoon style
(from `raccoon-style.txt`), reference image resolution, and parallel batch execution automatically.

1. Resolve the script path:

   ```bash
   CHOP_ROOT="$(cd "$(dirname "$(readlink -f ~/.claude/skills/gen-image/SKILL.md)")" && git rev-parse --show-toplevel)"
   GEN="$CHOP_ROOT/skills/image-explore/generate.py"
   ```

2. **Single image:**

   ```bash
   python3 "$GEN" --scene "Raccoon lifting kettlebell in a gym" --shirt "FIT" --output raccoon-kettlebell.webp
   ```

   Pass `--aspect`, `--ref`, or `--style` to override defaults.

3. **Multiple images (parallel):** Write a JSON file and use batch mode:

   ```json
   [
     {
       "scene": "Raccoon lifting kettlebell in a gym",
       "shirt": "FIT",
       "output": "raccoon-kettlebell.webp"
     },
     {
       "scene": "Raccoon at family picnic",
       "shirt": "FAMILY",
       "output": "raccoon-picnic.webp"
     }
   ]
   ```

   ```bash
   python3 "$GEN" --batch illustrations.json --aspect 3:4
   ```

4. After generation, show each image to the user by reading the file with the Read tool (which renders images inline).

5. If generation fails, report the error and ask if the user wants to retry with a modified prompt or skip.

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

- **Missing API key**: `generate.py` auto-loads from `~/.env`. If still missing, tell the user to set `GOOGLE_API_KEY`
- **API error**: Show the error message, suggest checking the API key or endpoint
- **No jq**: The helper script (`gemini-image.sh`) requires `jq`
- **No cwebp**: Images will be saved as PNG instead of WebP — inform the user

## Safety

- Always confirm before generating (API calls cost money)
- Never generate more than 5 images in a single run without explicit user approval
- Show each generated image to the user for review
