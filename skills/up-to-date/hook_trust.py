#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Trust-store helper for `post-up-to-date.md` hooks.

Implements the "read once, hash once, execute once" security contract
described in the claude-md-sharing design:

1. Read the hook bytes from disk exactly once per `/up-to-date` run.
2. Hash the in-memory bytes and compare against the per-machine
   allowlist at `~/.claude/claude-md/hooks-trusted.json`.
3. Feed the same in-memory bytes to the caller for LLM execution — the
   caller MUST NOT re-open the file between the hash check and use.

A corrupt trust-store is never silently overwritten; a symlinked hook
is rejected at `check_post_up_to_date` upstream before the trust path
ever runs.

Tested as a library via `test_hook_trust.py`.

Usage:
    ./hook_trust.py --repo-toplevel <path> --pretty
        prints JSON describing {hook_content_bytes (b64), trust_status,
        stored_hash, current_hash, repo_toplevel, action_required}
        so the calling skill can prompt the user and persist the
        approval.

    ./hook_trust.py --approve --repo-toplevel <path>
        records the current hash in the trust store (atomic write),
        creating the file if absent. Does NOT re-prompt — the calling
        skill is responsible for collecting explicit user approval
        before invoking --approve.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TRUST_STORE_VERSION = 1
HOOK_REL_PATH = ".claude/post-up-to-date.md"


@dataclass(frozen=True)
class TrustOutcome:
    """Result of consulting the trust store for a single hook.

    `status` is one of:
      - "trusted"   — stored hash matches current hash; skill runs hook.
      - "first_sight" — no entry for this repo; skill prompts user.
      - "changed"   — entry exists but hash differs; skill re-prompts.
      - "corrupt"   — trust store unreadable/malformed; skill skips hook.
      - "absent"    — no hook file present; skill does nothing.
      - "rejected"  — symlink or other upstream rejection; skill skips.
    """

    status: str
    current_hash: str | None
    stored_hash: str | None
    content_bytes: bytes | None
    error: dict[str, Any] | None = None


def hook_path_from_toplevel(repo_toplevel: Path) -> Path:
    return repo_toplevel / HOOK_REL_PATH


def trust_store_path(home: Path) -> Path:
    return home / ".claude" / "claude-md" / "hooks-trusted.json"


def load_trust_store(
    store_path: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Load the trust store.

    Returns `(store_dict_or_none, error_or_none)`:
      - missing file → `({"version": 1, "entries": {}}, None)`
      - valid file   → `(parsed, None)`
      - corrupt file → `(None, error_dict)`

    Per the spec the skill MUST NOT overwrite a corrupt store; it
    surfaces the error and skips hook execution.
    """
    if not store_path.exists():
        return {"version": TRUST_STORE_VERSION, "entries": {}}, None
    try:
        raw = store_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, {
            "subsystem": "post_up_to_date",
            "code": "hooks_trusted_unreadable",
            "message": f"{store_path} unreadable: {exc}",
            "path": str(store_path),
        }
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, {
            "subsystem": "post_up_to_date",
            "code": "hooks_trusted_corrupt",
            "message": f"{store_path} is not valid JSON: {exc.msg}",
            "path": str(store_path),
        }
    if not isinstance(parsed, dict):
        return None, {
            "subsystem": "post_up_to_date",
            "code": "hooks_trusted_corrupt",
            "message": f"{store_path} root is not a JSON object",
            "path": str(store_path),
        }
    version = parsed.get("version")
    if version != TRUST_STORE_VERSION:
        return None, {
            "subsystem": "post_up_to_date",
            "code": "hooks_trusted_corrupt",
            "message": (
                f"{store_path} version={version!r}, expected "
                f"{TRUST_STORE_VERSION}"
            ),
            "path": str(store_path),
        }
    entries = parsed.get("entries")
    if not isinstance(entries, dict):
        return None, {
            "subsystem": "post_up_to_date",
            "code": "hooks_trusted_corrupt",
            "message": f"{store_path} `entries` is not an object",
            "path": str(store_path),
        }
    return parsed, None


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify_trust(
    current_hash: str,
    stored_hash: str | None,
) -> str:
    """Pure classification: which action does the skill need to take?"""
    if stored_hash is None:
        return "first_sight"
    if stored_hash == current_hash:
        return "trusted"
    return "changed"


def evaluate_hook(
    repo_toplevel: Path,
    home: Path,
) -> TrustOutcome:
    """Perform the one-shot read+hash+classify sequence.

    The hook file is opened exactly once; its bytes are held in the
    returned `TrustOutcome.content_bytes` so the caller can feed them
    to the LLM without re-reading from disk.
    """
    hook_path = hook_path_from_toplevel(repo_toplevel)
    if not hook_path.exists():
        return TrustOutcome(
            status="absent",
            current_hash=None,
            stored_hash=None,
            content_bytes=None,
        )
    if hook_path.is_symlink():
        # Upstream (`check_post_up_to_date` in diagnose.py) already
        # emits an error for this case, but we enforce again for
        # defense-in-depth: even if a caller bypassed diagnose, the
        # trust evaluator refuses symlinks outright.
        return TrustOutcome(
            status="rejected",
            current_hash=None,
            stored_hash=None,
            content_bytes=None,
            error={
                "subsystem": "post_up_to_date",
                "code": "hook_is_symlink",
                "message": "Refusing to read a symlinked hook",
                "path": str(hook_path),
            },
        )
    # Single read — TOCTOU-safe for downstream use.
    try:
        content = hook_path.read_bytes()
    except OSError as exc:
        return TrustOutcome(
            status="rejected",
            current_hash=None,
            stored_hash=None,
            content_bytes=None,
            error={
                "subsystem": "post_up_to_date",
                "code": "hook_unreadable",
                "message": f"{hook_path} unreadable: {exc}",
                "path": str(hook_path),
            },
        )
    current_hash = compute_sha256(content)
    store, err = load_trust_store(trust_store_path(home))
    if err is not None:
        return TrustOutcome(
            status="corrupt",
            current_hash=current_hash,
            stored_hash=None,
            content_bytes=content,
            error=err,
        )
    assert store is not None
    entry = store["entries"].get(str(repo_toplevel))
    stored_hash = entry.get("sha256") if isinstance(entry, dict) else None
    return TrustOutcome(
        status=classify_trust(current_hash, stored_hash),
        current_hash=current_hash,
        stored_hash=stored_hash,
        content_bytes=content,
    )


def record_approval(
    repo_toplevel: Path,
    home: Path,
    sha256_hex: str,
    now_utc_iso: str,
) -> tuple[bool, dict[str, Any] | None]:
    """Persist an approval for a repo's hook at the given hash.

    Atomic: writes to `<store>.tmp` then `os.replace`s into place so
    a crash mid-write cannot leave a corrupt JSON. The parent
    directory (`~/.claude/claude-md/`) must exist and must NOT be a
    symlink — caller is responsible for the `is_symlink()` guard and
    the `mkdir -p` before invoking this.

    Returns `(True, None)` on success or `(False, error_dict)` if the
    store is corrupt (record_approval refuses to overwrite).
    """
    store_path = trust_store_path(home)
    store, err = load_trust_store(store_path)
    if err is not None:
        return False, err
    assert store is not None
    store["entries"][str(repo_toplevel)] = {
        "sha256": sha256_hex,
        "approved_at": now_utc_iso,
        "hook_path": HOOK_REL_PATH,
    }
    store_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = store_path.with_name(store_path.name + ".tmp")
    tmp_path.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, store_path)
    return True, None


def _iso_utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate and record trust for post-up-to-date hooks"
    )
    parser.add_argument(
        "--repo-toplevel",
        required=True,
        help="Absolute path to the repo toplevel (git rev-parse --show-toplevel)",
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Record approval for the current hook hash",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    repo_toplevel = Path(args.repo_toplevel).resolve()
    home = Path.home()

    if args.approve:
        outcome = evaluate_hook(repo_toplevel, home)
        if outcome.current_hash is None:
            payload = {
                "ok": False,
                "error": outcome.error
                or {
                    "code": "no_hook_to_approve",
                    "message": "No hook file present",
                    "path": str(hook_path_from_toplevel(repo_toplevel)),
                },
            }
            json.dump(payload, sys.stdout, indent=2 if args.pretty else None)
            sys.stdout.write("\n")
            return 1
        ok, err = record_approval(
            repo_toplevel=repo_toplevel,
            home=home,
            sha256_hex=outcome.current_hash,
            now_utc_iso=_iso_utc_now(),
        )
        payload = {
            "ok": ok,
            "error": err,
            "sha256": outcome.current_hash if ok else None,
        }
        json.dump(payload, sys.stdout, indent=2 if args.pretty else None)
        sys.stdout.write("\n")
        return 0 if ok else 1

    outcome = evaluate_hook(repo_toplevel, home)
    payload = {
        "status": outcome.status,
        "current_hash": outcome.current_hash,
        "stored_hash": outcome.stored_hash,
        "content_b64": (
            base64.b64encode(outcome.content_bytes).decode("ascii")
            if outcome.content_bytes is not None
            else None
        ),
        "error": outcome.error,
    }
    json.dump(payload, sys.stdout, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
