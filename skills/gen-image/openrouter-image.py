#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
# ABOUTME: One image per call through OpenRouter's Image API (POST /api/v1/images).
# ABOUTME: Default model is Muse Image; GPT Image is the opt-in for the jobs Muse can't do.
#
# Usage:
#   openrouter-image.py <prompt | @prompt.txt> <output.{png,webp}> [--model muse|gpt|<id>]
#                       [--ref PATH]... [--aspect 1:1] [--resolution 2K] [--retries 1]
#
# References ride as `input_references` in the order given. The model cannot
# tell them apart on its own, so the prompt must name each one by position
# ("Reference 1 is the canonical raccoon…"). Put the canon raccoon first.
#
# Key: OPEN_ROUTER_KEY (or OPENROUTER_API_KEY) from the environment, then
# ~/.env, then the JSON file named by $SECRET_BOX.
#
# Prints the model id and billed cost to stderr on every call, so a run log
# proves which model drew the picture.

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://openrouter.ai/api/v1/images"
MODELS = {
    "muse": "meta/muse-image",
    "gpt": "openai/gpt-image-2.5-sunburst",
}
MIME = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def resolve_model(name: str) -> str:
    return MODELS.get(name, name)


def gpt_size(aspect: str) -> str:
    """GPT Image ignores `resolution`; it wants an explicit size. Long side 2048.

    Only 2048x2048 (1:1) has been verified; other ratios are derived.
    """
    w, h = (int(x) for x in aspect.split(":"))
    if w >= h:
        return f"2048x{round(2048 * h / w / 16) * 16}"
    return f"{round(2048 * w / h / 16) * 16}x2048"


def build_body(
    model: str, prompt: str, refs: list[Path], aspect: str, resolution: str
) -> dict:
    body = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "aspect_ratio": aspect,
        "output_format": "png",
        "stream": False,
    }
    if model.startswith("openai/gpt-image"):
        body["size"] = gpt_size(aspect)
    else:
        body["resolution"] = resolution
    if refs:
        body["input_references"] = [
            {"type": "image_url", "image_url": {"url": data_url(r)}} for r in refs
        ]
    return body


def data_url(p: Path) -> str:
    mt = MIME.get(p.suffix.lower())
    if not mt:
        raise SystemExit(f"Error: unsupported reference type {p.suffix} ({p})")
    return f"data:{mt};base64,{base64.b64encode(p.read_bytes()).decode()}"


def load_key() -> str:
    for name in ("OPEN_ROUTER_KEY", "OPENROUTER_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    env = Path("~/.env").expanduser()
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip().removeprefix("export ").strip()
            key, _, val = line.partition("=")
            if key in ("OPEN_ROUTER_KEY", "OPENROUTER_API_KEY") and val:
                return val.strip().strip("'\"")
    box = os.environ.get("SECRET_BOX")
    if box and Path(box).expanduser().exists():
        data = json.loads(Path(box).expanduser().read_text())
        for name in ("OPEN_ROUTER_KEY", "OPENROUTER_API_KEY"):
            if data.get(name):
                return data[name]
    raise SystemExit("Error: OPEN_ROUTER_KEY not found in env, ~/.env or $SECRET_BOX")


def post(body: dict, key: str) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:600]


def write_output(png_bytes: bytes, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".png":
        out.write_bytes(png_bytes)
        return
    tmp = out.with_suffix(".tmp.png")
    tmp.write_bytes(png_bytes)
    if not shutil.which("magick"):
        tmp.rename(out.with_suffix(".png"))
        print(
            f"warn: magick not found, wrote {out.with_suffix('.png')}", file=sys.stderr
        )
        return
    subprocess.run(["magick", str(tmp), "-quality", "90", str(out)], check=True)
    tmp.unlink()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="One image per call via OpenRouter (default Muse Image)."
    )
    ap.add_argument("prompt", help="prompt text, or @file to read it from a file")
    ap.add_argument("output")
    ap.add_argument(
        "--model",
        default="muse",
        help="muse (default), gpt, or a full OpenRouter model id",
    )
    ap.add_argument(
        "--ref",
        action="append",
        default=[],
        help="reference image, repeatable, in prompt order",
    )
    ap.add_argument("--aspect", default="1:1")
    ap.add_argument(
        "--resolution",
        default="2K",
        help="Muse only; GPT gets an explicit size instead",
    )
    ap.add_argument(
        "--retries",
        type=int,
        default=1,
        help="identical retries after a refusal or error (refusals are free)",
    )
    a = ap.parse_args(argv)

    prompt = Path(a.prompt[1:]).read_text() if a.prompt.startswith("@") else a.prompt
    model = resolve_model(a.model)
    refs = [Path(r).expanduser() for r in a.ref]
    for r in refs:
        if not r.exists():
            raise SystemExit(f"Error: reference not found: {r}")
    body = build_body(model, prompt, refs, a.aspect, a.resolution)
    key = load_key()

    print(f"model={model} refs={len(refs)} out={a.output}", file=sys.stderr)
    t0 = time.monotonic()
    for attempt in range(a.retries + 1):
        status, resp = post(body, key)
        if status == 200 and isinstance(resp, dict) and resp.get("data"):
            break
        print(
            f"attempt {attempt + 1}: HTTP {status}: {str(resp)[:300]}", file=sys.stderr
        )
    else:
        return 1
    write_output(base64.b64decode(resp["data"][0]["b64_json"]), Path(a.output))
    cost = (resp.get("usage") or {}).get("cost")
    print(
        json.dumps(
            {
                "model": model,
                "out": a.output,
                "cost": cost,
                "secs": round(time.monotonic() - t0),
            }
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
