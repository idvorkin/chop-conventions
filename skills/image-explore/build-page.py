#!/usr/bin/env python3
# ABOUTME: Builds a showboat comparison page from generated images and direction metadata.
# ABOUTME: Creates showboat doc, converts to HTML via pandoc, and serves locally.
#
# Usage: build-page.py --title "Title" --dir output-dir/ [--images-dir path/] directions.json
#
# directions.json format (without variants):
# [
#   {"name": "Mountain Vista", "section": "Purpose", "vibe": "Quiet awe",
#    "shirt": "NORTH", "image": "mountain.webp"}
# ]
#
# directions.json format (with variants — group field enables grouping):
# [
#   {"name": "Mountain Vista v1", "group": "Mountain Vista", "section": "Purpose",
#    "vibe": "Quiet awe", "shirt": "NORTH", "image": "mountain-v1.webp"},
#   {"name": "Mountain Vista v2", "group": "Mountain Vista", "section": "Purpose",
#    "vibe": "Quiet awe", "shirt": "NORTH", "image": "mountain-v2.webp"}
# ]

import argparse
import json
import shutil
import socket
import subprocess
import sys
from collections import OrderedDict
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


def resolve_image(image_field, images_dir=None):
    """Resolve an image path, trying multiple locations.

    Search order:
    1. As-is (absolute or relative to cwd)
    2. Relative to --images-dir (if provided)
    3. Relative to cwd/images/
    """
    path = Path(image_field)
    if path.exists():
        return path

    if images_dir:
        candidate = Path(images_dir) / path.name
        if candidate.exists():
            return candidate
        candidate = Path(images_dir) / path
        if candidate.exists():
            return candidate

    # Try images/ subdirectory of cwd
    candidate = Path("images") / path.name
    if candidate.exists():
        return candidate

    return None


def group_directions(directions):
    """Group directions by their 'group' field.

    Returns OrderedDict of group_name -> list of entries.
    Entries without a group field are treated as their own group (using name).
    """
    groups = OrderedDict()
    for d in directions:
        group_name = d.get("group") or d["name"]
        if group_name not in groups:
            groups[group_name] = []
        groups[group_name].append(d)
    return groups


def main():
    parser = argparse.ArgumentParser(
        description="Build a showboat comparison page from generated images",
    )
    parser.add_argument("--title", required=True, help="Page title")
    parser.add_argument(
        "--dir", required=True, help="Output directory for the comparison page"
    )
    parser.add_argument(
        "--images-dir",
        default=None,
        help="Directory to search for generated images (e.g., images/)",
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

    # Remove stale demo.md if it exists (showboat init fails on existing files)
    demo_path = Path(demo_file)
    if demo_path.exists():
        demo_path.unlink()

    # Initialize showboat document
    run(["showboat", "init", demo_file, args.title])

    has_magick = shutil.which("magick") is not None
    has_groups = any("group" in d for d in directions)

    if has_groups:
        _build_grouped_page(directions, demo_file, out_dir, has_magick, args.images_dir)
    else:
        _build_flat_page(directions, demo_file, out_dir, has_magick, args.images_dir)

    # Fix pandoc YAML issue: replace --- separators with *** in the generated markdown
    # (pandoc interprets --- as YAML metadata delimiters)
    content = demo_path.read_text()
    # Only replace --- that appear as standalone separators (line by themselves)
    import re

    content = re.sub(r"^---$", "***", content, flags=re.MULTILINE)
    demo_path.write_text(content)

    # Convert to HTML with pandoc
    html_file = str(out_dir / "demo.html")
    run(
        [
            "pandoc",
            demo_file,
            "-o",
            html_file,
            "--standalone",
            "--wrap=none",
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


def _html_escape(text):
    """Escape HTML special characters in text."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _attr_caption(label, name, scene=""):
    """Build an HTML caption string entity-encoded for use inside an attribute.

    The browser decodes entities when reading getAttribute(), so innerHTML
    gets the actual HTML tags. We entity-encode so pandoc doesn't confuse
    the tags with real HTML structure.
    """
    parts = [
        f"&lt;strong&gt;{_html_escape(label)}. {_html_escape(name)}&lt;/strong&gt;"
    ]
    if scene:
        parts.append(f"&lt;br&gt;{_html_escape(scene)}")
    return "".join(parts)


def _convert_image(d, out_dir, has_magick, images_dir):
    """Resolve and convert a single image to PNG. Returns PNG filename or None."""
    image_field = d.get("image") or d["output"]
    image_path = resolve_image(image_field, images_dir)

    if image_path is None:
        print(f"Warning: Image not found: {image_field}", file=sys.stderr)
        return None

    # Convert image to PNG (if needed)
    png_path = out_dir / f"{image_path.stem}.png"
    if image_path.suffix.lower() == ".png":
        shutil.copy2(image_path, png_path)
    elif has_magick:
        run(["magick", str(image_path), str(png_path)])
    else:
        shutil.copy2(image_path, png_path)

    return png_path.name


def _convert_and_add_image(d, demo_file, out_dir, has_magick, images_dir):
    """Resolve, convert, and add a single image to the showboat doc (flat mode)."""
    png_name = _convert_image(d, out_dir, has_magick, images_dir)
    if png_name is None:
        image_field = d.get("image") or d["output"]
        run(["showboat", "note", demo_file, f"*Image not found: {image_field}*"])
        return

    # Build caption from direction metadata
    name = d.get("name", "")
    scene = d.get("scene", "")
    caption = _attr_caption("", name, scene)

    # Add image with data-caption for lightbox (raw HTML so template JS picks it up)
    run(
        [
            "showboat",
            "note",
            demo_file,
            f'<img src="{png_name}" data-caption="{caption}" '
            f'style="max-width:100%;" />',
        ]
    )


def _build_flat_page(directions, demo_file, out_dir, has_magick, images_dir):
    """Build page with one section per direction (no grouping)."""
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
    for i, d in enumerate(directions):
        label = chr(ord("A") + i)

        note_text = (
            f"## {label}. {d['name']}\n"
            f"**Section:** {d.get('section', 'standalone')}  \n"
            f"**Vibe:** {d.get('vibe', '')}  \n"
            f'**Shirt:** "{d.get("shirt", "")}"'
        )
        run(["showboat", "note", demo_file, note_text])
        _convert_and_add_image(d, demo_file, out_dir, has_magick, images_dir)


def _build_grouped_page(directions, demo_file, out_dir, has_magick, images_dir):
    """Build page with grouped variants displayed horizontally in tables."""
    groups = group_directions(directions)

    # Add summary table (one row per group)
    table_lines = [
        "| # | Name | Section | Vibe | Shirt | Variants |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for i, (group_name, entries) in enumerate(groups.items()):
        label = chr(ord("A") + i)
        first = entries[0]
        table_lines.append(
            f"| {label} | {group_name} | {first.get('section', 'standalone')} "
            f"| {first.get('vibe', '')} | {first.get('shirt', '')} "
            f"| {len(entries)} |"
        )
    run(["showboat", "note", demo_file, "\n".join(table_lines)])

    # Process each group: convert images first, then build horizontal table
    for i, (group_name, entries) in enumerate(groups.items()):
        label = chr(ord("A") + i)
        first = entries[0]

        # Group heading
        group_note = (
            f"## {label}. {group_name}\n"
            f"**Section:** {first.get('section', 'standalone')}  \n"
            f"**Vibe:** {first.get('vibe', '')}  \n"
            f'**Shirt:** "{first.get("shirt", "")}"'
        )
        run(["showboat", "note", demo_file, group_note])

        # Convert all variant images
        variant_pngs = []
        for d in entries:
            png_name = _convert_image(d, out_dir, has_magick, images_dir)
            variant_pngs.append(png_name)

        # Build HTML table with variants side-by-side
        n = len(entries)
        col_width = max(30, 100 // n)
        html_lines = [
            '<table style="width:100%; border-collapse:collapse; margin:1em 0;">',
            "  <tr>",
        ]
        # Header row with variant labels
        for j, d in enumerate(entries):
            variant_label = f"{label}{j + 1}"
            variant_name = d["name"]
            if variant_name == group_name:
                variant_name = f"Variant {j + 1}"
            html_lines.append(
                f'    <th style="text-align:center; padding:4px; width:{col_width}%;">'
                f"{variant_label}. {variant_name}</th>"
            )
        html_lines.append("  </tr>")

        # Image row
        html_lines.append("  <tr>")
        for j, png_name in enumerate(variant_pngs):
            d = entries[j]
            variant_label = f"{label}{j + 1}"
            variant_name = d["name"]
            scene = d.get("scene", "")
            caption = _attr_caption(variant_label, variant_name, scene)
            if png_name:
                html_lines.append(
                    f'    <td style="text-align:center; padding:4px; vertical-align:top;">'
                    f'<img src="{png_name}" '
                    f'data-caption="{caption}" '
                    f'style="max-width:100%; height:auto;" /></td>'
                )
            else:
                image_field = d.get("image") or d["output"]
                html_lines.append(
                    f'    <td style="text-align:center; padding:4px; vertical-align:top;">'
                    f"<em>Not found: {image_field}</em></td>"
                )
        html_lines.append("  </tr>")
        html_lines.append("</table>")

        run(["showboat", "note", demo_file, "\n".join(html_lines)])


if __name__ == "__main__":
    main()
