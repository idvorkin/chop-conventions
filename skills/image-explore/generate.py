#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "typer>=0.12",
#     "numpy",
#     "pillow",
#     "scipy",
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
#
# Background removal under --transparent always goes through Recraft's
# removeBackground API (skills/gen-image/recraft_bg_remove.py). Numpy/
# Pillow/Scipy power the post-strip eval_alpha() pathway (interior holes
# / edge fringe) and are lazy-imported there. The `uv run --script`
# shebang auto-installs them; running via plain `python3` works as long
# as --transparent is off or --no-eval is set.

import json
import os
import subprocess
import sys
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
    # Layered alpha-mask eval on top of evaluate_strip's alpha-mean signal.
    # Detects interior holes and edge fringe in the Recraft output.
    eval_alpha: bool = True
    eval_strict: bool = False
    # Path to the Recraft script (skills/gen-image/recraft_bg_remove.py).
    recraft_script: str | None = None


@dataclass
class GenerationResult:
    output: str
    success: bool
    error: str | None
    prompt: str
    duration_s: float
    eval_metrics: dict | None = None
    eval_warnings: list | None = None


# Alpha-mask eval thresholds for eval_alpha(). Tuned conservatively on
# typical output so clean images don't false-alarm. Exceeding any one
# triggers a [WARN] on the eval line (and, under --eval-strict, a
# nonzero exit). These complement evaluate_strip's alpha-mean signal —
# the mean catches "strip ate the subject" / "nothing stripped"; these
# catch "Swiss-cheese holes" and "halo".
EVAL_ALPHA_THRESHOLDS = {
    "interior_hole_px": 500,
    "edge_fringe_px": 2000,
}

# Before counting interior holes, morphologically close the opaque mask
# (dilate then erode by this many pixels). Recraft's mask is generally
# clean, but if a thin connector ever pierces the silhouette, sealing
# 1-2-pixel channels keeps the interior-hole metric accurate. Radius 1
# matches the original tuning for the calibration set.
INTERIOR_HOLE_CLOSE_RADIUS = 1
# After sealing channels, drop interior components smaller than this —
# antialiasing specks aren't real damage, and they dominate the
# component count on otherwise-clean images.
INTERIOR_HOLE_MIN_COMPONENT_PX = 100


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


# Post-strip eval thresholds — the same metrics the unit tests assert on,
# reused as a runtime regression guard. See /hill-climbing for why the
# same eval that drives the search becomes infrastructure after it.
# Alpha mean (0..100): percentage of pixels fully opaque. Below
# HEALTHY_ALPHA_MIN_PCT means the strip ate the subject; above
# HEALTHY_ALPHA_MAX_PCT means nearly nothing was transparent (subject
# likely fills the frame, strip effectively a no-op).
HEALTHY_ALPHA_MIN_PCT = 15.0
HEALTHY_ALPHA_MAX_PCT = 85.0


def remove_background_recraft(image_path, recraft_script):
    """Strip the background via Recraft's removeBackground API.

    Returns (success: bool, error: str | None). The same evaluate_strip +
    eval_alpha guards run on the result downstream — if Recraft regresses,
    the alpha-mean / interior-hole signals catch it.

    Implementation: shells out to skills/gen-image/recraft_bg_remove.py
    (Typer + uv-shebang, stdlib-only HTTP layer). The script self-bootstraps
    its typer dep via uv. We invoke its `strip` subcommand by absolute path
    so the shebang fires.

    Cost / latency: ~$0.01/call, ~7-40s wall. Requires RECRAFT_API_TOKEN
    in env or ~/.env.
    """
    if not recraft_script or not Path(recraft_script).exists():
        return False, (
            f"recraft script not found at {recraft_script!r}; "
            "expected skills/gen-image/recraft_bg_remove.py"
        )

    result = subprocess.run(
        [recraft_script, "strip", image_path, image_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, (
            f"recraft_bg_remove.py failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return True, None


def evaluate_strip(image_path):
    """Compute bg-strip quality metrics for a finished image.

    Returns (metrics_dict, warning_str_or_None). The metrics dict always
    has the same keys so the caller can log them uniformly; warning is
    set when the metrics indicate the strip likely failed (subject eaten
    or nothing stripped). The same thresholds are asserted on in
    test_generate.py's integration suite, so test + runtime share the
    same definition of "healthy."

    General-purpose alpha-mean signal: works on any RGBA output,
    including Recraft's PNG-with-alpha. Independent of the upstream
    bg-removal mechanism.

    Keys in the metrics dict:
    - alpha_mean_pct: percentage of pixels fully opaque (0..100)
    - file_size_kb: size on disk, rounded
    - status: one of "healthy", "subject_eaten", "nothing_stripped",
      "eval_failed"
    """
    import shutil  # noqa: PLC0415

    magick = shutil.which("magick") or shutil.which("convert")
    if magick is None:
        return (
            {"alpha_mean_pct": None, "file_size_kb": None, "status": "eval_failed"},
            "magick not found — skipping post-strip eval",
        )

    try:
        path_obj = Path(image_path)
        file_size_kb = (
            round(path_obj.stat().st_size / 1024, 1) if path_obj.exists() else None
        )
    except OSError:
        file_size_kb = None

    result = subprocess.run(
        [magick, image_path, "-format", "%[fx:mean.a*100]", "info:"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return (
            {
                "alpha_mean_pct": None,
                "file_size_kb": file_size_kb,
                "status": "eval_failed",
            },
            f"magick alpha probe failed: {result.stderr.strip()}",
        )

    try:
        alpha_pct = round(float(result.stdout.strip()), 2)
    except ValueError:
        return (
            {
                "alpha_mean_pct": None,
                "file_size_kb": file_size_kb,
                "status": "eval_failed",
            },
            f"could not parse alpha mean: {result.stdout.strip()!r}",
        )

    if alpha_pct < HEALTHY_ALPHA_MIN_PCT:
        status = "subject_eaten"
        warning = (
            f"alpha_mean={alpha_pct}% is below {HEALTHY_ALPHA_MIN_PCT}%: "
            "the strip likely ate the subject. Recraft may have misidentified "
            "the subject — regenerate, or inspect the source image."
        )
    elif alpha_pct > HEALTHY_ALPHA_MAX_PCT:
        status = "nothing_stripped"
        warning = (
            f"alpha_mean={alpha_pct}% is above {HEALTHY_ALPHA_MAX_PCT}%: "
            "almost nothing was transparented. Subject likely fills the "
            "frame; the character reference image may need a wider crop."
        )
    else:
        status = "healthy"
        warning = None

    return (
        {
            "alpha_mean_pct": alpha_pct,
            "file_size_kb": file_size_kb,
            "status": status,
        },
        warning,
    )


def _format_eval_card(image_path, metrics, warning):
    """Render a compact one-line eval summary for stderr logging."""
    alpha = metrics.get("alpha_mean_pct")
    size = metrics.get("file_size_kb")
    status = metrics.get("status", "unknown")
    alpha_s = f"{alpha}%" if alpha is not None else "?"
    size_s = f"{size}KB" if size is not None else "?"
    line = f"eval [{status}] {Path(image_path).name}: alpha={alpha_s} size={size_s}"
    if warning:
        line += f"\n  WARN: {warning}"
    return line


def _label_interior(transparent_mask, label):
    """Return a boolean mask of transparent pixels NOT reachable from
    the image border. `label` is scipy.ndimage.label injected by the
    caller so this helper doesn't need its own import."""
    import numpy as np  # noqa: PLC0415

    labeled, _ = label(transparent_mask)
    border_labels = set()
    border_labels.update(labeled[0, :].tolist())
    border_labels.update(labeled[-1, :].tolist())
    border_labels.update(labeled[:, 0].tolist())
    border_labels.update(labeled[:, -1].tolist())
    border_labels.discard(0)
    border_mask = np.isin(labeled, list(border_labels))
    return transparent_mask & ~border_mask


def eval_alpha(
    image_path,
    close_radius=INTERIOR_HOLE_CLOSE_RADIUS,
    min_component_px=INTERIOR_HOLE_MIN_COMPONENT_PX,
):
    """Compute alpha-mask quality metrics for a post-strip RGBA image.

    Detects failure modes orthogonal to evaluate_strip's alpha-mean signal.
    General-purpose: works on any RGBA output, regardless of how the
    background was removed.

      * interior_hole_px — transparent pixels NOT reachable from the image
        border after the opaque mask is morphologically closed. Closing
        seals 1-2-pixel bleed channels that connect a real interior hole
        to the outside background, which would otherwise let it slip
        through a naive border-touching check. Tiny components below
        `min_component_px` are filtered as antialiasing noise.
      * interior_hole_largest_px — pixels in the single largest interior
        hole. More stable across images than the total; good for
        thresholding because one big hole is what a human sees.
      * edge_fringe_px — partial-alpha pixels. Large counts suggest halo —
        a known Recraft tradeoff on hair/fur edges.

    Deps (numpy, pillow, scipy) are lazy-imported so callers that pass
    --no-eval (or don't touch --transparent) never hit an ImportError on
    a stock python3.
    """
    import numpy as np  # noqa: PLC0415 — lazy import, see module header
    from PIL import Image  # noqa: PLC0415
    from scipy.ndimage import binary_closing, label  # noqa: PLC0415

    arr = np.asarray(Image.open(image_path).convert("RGBA"))
    a = arr[:, :, 3]
    opaque = a > 128
    transparent = a < 16

    # Interior holes: we want to flag bleed-through damage but NOT
    # legitimate design-intentional gaps (armpit openings, space between
    # legs, gap between fingers). Both show up as "transparent surrounded
    # by opaque" but they behave differently under morphological closing
    # of the opaque mask:
    #   - Design gap: several pixels wide at its opening; closing by 1 px
    #     barely narrows it, so its topological relationship to the border
    #     is unchanged.
    #   - Bleed channel: 1–2 px wide where it pierces the character;
    #     closing by 1 px seals the channel shut, so what was border-
    #     connected becomes enclosed and "appears" in the interior set.
    # The set difference (closed-interior minus open-interior) isolates
    # exactly the pixels that only became enclosed because of the
    # channel-sealing. That's the bleed-through signal we actually want.
    interior_open = _label_interior(~opaque, label)
    if close_radius > 0:
        closed_opaque = binary_closing(opaque, iterations=close_radius)
        interior_closed = _label_interior(~closed_opaque, label)
        channel_revealed = interior_closed & ~interior_open
    else:
        channel_revealed = interior_open

    if min_component_px > 1 and channel_revealed.any():
        relabeled, _ = label(channel_revealed)
        sizes = np.bincount(relabeled.ravel())
        small_labels = np.where(sizes < min_component_px)[0]
        small_labels = small_labels[small_labels != 0]
        if small_labels.size:
            channel_revealed = channel_revealed & ~np.isin(relabeled, small_labels)

    interior = int(channel_revealed.sum())
    if channel_revealed.any():
        relabeled, _ = label(channel_revealed)
        sizes = np.bincount(relabeled.ravel())
        sizes[0] = 0
        interior_largest = int(sizes.max())
    else:
        interior_largest = 0

    # Edge fringe: partial alpha. Some is expected (antialiasing); a lot
    # suggests a halo around the subject.
    edge = int(((~opaque) & (~transparent)).sum())

    return {
        "interior_hole_px": interior,
        "interior_hole_largest_px": interior_largest,
        "edge_fringe_px": edge,
    }


def format_eval_line(image_path, metrics, warnings):
    """Render the eval_alpha line printed to stderr."""
    status = f"[WARN: {'; '.join(warnings)}]" if warnings else "[OK]"
    largest = metrics.get("interior_hole_largest_px", 0)
    return (
        f"[eval] {image_path}: "
        f"holes={metrics['interior_hole_px']} (largest={largest}), "
        f"fringe={metrics['edge_fringe_px']}   {status}"
    )


def check_eval_thresholds(metrics, thresholds=EVAL_ALPHA_THRESHOLDS):
    """Return a list of human-readable warnings for tripped thresholds."""
    warnings = []
    if metrics["interior_hole_px"] > thresholds["interior_hole_px"]:
        warnings.append(
            f"interior damage likely (holes={metrics['interior_hole_px']} "
            f"> {thresholds['interior_hole_px']}) — check alpha mask"
        )
    if metrics["edge_fringe_px"] > thresholds["edge_fringe_px"]:
        warnings.append(
            f"edge fringe (fringe={metrics['edge_fringe_px']} "
            f"> {thresholds['edge_fringe_px']}) — possible halo"
        )
    return warnings


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

    eval_alpha_metrics = None
    eval_alpha_warnings = None
    if config.transparent:
        # Recraft is the only bg-removal path. ~7-40s of API latency per
        # image, ~$0.01/call. The eval pass below runs on the result —
        # alpha-mean catches "subject eaten" / "nothing stripped";
        # eval_alpha catches interior holes and edge fringe.
        print(
            f"Removing background (recraft): {direction.output}",
            file=sys.stderr,
        )
        success, err = remove_background_recraft(
            direction.output, config.recraft_script
        )
        if not success:
            return GenerationResult(
                output=direction.output,
                success=False,
                error=f"Background removal failed: {err}",
                prompt=full_prompt,
                duration_s=duration_s,
            )
        # Auto-eval the strip: the same metrics asserted in test_generate.py
        # become the runtime regression guard. Healthy strips print a quiet
        # one-liner; failed strips print a loud warning with actionable
        # guidance.
        metrics, warning = evaluate_strip(direction.output)
        print(
            _format_eval_card(direction.output, metrics, warning),
            file=sys.stderr,
        )

        # Layered alpha-mask eval — catches interior holes and halo
        # fringe that the alpha-mean signal above can't detect.
        # Best-effort: missing deps or errors log and continue rather
        # than failing the generation.
        if config.eval_alpha:
            try:
                eval_alpha_metrics = eval_alpha(direction.output)
                eval_alpha_warnings = check_eval_thresholds(eval_alpha_metrics)
                print(
                    format_eval_line(
                        direction.output, eval_alpha_metrics, eval_alpha_warnings
                    ),
                    file=sys.stderr,
                )
            except ImportError as e:
                print(
                    f"[eval] {direction.output}: skipped (missing deps: {e}); "
                    f"run via 'uv run --script' or pass --no-eval",
                    file=sys.stderr,
                )
            except Exception as e:  # noqa: BLE001 — eval is best-effort
                print(
                    f"[eval] {direction.output}: skipped (error: {e})",
                    file=sys.stderr,
                )

    return GenerationResult(
        output=direction.output,
        success=True,
        error=None,
        prompt=full_prompt,
        duration_s=duration_s,
        eval_metrics=eval_alpha_metrics,
        eval_warnings=eval_alpha_warnings,
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
            help="Generate on a uniform magenta background, then strip it via Recraft's removeBackground API (~$0.01/call, ~7-40s/image, requires RECRAFT_API_TOKEN). Soft-mask edges on hair/fur, no flood-fill failure modes.",
        ),
        no_eval: bool = typer.Option(
            False,
            "--no-eval",
            help="Skip the post-strip alpha-mask eval (interior holes, edge fringe). Needs numpy/pillow/scipy — the uv shebang installs them, but bare python3 callers may need this.",
        ),
        eval_strict: bool = typer.Option(
            False,
            "--eval-strict",
            help="Exit 2 if any eval threshold trips (interior holes, edge fringe). Useful for CI / calling agents that want to retry or fail loudly.",
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
            eval_alpha=not no_eval,
            eval_strict=eval_strict,
            recraft_script=str(
                chop_root / "skills" / "gen-image" / "recraft_bg_remove.py"
            ),
        )

        direction = Direction(scene=scene, shirt=shirt, output=output)
        result = generate_one(direction, config)
        if not result.success:
            print(f"Error: {result.error}", file=sys.stderr)
            raise typer.Exit(1)
        print(result.output)
        print(f"Generated in {result.duration_s}s", file=sys.stderr)
        if config.eval_strict and result.eval_warnings:
            print(
                f"Error: --eval-strict tripped on {result.output}",
                file=sys.stderr,
            )
            raise typer.Exit(2)

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
            help="Generate on a uniform magenta background, then strip it via Recraft's removeBackground API (~$0.01/call, ~7-40s/image, requires RECRAFT_API_TOKEN). Soft-mask edges on hair/fur, no flood-fill failure modes.",
        ),
        no_eval: bool = typer.Option(
            False,
            "--no-eval",
            help="Skip the post-strip alpha-mask eval (interior holes, edge fringe). Needs numpy/pillow/scipy — the uv shebang installs them, but bare python3 callers may need this.",
        ),
        eval_strict: bool = typer.Option(
            False,
            "--eval-strict",
            help="Exit 2 if any eval threshold trips on any image. Useful for CI / calling agents that want to retry or fail loudly.",
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
            eval_alpha=not no_eval,
            eval_strict=eval_strict,
            recraft_script=str(
                chop_root / "skills" / "gen-image" / "recraft_bg_remove.py"
            ),
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
        eval_tripped = []

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
                    if result.eval_metrics is not None:
                        dir_by_output[result.output]["_eval"] = result.eval_metrics
                if result.success:
                    print(result.output)
                    if result.eval_warnings:
                        eval_tripped.append(result.output)
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
        if config.eval_strict and eval_tripped:
            print(
                f"\nError: --eval-strict tripped on {len(eval_tripped)} image(s): "
                f"{', '.join(eval_tripped)}",
                file=sys.stderr,
            )
            raise typer.Exit(2)

    return app


if __name__ == "__main__":
    _build_app()()
