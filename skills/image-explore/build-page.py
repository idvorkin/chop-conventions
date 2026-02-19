#!/usr/bin/env python3
# ABOUTME: Builds a showboat comparison page from generated images and direction metadata.
# ABOUTME: Creates showboat doc, converts to HTML via pandoc, and serves locally.
#
# Usage: build-page.py --title "Title" --dir output-dir/ directions.json
#
# directions.json format:
# [
#   {"name": "Mountain Vista", "section": "Purpose", "vibe": "Quiet awe",
#    "shirt": "NORTH", "image": "mountain.webp"}
# ]

import argparse
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path


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


def run(cmd, **kwargs):
    """Run a shell command, exit on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        print(f"Error running: {' '.join(str(c) for c in cmd)}", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def find_free_port(start=4005):
    """Find a free port starting from the given port."""
    for port in range(start, start + 100):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port))
                return port
        except OSError:
            continue
    return start + 100


def get_tailscale_hostname():
    """Get the Tailscale hostname for URL construction."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            import json as _json

            status = _json.loads(result.stdout)
            dns_name = status.get("Self", {}).get("DNSName", "")
            if dns_name:
                return dns_name.rstrip(".")
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # Fallback: use hostname
    return socket.gethostname().lower() + ".squeaker-teeth.ts.net"


def main():
    parser = argparse.ArgumentParser(
        description="Build a showboat comparison page from generated images",
    )
    parser.add_argument("--title", required=True, help="Page title")
    parser.add_argument(
        "--dir", required=True, help="Output directory for the comparison page"
    )
    parser.add_argument("directions_json", help="JSON file with direction metadata")
    parser.add_argument(
        "--no-serve", action="store_true", help="Skip starting HTTP server"
    )
    args = parser.parse_args()

    chop_root = resolve_chop_root()
    template = chop_root / "skills" / "showboat" / "pandoc-template.html"
    if not template.exists():
        print(f"Error: Pandoc template not found: {template}", file=sys.stderr)
        sys.exit(1)

    # Read directions
    directions_path = Path(args.directions_json)
    if not directions_path.exists():
        print(f"Error: Directions file not found: {directions_path}", file=sys.stderr)
        sys.exit(1)

    with open(directions_path) as f:
        directions = json.load(f)

    if not directions:
        print("Error: No directions in JSON file", file=sys.stderr)
        sys.exit(1)

    # Create output directory
    out_dir = Path(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    demo_file = str(out_dir / "demo.md")

    # Initialize showboat document
    run(["showboat", "init", demo_file, args.title])

    # Add summary table
    table_lines = [
        "| # | Name | Section | Vibe | Shirt |",
        "| --- | --- | --- | --- | --- |",
    ]
    for i, d in enumerate(directions):
        label = chr(ord("A") + i)
        table_lines.append(
            f"| {label} | {d['name']} | {d.get('section', 'standalone')} "
            f"| {d.get('vibe', '')} | {d.get('shirt', '')} |"
        )
    run(["showboat", "note", demo_file, "\n".join(table_lines)])

    # Process each direction
    has_magick = shutil.which("magick") is not None
    for i, d in enumerate(directions):
        label = chr(ord("A") + i)
        image_path = Path(d.get("image") or d["output"])

        # Resolve image path - try as-is, then relative to cwd
        if not image_path.exists():
            print(f"Error: Image not found: {image_path}", file=sys.stderr)
            sys.exit(1)

        # Add direction header and description
        note_text = (
            f"## {label}. {d['name']}\n"
            f"**Section:** {d.get('section', 'standalone')}  \n"
            f"**Vibe:** {d.get('vibe', '')}  \n"
            f'**Shirt:** "{d.get("shirt", "")}"'
        )
        run(["showboat", "note", demo_file, note_text])

        # Convert image to PNG for showboat (if needed)
        png_path = out_dir / f"{image_path.stem}.png"
        if image_path.suffix.lower() == ".webp" and has_magick:
            run(["magick", str(image_path), str(png_path)])
        elif image_path.suffix.lower() == ".png":
            shutil.copy2(image_path, png_path)
        else:
            # Try magick for any other format
            if has_magick:
                run(["magick", str(image_path), str(png_path)])
            else:
                shutil.copy2(image_path, png_path)

        # Add image to showboat doc
        run(["showboat", "image", demo_file, str(png_path)])

    # Convert to HTML with pandoc
    html_file = str(out_dir / "demo.html")
    run(
        [
            "pandoc",
            demo_file,
            "-o",
            html_file,
            "--standalone",
            "--metadata",
            f"title={args.title}",
            "--template",
            str(template),
        ]
    )
    print(f"HTML: {html_file}")

    if args.no_serve:
        return

    # Serve locally
    port = find_free_port()
    subprocess.Popen(
        ["python3", "-m", "http.server", str(port), "--bind", "0.0.0.0"],
        cwd=str(out_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    hostname = get_tailscale_hostname()
    url = f"http://{hostname}:{port}/demo.html"
    print(f"Serving at: {url}")


if __name__ == "__main__":
    main()
