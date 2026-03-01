#!/usr/bin/env python3
# ABOUTME: Wrapper around gemini-image.sh for image generation (single or batch).
# ABOUTME: Handles env loading, prompt assembly, and ref image resolution safely.
#
# Single mode:
#   generate.py --scene "..." --shirt "TEXT" --output file.webp [options]
#
# Batch mode (parallel):
#   generate.py --batch directions.json [--aspect 3:4] [--ref path] [--style "..."]
#
# directions.json format:
#   [{"scene": "...", "shirt": "TEXT", "output": "file.webp"}, ...]

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

GREENSCREEN_PROMPT = (
    "IMPORTANT: Solid bright magenta chroma-key background (#FF00FF), "
    "uniform flat magenta everywhere behind the character."
)


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
    """Remove background using rembg via uvx. Returns (success, error_msg)."""
    uvx = shutil.which("uvx")
    if not uvx:
        return False, "uvx not found — needed to run rembg"

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [uvx, "--from", "rembg[cpu,cli]", "rembg", "i", image_path, tmp_path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False, f"rembg failed: {result.stderr.strip()}"

        ext = Path(image_path).suffix.lower()
        if ext == ".webp":
            magick = shutil.which("magick") or shutil.which("convert")
            if magick:
                conv = subprocess.run(
                    [magick, tmp_path, "-quality", "90", image_path],
                    capture_output=True,
                    text=True,
                )
                if conv.returncode != 0:
                    return False, f"magick convert failed: {conv.stderr.strip()}"
            else:
                shutil.copy2(tmp_path, image_path.replace(".webp", ".png"))
        else:
            shutil.copy2(tmp_path, image_path)
        return True, None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def generate_one(
    scene, shirt, output, gemini_script, style, ref_image, aspect, transparent=False
):
    """Generate a single image. Returns (output, success, error_msg)."""
    prompt_parts = [
        style,
        f"IMPORTANT: Main raccoon LARGE and PROMINENT, at least 40% of image, "
        f'shirt text clearly readable. Shirt reads: "{shirt}".',
        scene,
    ]
    if transparent:
        prompt_parts.append(GREENSCREEN_PROMPT)

    full_prompt = " ".join(prompt_parts)

    cmd = ["bash", gemini_script, full_prompt, output, ""]
    if ref_image:
        cmd.append(ref_image)

    env = os.environ.copy()
    env["ASPECT_RATIO"] = aspect

    print(f"Generating: {output}", file=sys.stderr)
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)

    if result.returncode != 0:
        return (output, False, result.stderr.strip())
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    if transparent:
        print(f"Removing background: {output}", file=sys.stderr)
        success, err = remove_background(output)
        if not success:
            return (output, False, f"Background removal failed: {err}")

    return (output, True, None)


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
        help="Generate on magenta greenscreen, then remove background with rembg",
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

    style = args.style or read_default_style(chop_root)
    gemini_script = str(chop_root / "skills" / "gen-image" / "gemini-image.sh")
    ref_image = args.ref or resolve_ref_image()

    if is_single:
        _, success, err = generate_one(
            args.scene,
            args.shirt,
            args.output,
            gemini_script,
            style,
            ref_image,
            args.aspect,
            transparent=args.transparent,
        )
        if not success:
            print(f"Error: {err}", file=sys.stderr)
            sys.exit(1)
        print(args.output)
    else:
        # Batch mode: read directions JSON and generate in parallel
        batch_path = Path(args.batch)
        if not batch_path.exists():
            print(f"Error: Batch file not found: {batch_path}", file=sys.stderr)
            sys.exit(1)

        with open(batch_path) as f:
            directions = json.load(f)

        if not directions:
            print("Error: No directions in batch file", file=sys.stderr)
            sys.exit(1)

        print(f"Generating {len(directions)} images in parallel...", file=sys.stderr)
        failures = []

        with ThreadPoolExecutor(max_workers=len(directions)) as pool:
            futures = {
                pool.submit(
                    generate_one,
                    d["scene"],
                    d["shirt"],
                    d["output"],
                    gemini_script,
                    style,
                    ref_image,
                    args.aspect,
                    transparent=args.transparent,
                ): d
                for d in directions
            }
            for future in as_completed(futures):
                output, success, err = future.result()
                if success:
                    print(output)
                else:
                    failures.append((output, err))
                    print(f"FAILED: {output} — {err}", file=sys.stderr)

        if failures:
            print(f"\n{len(failures)}/{len(directions)} failed", file=sys.stderr)
            sys.exit(1)
        print(f"\nAll {len(directions)} images generated", file=sys.stderr)


if __name__ == "__main__":
    main()
