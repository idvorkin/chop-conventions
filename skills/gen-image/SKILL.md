---
name: gen-image
description: "Analyze content and generate illustrations via Gemini image API"
argument-hint: "<post-or-topic> [--count N] [--aspect W:H] [--style '...'] [--ref path] [--transparent] [--api-url url]"
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
- **`--transparent`**: Generate on magenta chroma-key background (`#FF00FF`), then strip it via ImageMagick **border-seeded flood fill**. The scanner samples pixels along every image edge and keeps only the ones that are actually near `#FF00FF` as flood seeds — so shots where Gemini rendered grass/scenery into some corners (which broke the earlier 4-corners-only approach when flood-fill started from grass at 30% fuzz and ate the subject) still strip cleanly. Only magenta pixels reachable from the image edges are made transparent; interior magenta-tinted pixels (pink fur highlights, glass reflections, lobster-claw reds) are preserved automatically. Fast (sub-second) and pixel-accurate — no ML needed. Requires `magick` (ImageMagick). If the subject fills the frame and no border pixel is near-magenta, the strip is skipped with an error rather than producing a swiss-cheese result. After the strip, two layered evals auto-run — see **Automatic eval** below.
- **`--no-eval`**: Skip the alpha-mask eval pass that looks for interior holes, residual magenta, and edge fringe (needs numpy/pillow/scipy — the `uv run --script` shebang installs them automatically, but plain `python3` invocations without `uv` may need this flag). The alpha-mean signal still runs.
- **`--eval-strict`**: Exit nonzero when any alpha-mask eval threshold trips. Useful when a calling agent wants to retry or fail loudly instead of silently shipping a broken alpha mask.

### Automatic eval

When `--transparent` is active, `generate.py` runs two complementary evals on the output and prints metrics to stderr.

**(1) Alpha-mean signal** (`evaluate_strip`, always on — same thresholds `test_generate.py` asserts):

- Status `healthy` — alpha mean in 15–85% band.
- Status `subject_eaten` — alpha mean below 15%; the strip ate the subject. Regenerate with a magenta border on all four sides.
- Status `nothing_stripped` — alpha mean above 85%; subject fills the frame. Widen the crop.

**(2) Alpha-mask quality signal** (`eval_alpha`, opt-out via `--no-eval`):

- `interior_hole_px` — transparent pixels enclosed by opaque (signals interior damage the alpha-mean misses: Swiss-cheese holes that read as shading on a colored preview background).
- `residual_magenta_px` — opaque pixels still near chroma color (signals trapped magenta pockets corner-seeded flood couldn't reach).
- `edge_fringe_px` — partial-alpha pixels (signals halo).

Output format:

```
eval [healthy] out.webp: alpha=51.3% size=74.0KB
[eval] /tmp/out.webp: holes=84, residual=132, fringe=0   [OK]
[eval] /tmp/out.webp: holes=9687, residual=0, fringe=0   [WARN: interior damage likely — check alpha mask]
```

Thresholds for the mask-quality signal are conservative by default (holes > 200, residual > 500, fringe > 2000). Pass `--no-eval` to skip it. Pass `--eval-strict` to exit nonzero when a mask-quality threshold trips.

**Why:** visual inspection on a light or dark background hides interior damage (holes read as shadow/shading). The alpha mask is the ground truth. Baking both evals into the skill makes them the default, so a silently-broken output can't ship. See [/hill-climbing](https://idvork.in/hill-climbing) for the "eval becomes regression guard" pattern.

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
   uv run "$GEN" single --scene "Raccoon lifting kettlebell in a gym" --shirt "FIT" --output raccoon-kettlebell.webp
   ```

   The script's PEP 723 shebang auto-installs deps (typer + numpy/pillow/scipy for the `--transparent` eval). Pass `--aspect`, `--ref`, or `--style` to override defaults. Under `--transparent`, pass `--no-eval` to skip the mask-quality eval on stock python3 callers without numpy/scipy, and `--eval-strict` to exit nonzero when any eval threshold trips.

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
   uv run "$GEN" batch illustrations.json --aspect 3:4
   ```

4. After generation, show each image to the user by reading the file with the Read tool (which renders images inline).

5. If generation fails, report the error and ask if the user wants to retry with a modified prompt or skip.

**Auto-eval runs on every generation.** When `--transparent` is set, `generate.py` runs two complementary evals right after the chroma-key pass — the alpha-mean signal (always) and the alpha-mask quality signal (interior holes, residual magenta, edge fringe; opt-out via `--no-eval`). Details and thresholds in the **Automatic eval** subsection above. See [/hill-climbing](https://idvork.in/hill-climbing) for the "eval becomes regression guard" pattern.

**Verifying transparent output.** Don't judge chroma-key quality by compositing on a solid background — interior holes read as the background color. Extract the alpha channel as a mask: `magick out.webp -alpha extract mask.png`. A clean mask is a solid silhouette; swiss-cheese holes mean the chroma ate interior color data.

**Never chain chroma passes on different magenta tones** (e.g. a second pass on `#E040E0` to catch pink shadow remnants). It eats magenta-tinted highlights inside fluffy characters. If the first pass has fringe, regenerate the source with a stricter prompt (`no shadow on ground, no gradient, no environment`) rather than filtering harder.

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
