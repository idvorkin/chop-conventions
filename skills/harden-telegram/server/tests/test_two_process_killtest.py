# pyright: reportMissingImports=false
# ^ pytest comes from the ephemeral uv venv; telegram_bot is imported via a
#   runtime sys.path.insert — Pyright can't follow either, but both resolve
#   at test time under `uv run --with pytest --python 3.12 python3 -m pytest`.
"""Integration kill-test for the two-process Telegram architecture.

Bead: igor2-bgt.2. Plan: docs/superpowers/plans/2026-04-12-telegram-two-process-migration.md
Spec: docs/superpowers/specs/2026-04-12-telegram-two-process-design.md

Goal: prove that messages queued to inbound.db while server.ts is dead are
delivered to Claude's MCP stream after server.ts restarts. The catch-up path
in server.ts (`selectUndelivered` + `catchup()`) is the durability guarantee
for the whole migration — if it breaks, messages get lost silently.

Strategy (Option B in the task brief): the test plays the role of
telegram_bot.py. We do NOT run telegram_bot.py here. Instead we:

  1. Initialize a fresh inbound.db using telegram_bot.init_db_sync
     (real schema, same code path production uses).
  2. Stage access.json with an allowlist so server.ts's gate won't drop rows.
  3. Spawn `bun server.ts` with TELEGRAM_STATE_DIR + LARRY_TELEGRAM_DIR pointed
     at a throwaway directory so production state is untouched.
  4. Insert rows into inbound.db (mimicking what telegram_bot.py would do).
     No bot.sock — server.ts falls back to 2s interval polling, which is
     exactly the path we want to exercise for catch-up correctness.
  5. Read MCP notifications off server.ts's stdout (they're newline-delimited
     JSON-RPC 2.0, written by StdioServerTransport.send()). Match on
     `notifications/claude/channel`.

The test is marked `@pytest.mark.slow`: it spawns two real bun subprocesses
(one before the kill, one after) and waits up to 10s per phase for delivery.
Wall-clock budget is ~15-20s per run; run with `pytest -m slow`.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

# telegram_bot.py lives in the parent `server/` directory, not the tests/ dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram_bot import init_db_sync  # noqa: E402

# After vendoring into the skill, server.ts is a sibling of tests/ — not under
# a `telegram-server/` subdirectory anymore.
REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_TS = REPO_ROOT / "server.ts"

# Phase budgets (seconds). Catch-up runs on the 2s fallback poll interval, so
# 10s gives us at least 4 poll ticks of slack before failing the assertion.
STARTUP_WAIT = 5.0
DELIVERY_WAIT = 12.0
SHUTDOWN_WAIT = 5.0

# Allowlisted fake user id for the test. Matches stringified Telegram user_id.
TEST_USER_ID = "42"
TEST_CHAT_ID = "10042"


# Suppress the unknown-mark warning for `slow` without adding a conftest.
# (Registering the mark properly would require a conftest.py, but the task
# brief constrains us to a single file.)
pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnknownMarkWarning")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _insert_row(
    db_path: Path, text: str, message_id: int, message_type: str = "message"
) -> int:
    """Mimic telegram_bot.py's INSERT + busy_timeout behavior."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        cur = conn.execute(
            """INSERT INTO inbound
               (ts, chat_id, message_id, user_id, username, message_type, text,
                gate_action)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'allow')""",
            (
                _now_iso(),
                TEST_CHAT_ID,
                str(message_id),
                TEST_USER_ID,
                "killtest",
                message_type,
                text,
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def _count_undelivered(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM inbound WHERE delivered = 0"
        ).fetchone()[0]
    finally:
        conn.close()


def _is_delivered(db_path: Path, row_id: int) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT delivered FROM inbound WHERE id = ?", (row_id,)
        ).fetchone()
        return row is not None and row[0] == 1
    finally:
        conn.close()


def _wait_delivered(db_path: Path, row_id: int, timeout: float) -> bool:
    """Poll until delivered=1 or timeout. server.ts writes the MCP notification
    to stdout BEFORE running `markDelivered.run(id)` — there's a short async
    window where the test reads the notification but the DB UPDATE hasn't
    committed yet. 2s is comfortably longer than that window under normal
    load; if it's exceeded something is genuinely broken."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_delivered(db_path, row_id):
            return True
        time.sleep(0.05)
    return False


def _wait_undelivered_count(db_path: Path, target: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _count_undelivered(db_path) == target:
            return True
        time.sleep(0.05)
    return False


class ServerProcess:
    """Wraps a spawned `bun server.ts` subprocess + an MCP notification reader.

    MCP stdio transport writes JSON-RPC 2.0 messages one per line to stdout
    (serializeMessage in @modelcontextprotocol/sdk appends '\\n'). A reader
    thread parses each line and pushes notifications onto a queue the test
    can drain.
    """

    def __init__(self, env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            ["bun", str(SERVER_TS)],
            cwd=str(REPO_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.notifications: queue.Queue[dict] = queue.Queue()
        self._stdout_reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._stdout_reader.start()
        # Drain stderr to prevent PIPE back-pressure from blocking the child.
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_reader.start()
        self.stderr_lines: list[str] = []

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            # MCP notifications look like {"jsonrpc":"2.0","method":"...","params":{...}}
            # (no "id" field). Responses to requests have "id". We only care
            # about notifications from server.ts.
            if isinstance(msg, dict) and "method" in msg and "id" not in msg:
                self.notifications.put(msg)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw in self.proc.stderr:
            try:
                self.stderr_lines.append(raw.decode("utf-8", errors="replace"))
            except Exception:
                pass

    def wait_for_notification(self, method: str, timeout: float) -> dict:
        """Block up to `timeout` seconds for the next notification with a
        matching method. Non-matching notifications are discarded."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                msg = self.notifications.get(timeout=max(0.1, remaining))
            except queue.Empty:
                continue
            if msg.get("method") == method:
                return msg
        stderr_tail = "".join(self.stderr_lines[-40:])
        raise AssertionError(
            f"timed out after {timeout}s waiting for {method}\n"
            f"--- server.ts stderr tail ---\n{stderr_tail}"
        )

    def terminate(self, timeout: float = SHUTDOWN_WAIT) -> int:
        if self.proc.poll() is not None:
            return self.proc.returncode
        try:
            self.proc.send_signal(signal.SIGTERM)
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait(timeout=2)
        finally:
            for f in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                try:
                    if f is not None:
                        f.close()
                except Exception:
                    pass


def _setup_state_dir(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """Create a self-contained state dir: .env with junk token, access.json
    with an allowlist, inbound.db with schema. Returns (base_dir, db_path, env)."""
    base_dir = tmp_path / f"killtest-{uuid.uuid4().hex[:8]}"
    state_dir = base_dir / "state"
    state_dir.mkdir(parents=True)
    base_dir.mkdir(exist_ok=True)
    (base_dir / "attachments").mkdir(exist_ok=True)

    # server.ts requires TELEGRAM_BOT_TOKEN or it exits with code 1. A junk
    # token is fine — the only code path that would validate it is grammY's
    # outbound API calls (e.g. setMessageReaction), and those errors are
    # swallowed in the catchup hot path (see server.ts:710).
    (state_dir / ".env").write_text("TELEGRAM_BOT_TOKEN=TEST:junktoken\n")

    # Allowlist the test user so gate_action='allow' is the canonical decision.
    # We write gate_action='allow' directly into rows (see _insert_row), so
    # this is belt-and-suspenders — server.ts doesn't re-gate rows from the DB.
    (state_dir / "access.json").write_text(
        json.dumps(
            {
                "dmPolicy": "allowlist",
                "allowFrom": [TEST_USER_ID],
                "groups": {},
                "pending": {},
            }
        )
        + "\n"
    )

    db_path = base_dir / "inbound.db"
    init_db_sync(db_path)

    env = {
        **os.environ,
        "TELEGRAM_STATE_DIR": str(state_dir),
        "LARRY_TELEGRAM_DIR": str(base_dir),
        # Strip any inherited bot token so the junk .env wins deterministically.
        # (server.ts keeps process.env if already set; see its env loader.)
    }
    env.pop("TELEGRAM_BOT_TOKEN", None)
    return base_dir, db_path, env


@pytest.mark.slow
def test_kill_restart_preserves_messages(tmp_path: Path) -> None:
    """Messages queued while server.ts is dead are delivered after restart.

    Phase 1: baseline — insert row A, confirm server.ts delivers it.
    Phase 2: kill server.ts, insert rows B/C/D, confirm they're undelivered.
    Phase 3: restart server.ts, confirm B/C/D flow through catch-up.
    """
    base_dir, db_path, env = _setup_state_dir(tmp_path)

    # --- Phase 1: baseline delivery ---
    server1 = ServerProcess(env)
    try:
        # Give server.ts a moment to open the DB + install the fallback
        # poller. Insert AFTER startup so the poller's immediate catch-up
        # pass sees the row on its first or second tick.
        time.sleep(STARTUP_WAIT)
        assert server1.proc.poll() is None, (
            f"server.ts exited during startup; stderr: "
            f"{''.join(server1.stderr_lines[-20:])}"
        )

        row_a = _insert_row(db_path, "message A (baseline)", message_id=1001)
        msg_a = server1.wait_for_notification(
            "notifications/claude/channel", timeout=DELIVERY_WAIT
        )
        assert msg_a["params"]["content"] == "message A (baseline)"
        assert msg_a["params"]["meta"]["chat_id"] == TEST_CHAT_ID
        # server.ts marks the row delivered AFTER the MCP notification write
        # resolves — small async gap. Poll briefly.
        assert _wait_delivered(db_path, row_a, timeout=2.0), (
            f"row A should be marked delivered=1 after MCP notification; "
            f"undelivered count={_count_undelivered(db_path)}"
        )
    finally:
        rc1 = server1.terminate()

    # --- Phase 2: dead-gap message injection ---
    # rc1 is from SIGTERM; server.ts calls process.exit(0) in shutdown(), so
    # 0 is the happy path. Non-0 means it died a different way — still OK as
    # long as it's dead, but log it.
    assert server1.proc.poll() is not None, "server1 should be dead"

    row_b = _insert_row(db_path, "message B (gap)", message_id=1002)
    row_c = _insert_row(db_path, "message C (gap)", message_id=1003)
    row_d = _insert_row(db_path, "message D (gap)", message_id=1004)

    # Sanity: A delivered, B/C/D not.
    assert _is_delivered(db_path, row_a)
    assert not _is_delivered(db_path, row_b)
    assert not _is_delivered(db_path, row_c)
    assert not _is_delivered(db_path, row_d)
    assert _count_undelivered(db_path) == 3

    # --- Phase 3: restart + catch-up ---
    server2 = ServerProcess(env)
    try:
        # Catch-up runs on the first fallback poll tick (2s) OR on the
        # immediate catch-up inside waitForSocketOrFallback. Either way
        # 12s is generous.
        delivered_texts: set[str] = set()
        deadline = time.monotonic() + DELIVERY_WAIT
        while time.monotonic() < deadline and len(delivered_texts) < 3:
            try:
                msg = server2.notifications.get(
                    timeout=max(0.1, deadline - time.monotonic())
                )
            except queue.Empty:
                continue
            if msg.get("method") != "notifications/claude/channel":
                continue
            delivered_texts.add(msg["params"]["content"])

        assert delivered_texts == {
            "message B (gap)",
            "message C (gap)",
            "message D (gap)",
        }, (
            f"expected B/C/D after catch-up, got {delivered_texts}; "
            f"stderr tail: {''.join(server2.stderr_lines[-40:])}"
        )

        # All four rows should now be delivered=1 (A from phase 1, B/C/D now).
        # Poll briefly to absorb the same notification-vs-UPDATE async gap
        # as in phase 1.
        assert _wait_undelivered_count(db_path, target=0, timeout=2.0), (
            f"expected 0 undelivered, got {_count_undelivered(db_path)}"
        )
        assert _is_delivered(db_path, row_a)
        assert _is_delivered(db_path, row_b)
        assert _is_delivered(db_path, row_c)
        assert _is_delivered(db_path, row_d)
    finally:
        server2.terminate()

    # Unused but kept to make the phase boundary explicit for future readers.
    del rc1, base_dir
