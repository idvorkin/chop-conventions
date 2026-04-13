#!/usr/bin/env python3
# ABOUTME: Publishes a showboat HTML comparison page to a GitHub Gist via gisthost.
# ABOUTME: Handles image conversion, URL rewriting, and git push in one step.
#
# Usage: publish-gist.py <html-file> [image-files...] [--title "description"]
#
# What it does:
#   1. Creates a public gist with the HTML (as index.html)
#   2. Clones the gist repo to a temp directory
#   3. Converts provided images to JPEG (quality 75)
#   4. Rewrites <img src="..."> in index.html to absolute gist raw URLs
#   5. Git adds, commits, pushes
#   6. Prints the gisthost URL
#
# Requirements: gh (GitHub CLI), git, magick (ImageMagick), python3

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd, **kwargs):
    """Run a shell command and return stdout."""
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        print(f"Error running: {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def find_images_in_html(html_path):
    """Find all local image src references in the HTML."""
    with open(html_path) as f:
        html = f.read()
    return re.findall(r'src="([^"]+\.(?:png|webp|jpg|jpeg))"', html)


def main():
    parser = argparse.ArgumentParser(
        description="Publish a showboat comparison page to gisthost"
    )
    parser.add_argument("html_file", help="Path to the HTML file to publish")
    parser.add_argument(
        "images",
        nargs="*",
        help="Image files to include (auto-detected from HTML if omitted)",
    )
    parser.add_argument("--title", default="Image Comparison", help="Gist description")
    args = parser.parse_args()

    html_path = Path(args.html_file).resolve()
    if not html_path.exists():
        print(f"Error: HTML file not found: {html_path}", file=sys.stderr)
        sys.exit(1)

    html_dir = html_path.parent

    # Find images - either from args or by scanning the HTML
    if args.images:
        image_files = [Path(img).resolve() for img in args.images]
    else:
        img_srcs = find_images_in_html(html_path)
        image_files = []
        for src in img_srcs:
            # Try as absolute, then relative to HTML dir
            p = Path(src)
            if p.exists():
                image_files.append(p.resolve())
            elif (html_dir / src).exists():
                image_files.append((html_dir / src).resolve())
            else:
                print(f"Warning: Image not found, skipping: {src}", file=sys.stderr)

    if not image_files:
        print("Warning: No images found to include", file=sys.stderr)

    print(f"Publishing {html_path.name} with {len(image_files)} images...")

    # Step 1: Create the gist
    print("Creating gist...")
    gist_url = run(
        ["gh", "gist", "create", "--public", "-d", args.title, str(html_path)]
    )
    # Extract gist ID from URL (last path component)
    gist_id = gist_url.rstrip("/").split("/")[-1]
    print(f"Gist created: {gist_id}")

    # Get the authenticated user
    gist_user = run(["gh", "api", "user", "-q", ".login"])

    # Step 2: Clone the gist
    work_dir = tempfile.mkdtemp(prefix="gist-publish-")
    token = run(["gh", "auth", "token"])
    run(
        [
            "git",
            "clone",
            f"https://x-access-token:{token}@gist.github.com/{gist_id}.git",
            work_dir,
        ]
    )

    # Step 3: Rename HTML to index.html and convert images to JPEG
    src_html = Path(work_dir) / html_path.name
    dst_html = Path(work_dir) / "index.html"
    if src_html.exists() and src_html != dst_html:
        src_html.rename(dst_html)

    gist_raw = f"https://gist.githubusercontent.com/{gist_user}/{gist_id}/raw"
    url_map = {}  # old src -> new absolute URL

    for img in image_files:
        # Convert to JPEG with descriptive name
        stem = img.stem
        # Strip UUID-style names, try to keep descriptive ones
        jpg_name = f"{stem}.jpg"
        jpg_path = Path(work_dir) / jpg_name

        has_magick = shutil.which("magick") is not None
        if has_magick:
            run(["magick", str(img), "-quality", "75", str(jpg_path)])
        else:
            # Fallback: just copy the file
            shutil.copy2(img, Path(work_dir) / img.name)
            jpg_name = img.name

        url_map[img.name] = f"{gist_raw}/{jpg_name}"
        print(f"  {img.name} -> {jpg_name}")

    # Step 4: Rewrite image URLs in index.html
    with open(dst_html) as f:
        html = f.read()

    for old_name, new_url in url_map.items():
        html = html.replace(f'src="{old_name}"', f'src="{new_url}"')

    with open(dst_html, "w") as f:
        f.write(html)

    # Step 5: Git add, commit, push
    print("Pushing to gist...")
    run(["git", "add", "."], cwd=work_dir)
    run(
        ["git", "commit", "-m", "Add images and update HTML with absolute URLs"],
        cwd=work_dir,
    )
    run(["git", "push"], cwd=work_dir)

    # Cleanup
    shutil.rmtree(work_dir, ignore_errors=True)

    # Step 6: Print the shareable URL
    gisthost_url = f"https://gisthost.github.io/?{gist_id}"
    print(f"\nPublished! Share this link:\n{gisthost_url}")
    return gisthost_url


if __name__ == "__main__":
    main()
