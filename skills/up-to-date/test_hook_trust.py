#!/usr/bin/env python3
"""Unit tests for hook_trust.py.

Covers the trust-store contract (add / load / corrupt handling) and the
"read once, hash once, execute once" TOCTOU contract from the design
spec. Per the project CLAUDE.md, signalling/side-effect functions MUST
mock their boundaries — so atomic-write tests exercise real temp files
but never touch the real user home.

Run with: python3 -m unittest test_hook_trust.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from hook_trust import (  # noqa: E402
    HOOK_REL_PATH,
    TRUST_STORE_VERSION,
    classify_trust,
    compute_sha256,
    evaluate_hook,
    hook_path_from_toplevel,
    load_trust_store,
    record_approval,
    trust_store_path,
)


class TestClassifyTrust(unittest.TestCase):
    def test_first_sight_when_no_stored(self):
        self.assertEqual(classify_trust("abc", None), "first_sight")

    def test_trusted_when_hashes_match(self):
        self.assertEqual(classify_trust("abc", "abc"), "trusted")

    def test_changed_when_hashes_differ(self):
        self.assertEqual(classify_trust("abc", "def"), "changed")


class TestLoadTrustStore(unittest.TestCase):
    def test_missing_file_returns_empty_store(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "hooks-trusted.json"
            store, err = load_trust_store(path)
            self.assertIsNone(err)
            self.assertEqual(
                store, {"version": TRUST_STORE_VERSION, "entries": {}}
            )

    def test_valid_file_loads(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "hooks-trusted.json"
            payload = {
                "version": TRUST_STORE_VERSION,
                "entries": {
                    "/repo": {
                        "sha256": "abc",
                        "approved_at": "2026-04-14T00:00:00Z",
                        "hook_path": HOOK_REL_PATH,
                    }
                },
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            store, err = load_trust_store(path)
            self.assertIsNone(err)
            self.assertEqual(store, payload)

    def test_malformed_json_returns_corrupt_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "hooks-trusted.json"
            path.write_text("{not json", encoding="utf-8")
            store, err = load_trust_store(path)
            self.assertIsNone(store)
            assert err is not None
            self.assertEqual(err["code"], "hooks_trusted_corrupt")

    def test_wrong_version_returns_corrupt(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "hooks-trusted.json"
            path.write_text(
                json.dumps({"version": 99, "entries": {}}), encoding="utf-8"
            )
            store, err = load_trust_store(path)
            self.assertIsNone(store)
            assert err is not None
            self.assertEqual(err["code"], "hooks_trusted_corrupt")

    def test_entries_wrong_type_returns_corrupt(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "hooks-trusted.json"
            path.write_text(
                json.dumps({"version": TRUST_STORE_VERSION, "entries": []}),
                encoding="utf-8",
            )
            store, err = load_trust_store(path)
            self.assertIsNone(store)
            assert err is not None
            self.assertEqual(err["code"], "hooks_trusted_corrupt")


class TestEvaluateHook(unittest.TestCase):
    def _setup(self, td: str, content: bytes = b"# hook\n",
               stored_entry: dict | None = None):
        repo = Path(td) / "repo"
        (repo / ".claude").mkdir(parents=True)
        hook = hook_path_from_toplevel(repo)
        hook.write_bytes(content)
        home = Path(td) / "home"
        (home / ".claude" / "claude-md").mkdir(parents=True)
        if stored_entry is not None:
            store = {
                "version": TRUST_STORE_VERSION,
                "entries": {str(repo): stored_entry},
            }
            trust_store_path(home).write_text(json.dumps(store), encoding="utf-8")
        return repo, home

    def test_absent_hook(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            home = Path(td) / "home"
            home.mkdir()
            outcome = evaluate_hook(repo, home)
            self.assertEqual(outcome.status, "absent")

    def test_first_sight_when_no_store_entry(self):
        with tempfile.TemporaryDirectory() as td:
            repo, home = self._setup(td)
            outcome = evaluate_hook(repo, home)
            self.assertEqual(outcome.status, "first_sight")
            self.assertEqual(
                outcome.current_hash, compute_sha256(b"# hook\n")
            )

    def test_trusted_when_hash_matches_stored(self):
        with tempfile.TemporaryDirectory() as td:
            content = b"# trusted content\n"
            stored = {
                "sha256": compute_sha256(content),
                "approved_at": "2026-04-14T00:00:00Z",
                "hook_path": HOOK_REL_PATH,
            }
            repo, home = self._setup(td, content=content, stored_entry=stored)
            outcome = evaluate_hook(repo, home)
            self.assertEqual(outcome.status, "trusted")
            self.assertEqual(outcome.content_bytes, content)

    def test_changed_when_hash_differs(self):
        with tempfile.TemporaryDirectory() as td:
            stored = {
                "sha256": "deadbeef",
                "approved_at": "2026-04-14T00:00:00Z",
                "hook_path": HOOK_REL_PATH,
            }
            repo, home = self._setup(td, stored_entry=stored)
            outcome = evaluate_hook(repo, home)
            self.assertEqual(outcome.status, "changed")

    def test_corrupt_store_marks_outcome_corrupt(self):
        with tempfile.TemporaryDirectory() as td:
            repo, home = self._setup(td)
            trust_store_path(home).write_text("{not json", encoding="utf-8")
            outcome = evaluate_hook(repo, home)
            self.assertEqual(outcome.status, "corrupt")
            assert outcome.error is not None
            self.assertEqual(outcome.error["code"], "hooks_trusted_corrupt")

    def test_symlinked_hook_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".claude").mkdir(parents=True)
            real = Path(td) / "elsewhere.md"
            real.write_text("# hostile", encoding="utf-8")
            hook = hook_path_from_toplevel(repo)
            hook.symlink_to(real)
            home = Path(td) / "home"
            home.mkdir()
            outcome = evaluate_hook(repo, home)
            self.assertEqual(outcome.status, "rejected")
            assert outcome.error is not None
            self.assertEqual(outcome.error["code"], "hook_is_symlink")


class TestTocTouContract(unittest.TestCase):
    """The spec's 'read once, hash once, execute once' contract.

    These tests exercise the guarantee that the bytes handed to the
    hasher are the same object handed back to the caller, and that a
    concurrent mutation of the on-disk file between evaluate_hook()
    and LLM invocation does NOT reach the LLM.
    """

    def test_hook_file_opened_exactly_once_per_run(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".claude").mkdir(parents=True)
            hook_path_from_toplevel(repo).write_bytes(b"# hook\n")
            home = Path(td) / "home"
            home.mkdir()
            # Wrap read_bytes to count calls.
            original = Path.read_bytes
            call_count = {"n": 0}

            def counting_read_bytes(self):  # noqa: ANN001
                if str(self).endswith(HOOK_REL_PATH):
                    call_count["n"] += 1
                return original(self)

            with mock.patch.object(Path, "read_bytes", counting_read_bytes):
                outcome = evaluate_hook(repo, home)
            self.assertEqual(call_count["n"], 1)
            self.assertIsNotNone(outcome.content_bytes)

    def test_in_memory_bytes_survive_post_read_mutation(self):
        """Mutate the file after evaluate_hook returns; outcome bytes
        must still reflect the pre-mutation content — the caller
        reads from memory, not from disk."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".claude").mkdir(parents=True)
            hook = hook_path_from_toplevel(repo)
            original_bytes = b"# trusted hook\n"
            hook.write_bytes(original_bytes)
            home = Path(td) / "home"
            home.mkdir()
            outcome = evaluate_hook(repo, home)
            # Simulate attacker swapping the file between read and
            # execute (the contract says the caller uses outcome.content_bytes).
            hook.write_bytes(b"# hostile replacement\n")
            self.assertEqual(outcome.content_bytes, original_bytes)
            # And the hash reflects the read-once bytes, not the current
            # disk contents.
            self.assertEqual(
                outcome.current_hash, hashlib.sha256(original_bytes).hexdigest()
            )


class TestRecordApproval(unittest.TestCase):
    def test_atomic_write_creates_store(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            home = Path(td) / "home"
            (home / ".claude" / "claude-md").mkdir(parents=True)
            ok, err = record_approval(
                repo_toplevel=repo,
                home=home,
                sha256_hex="abc123",
                now_utc_iso="2026-04-14T12:00:00Z",
            )
            self.assertTrue(ok)
            self.assertIsNone(err)
            store = json.loads(trust_store_path(home).read_text())
            self.assertEqual(
                store["entries"][str(repo)]["sha256"], "abc123"
            )

    def test_atomic_write_uses_tmp_then_replace(self):
        """Verify that the implementation writes to `<name>.tmp` then
        os.replace, so a crash mid-write cannot leave a corrupt store.
        """
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            home = Path(td) / "home"
            (home / ".claude" / "claude-md").mkdir(parents=True)
            real_replace = __import__("os").replace
            observed: dict[str, tuple[str, str]] = {}

            def spying_replace(src, dst):
                observed["call"] = (str(src), str(dst))
                return real_replace(src, dst)

            with mock.patch("os.replace", spying_replace):
                ok, _ = record_approval(
                    repo_toplevel=repo,
                    home=home,
                    sha256_hex="abc123",
                    now_utc_iso="2026-04-14T12:00:00Z",
                )
            self.assertTrue(ok)
            src, dst = observed["call"]
            self.assertTrue(src.endswith(".tmp"))
            self.assertEqual(dst, str(trust_store_path(home)))

    def test_refuses_to_overwrite_corrupt_store(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            home = Path(td) / "home"
            (home / ".claude" / "claude-md").mkdir(parents=True)
            trust_store_path(home).write_text("{not json", encoding="utf-8")
            ok, err = record_approval(
                repo_toplevel=repo,
                home=home,
                sha256_hex="abc123",
                now_utc_iso="2026-04-14T12:00:00Z",
            )
            self.assertFalse(ok)
            assert err is not None
            self.assertEqual(err["code"], "hooks_trusted_corrupt")
            # Corrupt file must NOT have been overwritten.
            self.assertEqual(
                trust_store_path(home).read_text(), "{not json"
            )


if __name__ == "__main__":
    unittest.main()
