---
name: image-explore
description: "Brainstorm multiple visual directions for a blog image, generate them in parallel, build a comparison page, and optionally publish as a shareable gist."
argument-hint: "<post-or-topic> [--count N] [--variants N] [--style 'override'] [--aspect 'W:H']"
allowed-tools: Bash, Read, Write, Glob, Grep, AskUserQuestion, WebFetch
---

# Image Explore - Visual Direction Brainstorming

Generate multiple distinct visual directions for a blog image, render them all in parallel, build a comparison page, and optionally publish as a shareable gist for feedback.

## Arguments

Parse the user's input for:

- **Target**: A file path (e.g., `_d/ai-native-manager.md`) or a freeform topic (e.g., "chaos of AI adoption")
- **`--count N`**: Number of directions to brainstorm and generate (default: 5, max: 8)
- **`--variants N`**: Number of minor variants per direction (default: 1, max: 3). Each variant tweaks the scene (different angle, lighting, composition) while keeping the same concept and shirt text.
- **`--style 'description'`**: Override the default illustration style (passed through to gen-image)
- **`--aspect 'W:H'`**: Aspect ratio (default: 3:4). Valid: `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `9:16`, `16:9`
- **`--ref 'path'`**: Override reference image (default: raccoon canonical ref)

## Workflow

### Phase 1: Analyze Content

If the target is a file path:

1. Read the file
2. Identify the **hook** — what's the one idea a reader should remember?
3. Note key metaphors, section themes, emotional arc
4. Check for existing images (look for `imagefeature`, `local_image`, `blob_image` includes)

If the target is a freeform topic:

1. Use it directly as the creative brief
2. Skip to Phase 2

### Phase 2: Brainstorm Directions

This is the creative core. Generate `--count` **distinct visual directions**. Each direction must have:

- **Name**: 2-4 evocative words (e.g., "Circus Ringmaster", "Surfing the Wave")
- **Section**: Which part of the post it maps to (or "standalone")
- **Scene**: One-sentence description of the image
- **Vibe**: What feeling it evokes (e.g., "controlled chaos", "quiet confidence")
- **Shirt text**: For raccoon style, what the shirt reads (max 8 chars)

**Directions must be meaningfully different.** Vary across these axes:

- Literal vs. metaphorical
- Action vs. stillness
- Humor vs. gravitas
- Individual vs. group scene
- Indoor vs. outdoor / grounded vs. fantastical

Avoid generating 5 variations of the same idea. If the post has one dominant metaphor, use it for at most 2 directions and find fresh angles for the rest.

Present directions as a table:

| #   | Name             | Section       | Scene                                    | Vibe           | Shirt   |
| --- | ---------------- | ------------- | ---------------------------------------- | -------------- | ------- |
| A   | Mission Control  | Year of Chaos | Raccoon at NASA console, screens on fire | "This is fine" | SHIP IT |
| B   | Surfing the Wave | AI Adoption   | Raccoon surfing tidal wave of AI debris  | Riding chaos   | SHIP IT |

Confirm with user via `AskUserQuestion` before generating. User may add, remove, or modify directions.

**If `--variants` > 1:** After user approves the directions, craft variant scenes for each. Each variant keeps the same concept, shirt text, and vibe, but varies the specific scene description (different angle, setting detail, composition, or lighting). Do NOT present variant scenes for approval — just generate them.

### Phase 3: Generate Images in Parallel

1. Resolve the script path once:

   ```bash
   CHOP_ROOT="$(cd "$(dirname "$(readlink -f ~/.claude/skills/image-explore/SKILL.md)")" && git rev-parse --show-toplevel)"
   GEN="$CHOP_ROOT/skills/image-explore/generate.py"
   ```

2. Write a `directions.json` file with all directions (used by both Phase 3 and Phase 4).

   **Without variants** (1 entry per direction):

   ```json
   [
     {
       "name": "Mission Control",
       "section": "Year of Chaos",
       "vibe": "This is fine",
       "shirt": "SHIP IT",
       "scene": "Raccoon at NASA console, screens showing fire",
       "output": "mission-control.webp"
     }
   ]
   ```

   **With variants** (multiple entries per direction, grouped by `group` field):

   ```json
   [
     {
       "name": "Mission Control v1",
       "group": "Mission Control",
       "section": "Year of Chaos",
       "vibe": "This is fine",
       "shirt": "SHIP IT",
       "scene": "Raccoon at NASA console, screens showing fire, dramatic front view",
       "output": "mission-control-v1.webp"
     },
     {
       "name": "Mission Control v2",
       "group": "Mission Control",
       "section": "Year of Chaos",
       "vibe": "This is fine",
       "shirt": "SHIP IT",
       "scene": "Raccoon at NASA console seen from side, leaning back in chair sipping tea",
       "output": "mission-control-v2.webp"
     }
   ]
   ```

   The `group` field enables `build-page.py` to group variants under a shared heading.
   Output filenames follow the pattern `{slug}-v{N}.webp` when using variants.

3. **Generate all images in parallel** with a single command:

   ```bash
   python3 "$GEN" --batch directions.json
   ```

   Pass `--aspect`, `--ref`, or `--style` if overriding defaults. The script handles env loading,
   prompt assembly, ref image resolution, and parallel execution via thread pool
   (secrets never leak into command strings).

4. After all complete, show each image to the user with the `Read` tool.

### Phase 4: Build Comparison Page

Build and serve the comparison page (reuses the same `directions.json` —
`build-page.py` reads `name`/`section`/`vibe`/`shirt` and accepts either `image` or `output` for the file path):

```bash
python3 "$CHOP_ROOT/skills/image-explore/build-page.py" \
  --title "Image Explore: Topic Name" \
  --dir docs/image-explore-topic/ \
  --images-dir images/ \
  directions.json
```

Options:

- `--images-dir PATH`: Where to find generated images (default: current directory). Useful when images were written to a different directory than where you run the command (e.g., `images/`).
- `--no-serve`: Skip starting the HTTP server.

This creates the showboat doc, converts images, generates HTML via pandoc,
and starts a local HTTP server. It prints the Tailscale URL.

When `directions.json` contains entries with a `group` field, the page groups variants
under shared direction headings with sub-headers for each variant.

### Phase 5: Publish (Ask First)

Ask the user: "Want to publish this as a shareable gist?"

If yes, use the helper script:

```bash
python3 "$CHOP_ROOT/skills/image-explore/publish-gist.py" demo.html --title "Description"
```

This handles: gist creation, image conversion to JPEG, URL rewriting, git push. It prints the gisthost URL.

### Phase 6: Apply Selection (Optional)

If the user picks a winner, offer to:

1. Update the blog post's `imagefeature` frontmatter
2. Update any `local_image_float_right` includes
3. Copy the chosen image to the blog's images directory with a permanent name

## Default Raccoon Style

When no `--style` is given, use this base style (same as gen-image):

> Cute anthropomorphic raccoon character with chibi proportions (oversized head, small body), dark raccoon mask markings around eyes, big friendly dark eyes, small black nose, round brown ears with lighter inner ear, soft brown felt/plush fur, striped ringed tail with brown and dark brown bands. Wearing big round rainbow-colored glasses (frames cycle through red, orange, yellow, green, blue, purple), green t-shirt with bold white text, blue denim shorts, IMPORTANT: mismatched Crocs shoes — one BLUE Croc on the left foot and one YELLOW Croc on the right foot (never the same color on both feet). Soft plush 3D/vinyl toy illustration style, studio softbox lighting, clean warm pastel background, subtle vintage film grain, children's book style. Full body.

## Error Handling

- **Missing API key**: Check `~/.env` first, then stop and ask user
- **Generation failure**: Report which direction failed, ask to retry or skip
- **showboat not installed**: Fall back to plain markdown + pandoc without showboat
- **magick not installed**: Warn, use PNG directly (larger files)

## Tips

- After user picks a winner, they may want to iterate on it — offer to re-run gen-image with refinements
- If a direction doesn't render well, suggest prompt tweaks rather than just re-rolling
- Keep the comparison page around for reference even after picking a winner
