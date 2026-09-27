#!/usr/bin/env python3
"""Tests for the Telegram MCP watchdog."""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Import the watchdog module from the skill's tools/ directory.
# Layout: skills/harden-telegram/{tools/watchdog.py, server/tests/test_watchdog.py}
_TOOLS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tools")
)
sys.path.insert(0, _TOOLS_DIR)
import watchdog  # noqa: E402


class TestIsProcessAlive(unittest.TestCase):
    """Tests for the is_pid_alive function.

    These tests mock `os.kill` rather than hitting the real process table —
    signalling real PIDs from a test suite is unsafe on a shared box.
    """

    @patch("watchdog.os.kill")
    def test_own_process_is_alive(self, mock_kill):
        """A PID that os.kill(pid, 0) accepts should be reported alive."""
        mock_kill.return_value = None
        self.assertTrue(watchdog.is_pid_alive(12345))

    @patch("watchdog.os.kill", side_effect=ProcessLookupError)
    def test_nonexistent_pid_is_dead(self, mock_kill):
        """ProcessLookupError from os.kill means the PID is dead."""
        self.assertFalse(watchdog.is_pid_alive(4_000_000))

    @patch("watchdog.os.kill")
    def test_pid_zero_handling(self, mock_kill):
        """PID 0 should not raise; function must return a bool."""
        mock_kill.return_value = None
        result = watchdog.is_pid_alive(0)
        self.assertIsInstance(result, bool)

    @patch("watchdog.os.kill")
    def test_init_process(self, mock_kill):
        """PID 1 (init) — simulated alive via a non-raising os.kill."""
        mock_kill.return_value = None
        self.assertTrue(watchdog.is_pid_alive(1))


class TestSingleton(unittest.TestCase):
    """Tests for PID file singleton logic."""

    def setUp(self):
        """Use a temporary PID file to avoid interfering with real watchdog."""
        self.original_pid_file = watchdog.PID_FILE
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pid")
        self.tmp.close()
        watchdog.PID_FILE = self.tmp.name
        # Reset global lock fd
        watchdog._lock_fd = None

    def tearDown(self):
        """Restore original PID file path and clean up."""
        # Release lock if held
        if watchdog._lock_fd is not None:
            try:
                os.close(watchdog._lock_fd)
            except OSError:
                pass
            watchdog._lock_fd = None
        watchdog.PID_FILE = self.original_pid_file
        try:
            os.unlink(self.tmp.name)
        except FileNotFoundError:
            pass

    def test_acquire_when_no_pid_file(self):
        """Should acquire lock when no PID file exists."""
        os.unlink(self.tmp.name)  # Remove the temp file
        self.assertTrue(watchdog.acquire_singleton())
        # PID file should now contain our PID
        self.assertEqual(watchdog.read_pid_file(), os.getpid())

    def test_acquire_when_stale_pid_file(self):
        """Should acquire lock when PID file has no active flock."""
        # Write a PID that doesn't exist — flock is not held
        with open(self.tmp.name, "w") as f:
            f.write("4000000")
        self.assertTrue(watchdog.acquire_singleton())
        self.assertEqual(watchdog.read_pid_file(), os.getpid())

    def test_reject_when_locked(self):
        """Should reject lock when flock is already held."""
        import fcntl

        # Hold a flock on the PID file to simulate another watchdog
        held_fd = os.open(self.tmp.name, os.O_CREAT | os.O_WRONLY, 0o644)
        fcntl.flock(held_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(held_fd, b"1")

        self.assertFalse(watchdog.acquire_singleton())
        os.close(held_fd)

    def test_cleanup_removes_our_pid_file(self):
        """cleanup_pid_file should remove file if it contains our PID."""
        watchdog.write_pid_file()
        self.assertTrue(os.path.exists(self.tmp.name))
        watchdog.cleanup_pid_file()
        self.assertFalse(os.path.exists(self.tmp.name))

    def test_cleanup_skips_other_pid(self):
        """cleanup_pid_file should NOT remove file if it contains another PID."""
        with open(self.tmp.name, "w") as f:
            f.write("1")
        watchdog.cleanup_pid_file()
        # File should still exist
        self.assertTrue(os.path.exists(self.tmp.name))

    def test_read_pid_file_missing(self):
        """read_pid_file should return None for missing file."""
        os.unlink(self.tmp.name)
        self.assertIsNone(watchdog.read_pid_file())

    def test_read_pid_file_invalid(self):
        """read_pid_file should return None for non-numeric content."""
        with open(self.tmp.name, "w") as f:
            f.write("not-a-number")
        self.assertIsNone(watchdog.read_pid_file())


class TestTmuxSendKeys(unittest.TestCase):
    """Tests for tmux_send_keys with mocked subprocess."""

    @patch("watchdog.subprocess.run")
    def test_successful_send(self, mock_run):
        """Should return True on successful tmux command."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        result = watchdog.tmux_send_keys("%3", "Escape")
        self.assertTrue(result)
        mock_run.assert_called_once_with(
            ["tmux", "send-keys", "-t", "%3", "Escape"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    @patch("watchdog.subprocess.run")
    def test_failed_send(self, mock_run):
        """Should return False when tmux command fails."""
        mock_run.return_value = MagicMock(returncode=1, stderr="no such pane")
        result = watchdog.tmux_send_keys("%3", "Escape")
        self.assertFalse(result)

    @patch("watchdog.subprocess.run", side_effect=FileNotFoundError("tmux not found"))
    def test_tmux_not_installed(self, mock_run):
        """Should return False when tmux is not installed."""
        result = watchdog.tmux_send_keys("%3", "Escape")
        self.assertFalse(result)

    @patch(
        "watchdog.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=5),
    )
    def test_tmux_timeout(self, mock_run):
        """Should return False when tmux command times out."""
        result = watchdog.tmux_send_keys("%3", "Escape")
        self.assertFalse(result)


class TestRecoverySequence(unittest.TestCase):
    def test_idle_timeout_never_sends_keys(self):
        with (
            patch("watchdog.claude_for_pane", return_value=100),
            patch("watchdog.bridge_pids", return_value={200}),
            patch("watchdog.wait_for_idle_prompt", return_value=False),
            patch("watchdog.tmux_send_keys") as send,
        ):
            self.assertFalse(watchdog.do_recovery("%3"))
            send.assert_not_called()

    def test_send_failure_never_waits_for_replacement(self):
        with (
            patch("watchdog.claude_for_pane", return_value=100),
            patch("watchdog.bridge_pids", return_value={200}),
            patch("watchdog.wait_for_idle_prompt", return_value=True),
            patch("watchdog.tmux_send_keys", return_value=False),
            patch("watchdog.wait_for_new_bun") as wait,
        ):
            self.assertFalse(watchdog.do_recovery("%3"))
            wait.assert_not_called()


class TestWaitForNewBun(unittest.TestCase):
    def test_existing_bridge_is_not_a_replacement(self):
        with (
            patch("watchdog.time.sleep"),
            patch("watchdog.time.monotonic", side_effect=[0, 1, 20]),
            patch("watchdog.bridge_pids", return_value={200}),
        ):
            self.assertIsNone(watchdog.wait_for_new_bun(100, {200}, timeout=10))


class TestDaemonEntryValidation(unittest.TestCase):
    """Tests for the `daemon` subcommand's environment validation.

    The CLI is Typer (`_build_app`), so these need typer: run under
    `uv run --with typer` (the repo's `just fast-test` does).
    """

    def run_daemon(self, env):
        from typer.testing import CliRunner

        return CliRunner().invoke(watchdog._build_app(), ["daemon"], env=env)

    def test_missing_pids_exits(self):
        """Should exit with code 1 when PIDs are missing."""
        result = self.run_daemon(
            {
                "WATCHDOG_BUN_PID": "",
                "WATCHDOG_CLAUDE_PID": "",
                "WATCHDOG_TMUX_PANE": "",
            }
        )
        self.assertEqual(result.exit_code, 1)

    def test_missing_tmux_pane_exits_cleanly(self):
        """Should exit with code 0 when tmux pane is not set."""
        result = self.run_daemon(
            {
                "WATCHDOG_BUN_PID": "123",
                "WATCHDOG_CLAUDE_PID": "456",
                "WATCHDOG_TMUX_PANE": "",
            }
        )
        self.assertEqual(result.exit_code, 0)

    def test_invalid_pid_format_exits(self):
        """Should exit with code 1 for non-numeric PIDs."""
        result = self.run_daemon(
            {
                "WATCHDOG_BUN_PID": "abc",
                "WATCHDOG_CLAUDE_PID": "def",
                "WATCHDOG_TMUX_PANE": "%3",
            }
        )
        self.assertEqual(result.exit_code, 1)


if __name__ == "__main__":
    unittest.main()
