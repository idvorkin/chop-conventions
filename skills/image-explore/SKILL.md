---
name: image-explore
description: "Brainstorm multiple visual directions for a blog image, generate them in parallel, build a comparison page, and optionally publish as a shareable gist."
argument-hint: "<post-or-topic> [--count N] [--style 'override'] [--aspect 'W:H']"
allowed-tools: Bash, Read, Write, Glob, Grep, AskUserQuestion, WebFetch
---

# Image Explore - Visual Direction Brainstorming

Generate multiple distinct visual directions for a blog image, render them all in parallel, build a comparison page, and optionally publish as a shareable gist for feedback.

## Arguments

Parse the user's input for:

- **Target**: A file path (e.g., `_d/ai-native-manager.md`) or a freeform topic (e.g., "chaos of AI adoption")
- **`--count N`**: Number of directions to brainstorm and generate (default: 5, max: 8)
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

### Phase 3: Generate Images in Parallel

1. Locate the gen-image helper script:

   ```bash
   SKILL_DIR="$(dirname "$(find ~/gits/chop-conventions/skills/gen-image -name 'gemini-image.sh' -print -quit)")"
   ```

2. Locate the canonical raccoon reference image:

   ```bash
   REF_IMAGE="$(ls ~/gits/blog7/images/raccoon-nerd.webp 2>/dev/null || ls ~/gits/blog*/images/raccoon-nerd.webp 2>/dev/null | head -1)"
   ```

3. **SECURITY: Never expand secrets into commands.** When using `showboat exec`, the full
   command string is recorded in the document. Use `source ~/.env` _inside_ the exec command
   so showboat records the reference, not the secret value. NEVER do `export $(cat ~/.env | xargs)`
   before calling `showboat exec` — that expands the key into the captured command text.

4. Build full prompts for each direction. Each prompt combines:
   - The default raccoon style from gen-image (or `--style` override)
   - `IMPORTANT: Main raccoon LARGE and PROMINENT, at least 40% of image, shirt text clearly readable.`
   - The direction-specific scene description
   - Aspect ratio

5. Write each prompt to a temp file via `showboat exec` (so the prompt is readable in the doc):

   ```bash
   showboat exec "$DEMO" bash "cat > /tmp/prompt-NAME.txt << 'PROMPT'
   [SCENE DESCRIPTION with shirt text]
   PROMPT
   cat /tmp/prompt-NAME.txt"
   ```

6. **Run ALL generations in parallel** via `showboat exec` — issue all bash calls in a single message.
   This captures the generation command in the document, making it reproducible:

   ```bash
   showboat exec "$DEMO" bash "source ~/.env && ASPECT_RATIO=3:4 bash $SKILL_DIR/gemini-image.sh \
     \"[STYLE] IMPORTANT: Main raccoon LARGE and PROMINENT, at least 40% of image, shirt text clearly readable. \$(cat /tmp/prompt-NAME.txt)\" \
     NAME.webp '' $REF_IMAGE"
   ```

   Then convert and add the image:

   ```bash
   magick NAME.webp NAME.png
   showboat image "$DEMO" NAME.png
   ```

   Output files: `raccoon-explore-{direction-name}.webp` in the output directory.

7. After all complete, show each image to the user with the `Read` tool.

### Phase 4: Build Comparison Page

1. Create an output directory (e.g., `docs/image-explore-{topic}/`)

2. Convert all images to PNG for showboat:

   ```bash
   magick input.webp output.png
   ```

3. Build the showboat document:

   ```bash
   showboat init "$DEMO" "Title"
   # For each direction:
   showboat note "$DEMO" '## A. Direction Name
   **Concept:** ...
   **Vibe:** ...'
   showboat image "$DEMO" "direction.png"
   # No manual footer needed — the showboat pandoc template includes a
   # "Built with Showboat" footer automatically.
   ```

4. Convert to HTML with the showboat pandoc template:

   ```bash
   TEMPLATE="$(find ~/gits/chop-conventions/skills/showboat -name 'pandoc-template.html' -print -quit)"
   pandoc demo.md -o demo.html --standalone \
     --metadata title="Title" \
     --template "$TEMPLATE"
   ```

5. Serve locally for preview:

   ```bash
   nohup python3 -m http.server PORT --bind 0.0.0.0 > /dev/null 2>&1 &
   ```

   Use `running-servers suggest` or pick a free port. Provide the Tailscale URL.

### Phase 5: Publish (Ask First)

Ask the user: "Want to publish this as a shareable gist?"

If yes, use the helper script:

```bash
PUBLISH="$(find ~/gits/chop-conventions/skills/image-explore -name 'publish-gist.py' -print -quit)"
python3 "$PUBLISH" demo.html --title "Description"
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
