#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "typer>=0.12",
# ]
# ///
# ABOUTME: Strip image background via Recraft's removeBackground API.
# ABOUTME: The bg-removal path used by gen-image's --transparent flag.
#
# Usage:
#   recraft_bg_remove.py strip <input> <output>      # CLI mode
#   recraft_bg_remove.py balance                     # Check credit balance
#
# As a library (called from generate.py):
#   from recraft_bg_remove import strip_background
#   ok, err = strip_background("/path/in.png", "/path/out.png")
#
# Costs ~$0.01/call (~7-15s wall). See:
#   https://www.recraft.ai/docs/api-reference/endpoints
# Image limits: 5 MB, 16 MP, max dim 4096 px, min dim 256 px,
# formats PNG/JPG/WEBP. Reads RECRAFT_API_TOKEN from env or ~/.env.
#
# Pure-function layer (load_token, strip_background, get_balance) is
# stdlib-only — typer is lazy-imported inside _build_app() so tests
# can import this module without uv. See the chop-conventions CLAUDE.md
# rule "Lazy-import PEP 723 deps that tests don't need".

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

API_BASE = "https://external.api.recraft.ai/v1"
REMOVE_BG_PATH = "/images/removeBackground"
USER_PATH = "/users/me"

# Recraft documented input limits (from /docs/api-reference/endpoints).
# These are pre-flight checks so we fail fast with a clear error rather
# than waiting for the API to reject and burning a round-trip.
MAX_FILE_BYTES = 5 * 1024 * 1024
MIN_DIM_PX = 256
MAX_DIM_PX = 4096
SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# Output sanity threshold. Recraft sometimes returns a tiny payload on
# unexpected errors (a few hundred bytes); guard against silent truncation.
MIN_OUTPUT_BYTES = 1024

# WebP conversion quality when the caller asks for `.webp` output.
# Matches what gemini-image.sh uses (`cwebp -q 90`) so file sizes and
# visuals stay consistent with Gemini's direct WebP output.
WEBP_QUALITY = 90


def load_token(env_file: str = "~/.env") -> str | None:
    """Resolve RECRAFT_API_TOKEN from env or ~/.env.

    Returns the token string if found, None otherwise. Mirrors generate.py's
    load_env() pattern: env wins over file, file values do NOT override env.
    """
    token = os.environ.get("RECRAFT_API_TOKEN")
    if token:
        return token
    path = Path(env_file).expanduser()
    if not path.exists():
        return None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            # Tolerate both `KEY=val` and `export KEY=val` forms — `~/.env`
            # files commonly use the shell-source-friendly `export` prefix.
            if line.startswith("export "):
                line = line[len("export ") :].lstrip()
            key, _, value = line.partition("=")
            key = key.strip()
            if key == "RECRAFT_API_TOKEN":
                return value.strip().strip('"').strip("'")
    return None


def _validate_input(input_path: str) -> str | None:
    """Pre-flight check input against Recraft's documented limits.

    Returns an error string on failure, None when the input passes. Cheap
    stat + extension check; no image decode (avoids pillow dep).
    """
    p = Path(input_path)
    if not p.exists():
        return f"input not found: {input_path}"
    if not p.is_file():
        return f"input is not a regular file: {input_path}"
    size = p.stat().st_size
    if size == 0:
        return f"input is empty: {input_path}"
    if size > MAX_FILE_BYTES:
        return f"input is {size} bytes, exceeds Recraft's {MAX_FILE_BYTES}-byte limit"
    if p.suffix.lower() not in SUPPORTED_EXTS:
        return (
            f"unsupported extension {p.suffix!r}; "
            f"Recraft accepts {sorted(SUPPORTED_EXTS)}"
        )
    return None


def _build_multipart(input_path: str) -> tuple[bytes, str]:
    """Build a multipart/form-data body with the image + b64_json response_format.

    Returns (body_bytes, content_type_header). Stdlib-only — using urllib
    instead of requests/httpx keeps the dep surface to "typer + transitive
    rich" which matches the rest of chop-conventions' Typer scripts.
    """
    boundary = f"----recraft-{uuid.uuid4().hex}"
    crlf = b"\r\n"
    parts: list[bytes] = []

    # response_format field — keep b64_json so we get bytes back in one
    # round trip and skip the transient-URL race.
    parts.append(f"--{boundary}".encode())
    parts.append(b'Content-Disposition: form-data; name="response_format"')
    parts.append(b"")
    parts.append(b"b64_json")

    # file field
    filename = Path(input_path).name
    ext = Path(input_path).suffix.lower().lstrip(".")
    mime = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
    }.get(ext, "application/octet-stream")
    with open(input_path, "rb") as f:
        file_bytes = f.read()

    parts.append(f"--{boundary}".encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode()
    )
    parts.append(f"Content-Type: {mime}".encode())
    parts.append(b"")
    body = crlf.join(parts) + crlf + file_bytes + crlf
    body += f"--{boundary}--{crlf.decode()}".encode()

    return body, f"multipart/form-data; boundary={boundary}"


def _write_with_format(img_bytes: bytes, output_path: str) -> tuple[bool, str | None]:
    """Persist Recraft's PNG bytes at output_path, converting if the
    caller requested .webp.

    Recraft's removeBackground endpoint only emits PNG with alpha. If
    the caller asks for .webp, we go PNG → cwebp → WebP-with-alpha (same
    `cwebp -q 90` invocation gemini-image.sh uses, so visual quality
    stays consistent with the upstream Gemini output). For any other
    extension, we write the PNG bytes verbatim (caller's problem if they
    asked for .jpg — alpha would be dropped). Falls back to PNG if cwebp
    is missing, with a clear message rather than silent rename.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = out.suffix.lower()

    if ext != ".webp":
        out.write_bytes(img_bytes)
        return True, None

    cwebp = shutil.which("cwebp")
    if cwebp is None:
        return False, (
            "output requested as .webp but cwebp is not installed. "
            "Install webp tools (`brew install webp` / `apt install webp`) "
            "or pass a .png output path."
        )

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
        tmp.write(img_bytes)

    try:
        result = subprocess.run(
            [cwebp, "-q", str(WEBP_QUALITY), tmp_path, "-o", str(out)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False, (
                f"cwebp failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return True, None


def strip_background(
    input_path: str,
    output_path: str,
    token: str | None = None,
    timeout_s: float = 60.0,
) -> tuple[bool, str | None]:
    """Strip the background from input_path; write transparent image to output_path.

    Returns (success: bool, error: str | None). Called from generate.py's
    remove_background_recraft() under --transparent — Recraft is the only
    bg-removal path. The post-strip eval pass (alpha-mean + interior-hole +
    edge-fringe) runs on the result downstream.

    Output format follows the extension on `output_path`:
        - .png  → Recraft's PNG-with-alpha bytes verbatim
        - .webp → Recraft PNG → cwebp -q 90 → WebP-with-alpha (matches
                  gemini-image.sh's quality settings)
        - other → PNG bytes written verbatim (caller's problem if alpha is lost)

    Returns:
        (True, None)  on success
        (False, "..") with a human-readable error message on any failure
                      (no token, validation, HTTP error, malformed response,
                      truncated output, missing cwebp for .webp output).
    """
    if token is None:
        token = load_token()
    if not token:
        return False, "RECRAFT_API_TOKEN not set in environment or ~/.env"

    err = _validate_input(input_path)
    if err is not None:
        return False, err

    body, content_type = _build_multipart(input_path)

    req = urllib.request.Request(
        f"{API_BASE}{REMOVE_BG_PATH}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as e:
        # Recraft errors come back as JSON; surface the message for debugging.
        detail = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            detail = err_body[:500]
        except Exception:  # noqa: BLE001 — best-effort error surfacing
            pass
        return False, f"recraft HTTP {e.code} {e.reason}: {detail}"
    except urllib.error.URLError as e:
        return False, f"recraft network error: {e.reason}"
    except OSError as e:
        return False, f"recraft I/O error: {e}"

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        return False, f"recraft returned non-JSON ({e}): {payload[:200]!r}"

    b64 = data.get("image", {}).get("b64_json")
    if not b64:
        return False, f"recraft response missing image.b64_json: {data}"

    try:
        img_bytes = base64.b64decode(b64)
    except (ValueError, TypeError) as e:
        return False, f"recraft b64 decode failed: {e}"

    if len(img_bytes) < MIN_OUTPUT_BYTES:
        return False, (
            f"recraft output suspiciously small ({len(img_bytes)} bytes); "
            "treating as failure"
        )

    return _write_with_format(img_bytes, output_path)


def get_balance(token: str | None = None) -> tuple[dict | None, str | None]:
    """Fetch account info + remaining credits from /users/me.

    Returns ({"credits": int, "email": str, ...}, None) on success, or
    (None, "error") on failure. Useful for the doctor / sanity checks
    without burning credits on a real removeBackground call.
    """
    if token is None:
        token = load_token()
    if not token:
        return None, "RECRAFT_API_TOKEN not set in environment or ~/.env"

    req = urllib.request.Request(
        f"{API_BASE}{USER_PATH}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} {e.reason}"
    except urllib.error.URLError as e:
        return None, f"network error: {e.reason}"


def _build_app():
    """Wire the Typer app. Called only from __main__ so tests skip the typer import."""
    import typer

    app = typer.Typer(
        help="Strip image backgrounds via Recraft API (PNG with alpha).",
        add_completion=False,
        no_args_is_help=True,
    )

    @app.command()
    def strip(
        input_path: str = typer.Argument(..., help="Input image (PNG/JPG/WEBP)"),
        output_path: str = typer.Argument(..., help="Output PNG with alpha"),
        timeout: float = typer.Option(
            60.0, "--timeout", help="HTTP timeout in seconds (default 60)"
        ),
    ) -> None:
        """Strip the background from one image. ~$0.01/call, ~7-15s wall."""
        ok, err = strip_background(input_path, output_path, timeout_s=timeout)
        if not ok:
            print(f"Error: {err}", file=sys.stderr)
            raise typer.Exit(1)
        size = Path(output_path).stat().st_size
        print(f"OK: {output_path} ({size} bytes)")

    @app.command()
    def balance() -> None:
        """Print the Recraft account email + remaining credit balance."""
        info, err = get_balance()
        if err is not None:
            print(f"Error: {err}", file=sys.stderr)
            raise typer.Exit(1)
        # 1000 units = $1.00; show both
        credits = info.get("credits", 0)
        usd = credits / 1000.0
        print(f"email:   {info.get('email', '?')}")
        print(f"credits: {credits} (~${usd:.2f})")
        print(f"calls:   ~{credits // 10} bg-remove (~$0.01 each)")

    return app


if __name__ == "__main__":
    _build_app()()
