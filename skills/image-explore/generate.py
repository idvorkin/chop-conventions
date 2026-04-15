#!/usr/bin/env python3
# ABOUTME: Wrapper around gemini-image.sh for image generation (single or batch).
# ABOUTME: Handles env loading, prompt assembly, and ref image resolution safely.
# ABOUTME: In batch mode, augments the input JSON with _prompt and _duration_s debug fields.
#
# Single mode:
#   generate.py --scene "..." --shirt "TEXT" --output file.webp [options]
#
# Batch mode (parallel):
#   generate.py --batch directions.json [--aspect 3:4] [--ref path] [--style "..."]
#
# directions.json format:
#   [{"scene": "...", "shirt": "TEXT", "output": "file.webp"}, ...]

from __future__ import annotations

import argparse
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


def remove_background(image_path):
    """Strip the magenta chroma-key background using ImageMagick.

    Since images are generated on a KNOWN solid #FF00FF background, exact
    color-based transparency with ImageMagick's -fuzz is pixel-accurate,
    fast, and dependency-free. This beat rembg in testing: rembg left
    purple halos around character edges and required a heavy ML install.

    Fuzz of 30% handles edge antialiasing (where magenta blends into the
    subject outline) without bleeding into red/pink character colors.
    """
    magick = shutil.which("magick") or shutil.which("convert")
    if not magick:
        return False, "magick not found — install ImageMagick"

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [
                magick,
                image_path,
                "-fuzz",
                "30%",
                "-transparent",
                "#FF00FF",
                "-quality",
                "90",
                tmp_path,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False, f"magick -transparent failed: {result.stderr.strip()}"

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


def main():
    parser = argparse.ArgumentParser(
        description="Generate raccoon images via Gemini (single or batch)",
    )

    # Batch mode
    parser.add_argument(
        "--batch",
        metavar="JSON",
        default=None,
        help="JSON file with directions to generate in parallel",
    )

    # Single mode
    parser.add_argument("--scene", default=None, help="Scene description for the image")
    parser.add_argument(
        "--shirt", default=None, help="Text on the raccoon's shirt (max 8 chars)"
    )
    parser.add_argument(
        "--output", default=None, help="Output filename (e.g., mountain.webp)"
    )

    # Shared options
    parser.add_argument("--aspect", default="3:4", help="Aspect ratio (default: 3:4)")
    parser.add_argument("--ref", default=None, help="Override reference image path")
    parser.add_argument("--style", default=None, help="Override default raccoon style")
    parser.add_argument(
        "--transparent",
        action="store_true",
        default=False,
        help="Generate on magenta chroma-key background, then strip it via ImageMagick -fuzz -transparent",
    )

    args = parser.parse_args()

    # Validate: must be batch or single, not both/neither
    is_batch = args.batch is not None
    is_single = args.scene is not None
    if not is_batch and not is_single:
        parser.error("Provide --batch JSON or --scene/--shirt/--output for single mode")
    if is_batch and is_single:
        parser.error("Cannot use --batch with --scene/--shirt/--output")
    if is_single and (not args.shirt or not args.output):
        parser.error("Single mode requires --scene, --shirt, and --output")

    chop_root = resolve_chop_root()
    load_env()

    if not os.environ.get("GOOGLE_API_KEY"):
        print(
            "Error: GOOGLE_API_KEY not found in environment or ~/.env", file=sys.stderr
        )
        sys.exit(1)

    config = GenerateConfig(
        gemini_script=str(chop_root / "skills" / "gen-image" / "gemini-image.sh"),
        style=args.style or read_default_style(chop_root),
        ref_image=args.ref or resolve_ref_image(),
        aspect=args.aspect,
        transparent=args.transparent,
    )

    if is_single:
        direction = Direction(scene=args.scene, shirt=args.shirt, output=args.output)
        result = generate_one(direction, config)
        if not result.success:
            print(f"Error: {result.error}", file=sys.stderr)
            sys.exit(1)
        print(result.output)
        print(f"Generated in {result.duration_s}s", file=sys.stderr)
    else:
        # Batch mode: read directions JSON and generate in parallel
        batch_path = Path(args.batch)
        if not batch_path.exists():
            print(f"Error: Batch file not found: {batch_path}", file=sys.stderr)
            sys.exit(1)

        with open(batch_path) as f:
            raw_directions = json.load(f)

        if not raw_directions:
            print("Error: No directions in batch file", file=sys.stderr)
            sys.exit(1)

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
            sys.exit(1)
        print(
            f"\nAll {len(raw_directions)} images generated ({batch_duration}s total)",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
