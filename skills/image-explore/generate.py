#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "typer>=0.12",
# ]
# ///
# ABOUTME: Wrapper around gemini-image.sh for image generation (single or batch).
# ABOUTME: Handles env loading, prompt assembly, and ref image resolution safely.
# ABOUTME: In batch mode, augments the input JSON with _prompt and _duration_s debug fields.
#
# Single mode:
#   generate.py single --scene "..." --shirt "TEXT" --output file.webp [options]
#
# Batch mode (parallel):
#   generate.py batch directions.json [--aspect 3:4] [--ref path] [--style "..."]
#
# directions.json format:
#   [{"scene": "...", "shirt": "TEXT", "output": "file.webp"}, ...]

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

GREENSCREEN_PROMPT = (
    "IMPORTANT: Solid bright magenta chroma-key background (#FF00FF), "
    "uniform flat magenta everywhere behind the character."
)


@dataclass
class Direction:
    scene: str
    shirt: str
    output: str
    scene_first: bool = False


@dataclass
class GenerateConfig:
    gemini_script: str
    style: str
    ref_image: str | None
    aspect: str
    transparent: bool = False


@dataclass
class GenerationResult:
    output: str
    success: bool
    error: str | None
    prompt: str
    duration_s: float


def resolve_chop_root():
    """Resolve CHOP_ROOT from this script's location in the repo."""
    script_dir = Path(__file__).resolve().parent
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        cwd=script_dir,
    )
    if result.returncode != 0:
        print("Error: Could not resolve CHOP_ROOT via git", file=sys.stderr)
        sys.exit(1)
    return Path(result.stdout.strip())


def load_env(env_file="~/.env"):
    """Load KEY=VALUE pairs from env file into os.environ."""
    path = Path(env_file).expanduser()
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def resolve_ref_image():
    """Find the canonical raccoon reference image."""
    candidates = [
        Path("images/raccoon-nerd.webp"),
        Path.home() / "gits/idvorkin.github.io/images/raccoon-nerd.webp",
    ]
    for p in sorted(Path.home().glob("gits/blog*/images/raccoon-nerd.webp")):
        candidates.append(p)

    for c in candidates:
        if c.exists():
            return str(c)
    return None


def read_default_style(chop_root):
    """Read raccoon style from gen-image/raccoon-style.txt."""
    style_file = chop_root / "skills" / "gen-image" / "raccoon-style.txt"
    if style_file.exists():
        return style_file.read_text().strip()
    return (
        "Cute anthropomorphic raccoon character with chibi proportions "
        "(oversized head, small body), dark raccoon mask markings around eyes, "
        "big friendly dark eyes, small black nose, round brown ears with lighter "
        "inner ear, soft brown felt/plush fur, striped ringed tail with brown and "
        "dark brown bands. Wearing big round rainbow-colored glasses, green t-shirt "
        "with bold white text, blue denim shorts, IMPORTANT: mismatched Crocs shoes. "
        "Soft plush 3D/vinyl toy illustration style, studio softbox lighting, "
        "solid bright magenta chroma-key background (#FF00FF), subtle vintage film grain, children's book style. "
        "Full body."
    )


# Flood-seed scan: sample points along the image border and seed the
# flood-fill only from points that are actually near the magenta chroma-key
# color. This handles the case where Gemini frames the shot with the
# subject or its scenery extending to some of the image edges — the older
# "flood from all 4 corners" approach assumed every corner is chroma-bg,
# and when the bottom corners rendered as grass or stone, a 30%-fuzz flood
# started from grass and ate the subject.
#
# Constants below: sampling step along each edge (smaller = more seeds, more
# robust; larger = cheaper). Near-magenta tolerance is stricter than the
# flood fuzz on purpose — we want to seed only from "definitely background"
# pixels and let the flood itself handle the gradient at the subject edge.
BORDER_SAMPLE_STEP = 8
NEAR_MAGENTA_L1_TOLERANCE = 70  # sum of |r-255| + |g| + |b-255|
FLOOD_FUZZ_PERCENT = 30


def _parse_srgb(fragment):
    """Parse 'srgb(r,g,b)' or 'srgba(r,g,b,a)' — return (r,g,b) ints, or None."""
    fragment = fragment.strip()
    if not fragment.startswith(("srgb(", "srgba(")):
        return None
    try:
        nums = fragment.split("(", 1)[1].rstrip(")").split(",")[:3]
        return tuple(int(n) for n in nums)
    except (ValueError, IndexError):
        return None


def _scan_border_for_magenta_seeds(magick_bin, image_path, w, h):
    """Return list of (x,y) border pixels within NEAR_MAGENTA_L1_TOLERANCE of #FF00FF.

    Uses a single magick invocation: build a format string that dumps every
    sampled border pixel separated by '|'. Much cheaper than per-pixel
    subprocess calls on large borders.
    """
    positions = []
    step = BORDER_SAMPLE_STEP
    for x in range(0, w, step):
        positions.append((x, 0))
        positions.append((x, h - 1))
    for y in range(0, h, step):
        positions.append((0, y))
        positions.append((w - 1, y))

    fmt = "|".join(f"%[pixel:p{{{x},{y}}}]" for x, y in positions)
    result = subprocess.run(
        [magick_bin, image_path, "-format", fmt, "info:"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None, f"magick border probe failed: {result.stderr.strip()}"

    seeds = []
    fragments = result.stdout.split("|")
    if len(fragments) != len(positions):
        return None, (
            f"border probe returned {len(fragments)} fragments, expected {len(positions)}"
        )
    for (x, y), frag in zip(positions, fragments):
        rgb = _parse_srgb(frag)
        if rgb is None:
            continue
        r, g, b = rgb
        if abs(r - 255) + abs(g) + abs(b - 255) <= NEAR_MAGENTA_L1_TOLERANCE:
            seeds.append((x, y))
    return seeds, None


def remove_background(image_path):
    """Strip the magenta chroma-key background using border-seeded flood fill.

    Images are generated on a KNOWN solid #FF00FF background, so chroma-key
    stripping is pixel-accurate without ML. The older one-pass
    `-fuzz 30% -transparent #FF00FF` approach kills every magenta-ish pixel
    globally, including magenta-tinted highlights *inside* the character
    (pink fur, specular glass reflections, lobster-claw reds) — leaving
    swiss-cheese holes in the alpha.

    The flood-fill strategy only transparents pixels reachable from the
    image border, so interior magenta-tinted pixels are preserved by the
    character's own silhouette. Seeding the flood needs to find actual
    chroma-background pixels on the border — hard-coding the 4 corners
    fails whenever Gemini frames a shot with grass, sky, or scenery
    touching an image edge (seen in practice: dense grass rendered into
    the bottom corners, flood started from grass at 30% fuzz and ate the
    subject). Instead, sample the border ring, keep only samples that are
    actually near #FF00FF, and seed the flood from all of them.

    If no border pixels are near-magenta — the subject fills the whole
    frame — skip the strip rather than eat the subject, and let the caller
    decide what to do.
    """
    magick = shutil.which("magick") or shutil.which("convert")
    if not magick:
        return False, "magick not found — install ImageMagick"

    probe = subprocess.run(
        [magick, "identify", "-format", "%w %h", image_path],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return False, f"magick identify failed: {probe.stderr.strip()}"
    try:
        w, h = (int(x) for x in probe.stdout.split())
    except ValueError:
        return False, f"could not parse image dimensions: {probe.stdout!r}"

    seeds, err = _scan_border_for_magenta_seeds(magick, image_path, w, h)
    if err is not None:
        return False, err
    if not seeds:
        return False, (
            "no magenta chroma-key background detected on the image border; "
            "skipping strip. Regenerate with a prompt that leaves a magenta "
            "border on all four sides of the frame."
        )

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    draws = []
    for x, y in seeds:
        draws += ["-draw", f"color {x},{y} floodfill"]

    try:
        cmd = [
            magick,
            image_path,
            "-alpha",
            "set",
            "-fuzz",
            f"{FLOOD_FUZZ_PERCENT}%",
            "-fill",
            "none",
            *draws,
            "-quality",
            "90",
            tmp_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return False, f"magick flood-fill failed: {result.stderr.strip()}"

        ext = Path(image_path).suffix.lower()
        if ext == ".webp":
            conv = subprocess.run(
                [magick, tmp_path, "-quality", "90", image_path],
                capture_output=True,
                text=True,
            )
            if conv.returncode != 0:
                return False, f"magick webp convert failed: {conv.stderr.strip()}"
        else:
            shutil.copy2(tmp_path, image_path)
        return True, None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def generate_one(direction: Direction, config: GenerateConfig) -> GenerationResult:
    """Generate a single image.

    Prompt order depends on direction.scene_first:
    - False (default): style → "large & prominent" → scene  (character-focused)
    - True:            scene → style → shirt text only       (scene-focused)

    Scene-first mode drops the "40% of image" instruction so wide-field
    compositions aren't overridden by the character anchoring.
    """
    if direction.scene_first:
        prompt_parts = [
            direction.scene,
            config.style,
            f'Shirt text clearly readable. Shirt reads: "{direction.shirt}".',
        ]
    else:
        prompt_parts = [
            config.style,
            f"IMPORTANT: Main raccoon LARGE and PROMINENT, at least 40% of image, "
            f'shirt text clearly readable. Shirt reads: "{direction.shirt}".',
            direction.scene,
        ]
    if config.transparent:
        prompt_parts.append(GREENSCREEN_PROMPT)

    full_prompt = " ".join(prompt_parts)

    cmd = ["bash", config.gemini_script, full_prompt, direction.output, ""]
    if config.ref_image:
        cmd.append(config.ref_image)

    env = os.environ.copy()
    env["ASPECT_RATIO"] = config.aspect

    print(f"Generating: {direction.output}", file=sys.stderr)
    t0 = time.monotonic()
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    duration_s = round(time.monotonic() - t0, 1)

    if result.returncode != 0:
        return GenerationResult(
            output=direction.output,
            success=False,
            error=result.stderr.strip(),
            prompt=full_prompt,
            duration_s=duration_s,
        )
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    if config.transparent:
        print(f"Removing background: {direction.output}", file=sys.stderr)
        success, err = remove_background(direction.output)
        if not success:
            return GenerationResult(
                output=direction.output,
                success=False,
                error=f"Background removal failed: {err}",
                prompt=full_prompt,
                duration_s=duration_s,
            )

    return GenerationResult(
        output=direction.output,
        success=True,
        error=None,
        prompt=full_prompt,
        duration_s=duration_s,
    )


def _build_app():
    """Wire Typer app. Called only from __main__ so tests skip the typer import."""
    import typer

    app = typer.Typer(
        help="Generate raccoon images via Gemini (single or batch).",
        add_completion=False,
        no_args_is_help=True,
    )

    @app.command()
    def single(
        scene: str = typer.Option(..., help="Scene description for the image"),
        shirt: str = typer.Option(
            ..., help="Text on the raccoon's shirt (max 8 chars)"
        ),
        output: str = typer.Option(..., help="Output filename (e.g., mountain.webp)"),
        aspect: str = typer.Option("3:4", help="Aspect ratio (default: 3:4)"),
        ref: str | None = typer.Option(None, help="Override reference image path"),
        style: str | None = typer.Option(None, help="Override default raccoon style"),
        transparent: bool = typer.Option(
            False,
            help="Generate on magenta chroma-key background, then strip it via ImageMagick edge-connected flood fill from the 4 corners (preserves interior magenta-tinted pixels)",
        ),
    ) -> None:
        """Generate a single raccoon image."""
        chop_root = resolve_chop_root()
        load_env()

        if not os.environ.get("GOOGLE_API_KEY"):
            print(
                "Error: GOOGLE_API_KEY not found in environment or ~/.env",
                file=sys.stderr,
            )
            raise typer.Exit(1)

        config = GenerateConfig(
            gemini_script=str(chop_root / "skills" / "gen-image" / "gemini-image.sh"),
            style=style or read_default_style(chop_root),
            ref_image=ref or resolve_ref_image(),
            aspect=aspect,
            transparent=transparent,
        )

        direction = Direction(scene=scene, shirt=shirt, output=output)
        result = generate_one(direction, config)
        if not result.success:
            print(f"Error: {result.error}", file=sys.stderr)
            raise typer.Exit(1)
        print(result.output)
        print(f"Generated in {result.duration_s}s", file=sys.stderr)

    @app.command()
    def batch(
        json_file: str = typer.Argument(
            help="JSON file with directions to generate in parallel"
        ),
        aspect: str = typer.Option("3:4", help="Aspect ratio (default: 3:4)"),
        ref: str | None = typer.Option(None, help="Override reference image path"),
        style: str | None = typer.Option(None, help="Override default raccoon style"),
        transparent: bool = typer.Option(
            False,
            help="Generate on magenta chroma-key background, then strip it via ImageMagick edge-connected flood fill from the 4 corners (preserves interior magenta-tinted pixels)",
        ),
    ) -> None:
        """Generate images in parallel from a JSON manifest."""
        chop_root = resolve_chop_root()
        load_env()

        if not os.environ.get("GOOGLE_API_KEY"):
            print(
                "Error: GOOGLE_API_KEY not found in environment or ~/.env",
                file=sys.stderr,
            )
            raise typer.Exit(1)

        config = GenerateConfig(
            gemini_script=str(chop_root / "skills" / "gen-image" / "gemini-image.sh"),
            style=style or read_default_style(chop_root),
            ref_image=ref or resolve_ref_image(),
            aspect=aspect,
            transparent=transparent,
        )

        batch_path = Path(json_file)
        if not batch_path.exists():
            print(f"Error: Batch file not found: {batch_path}", file=sys.stderr)
            raise typer.Exit(1)

        with open(batch_path) as f:
            raw_directions = json.load(f)

        if not raw_directions:
            print("Error: No directions in batch file", file=sys.stderr)
            raise typer.Exit(1)

        print(
            f"Generating {len(raw_directions)} images in parallel...", file=sys.stderr
        )
        failures = []

        # Map output filename -> raw dict for augmenting with debug info
        dir_by_output = {d["output"]: d for d in raw_directions}

        batch_t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=len(raw_directions)) as pool:
            futures = {
                pool.submit(
                    generate_one,
                    Direction(
                        scene=d["scene"],
                        shirt=d["shirt"],
                        output=d["output"],
                        scene_first=d.get("scene_first", False),
                    ),
                    config,
                ): d
                for d in raw_directions
            }
            for future in as_completed(futures):
                result = future.result()
                # Augment the raw dict with debug info
                if result.output in dir_by_output:
                    dir_by_output[result.output]["_prompt"] = result.prompt
                    dir_by_output[result.output]["_duration_s"] = result.duration_s
                if result.success:
                    print(result.output)
                else:
                    failures.append((result.output, result.error))
                    print(f"FAILED: {result.output} — {result.error}", file=sys.stderr)

        batch_duration = round(time.monotonic() - batch_t0, 1)

        # Write augmented directions back with debug info
        with open(batch_path, "w") as f:
            json.dump(raw_directions, f, indent=2)

        if failures:
            print(
                f"\n{len(failures)}/{len(raw_directions)} failed ({batch_duration}s total)",
                file=sys.stderr,
            )
            raise typer.Exit(1)
        print(
            f"\nAll {len(raw_directions)} images generated ({batch_duration}s total)",
            file=sys.stderr,
        )

    return app


if __name__ == "__main__":
    _build_app()()
