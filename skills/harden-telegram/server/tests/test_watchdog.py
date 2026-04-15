#!/usr/bin/env python3
"""Tests for the Telegram MCP watchdog."""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Import the watchdog module from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watchdog


class TestIsProcessAlive(unittest.TestCase):
    """Tests for the is_pid_alive function."""

    def test_own_process_is_alive(self):
        """Our own PID should be alive."""
        self.assertTrue(watchdog.is_pid_alive(os.getpid()))

    def test_nonexistent_pid_is_dead(self):
        """A PID that doesn't exist should be dead."""
        # Use a very high PID unlikely to exist
        self.assertFalse(watchdog.is_pid_alive(4_000_000))

    def test_pid_zero_handling(self):
        """PID 0 should not raise."""
        # os.kill(0, 0) sends signal to entire process group — it will succeed.
        # The function should handle this without crashing.
        result = watchdog.is_pid_alive(0)
        self.assertIsInstance(result, bool)

    def test_init_process(self):
        """PID 1 (init) should be alive."""
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
    """Tests for the recovery sequence with mocked tmux."""

    @patch("watchdog.tmux_capture_pane", return_value="  ❯ \n")
    @patch("watchdog.time.sleep")
    @patch("watchdog.time.monotonic")
    @patch("watchdog.tmux_send_keys")
    def test_full_recovery_sequence(
        self, mock_send, mock_mono, mock_sleep, mock_capture
    ):
        """Should send Escape, C-u, /reload-plugins and verify Reloaded:."""
        mock_send.return_value = True
        # For wait_for_idle_prompt + reload confirmation loops
        mock_mono.side_effect = [0, 1, 0, 1]
        mock_capture.return_value = "  ❯ \nReloaded: 5 plugins\n"

        result = watchdog.do_recovery("%3")
        self.assertTrue(result)

        # Verify Escape, C-u, and /reload-plugins were sent
        send_calls = mock_send.call_args_list
        self.assertEqual(send_calls[0].args, ("%3", "Escape"))
        # C-u to clear input
        self.assertEqual(send_calls[1].args, ("%3", "C-u"))
        # /reload-plugins with Enter as separate arg
        self.assertEqual(send_calls[2].args, ("%3", "/reload-plugins", "Enter"))

    @patch("watchdog.time.sleep")
    @patch("watchdog.tmux_send_keys")
    def test_recovery_aborts_on_escape_failure(self, mock_send, mock_sleep):
        """Should abort if Escape send fails."""
        mock_send.return_value = False

        result = watchdog.do_recovery("%3")
        self.assertFalse(result)
        self.assertEqual(mock_send.call_count, 1)

    @patch("watchdog.tmux_capture_pane", return_value="  ❯ \n")
    @patch("watchdog.time.sleep")
    @patch("watchdog.time.monotonic")
    @patch("watchdog.tmux_send_keys")
    def test_recovery_aborts_on_reload_failure(
        self, mock_send, mock_mono, mock_sleep, mock_capture
    ):
        """Should abort if /reload-plugins send fails."""
        mock_mono.side_effect = [0, 1]  # for wait_for_idle_prompt
        mock_send.side_effect = [True, True, False]  # Escape ok, C-u ok, reload fails

        result = watchdog.do_recovery("%3")
        self.assertFalse(result)

    @patch("watchdog.tmux_capture_pane", return_value="  ❯ \nReloaded: 5 plugins\n")
    @patch("watchdog.time.sleep")
    @patch("watchdog.time.monotonic")
    @patch("watchdog.tmux_send_keys")
    def test_recovery_verifies_reloaded(
        self, mock_send, mock_mono, mock_sleep, mock_capture
    ):
        """Should verify 'Reloaded:' appears in tmux capture."""
        mock_send.return_value = True
        mock_mono.side_effect = [0, 1, 0, 1]

        result = watchdog.do_recovery("%3")
        self.assertTrue(result)


class TestWaitForNewBun(unittest.TestCase):
    """Tests for wait_for_new_bun with mocked subprocess."""

    @patch("watchdog.time.sleep")
    @patch("watchdog.time.monotonic")
    @patch("watchdog.subprocess.run")
    def test_bun_appears_immediately(self, mock_run, mock_mono, mock_sleep):
        """Should return True when new bun process is found."""
        mock_mono.side_effect = [0, 1]  # start, first check
        mock_run.return_value = MagicMock(returncode=0, stdout="12345\n")

        result = watchdog.wait_for_new_bun(timeout=10)
        self.assertTrue(result)

    @patch("watchdog.time.sleep")
    @patch("watchdog.time.monotonic")
    @patch("watchdog.subprocess.run")
    def test_bun_never_appears(self, mock_run, mock_mono, mock_sleep):
        """Should return False after timeout when no bun appears."""
        # monotonic: start=0, then always past deadline
        mock_mono.side_effect = [0, 100]
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = watchdog.wait_for_new_bun(timeout=10)
        self.assertFalse(result)


class TestMainEntryValidation(unittest.TestCase):
    """Tests for main() argument validation."""

    @patch.dict(
        os.environ,
        {"WATCHDOG_BUN_PID": "", "WATCHDOG_CLAUDE_PID": "", "WATCHDOG_TMUX_PANE": ""},
        clear=False,
    )
    def test_missing_pids_exits(self):
        """Should exit with code 1 when PIDs are missing."""
        with self.assertRaises(SystemExit) as ctx:
            watchdog.main()
        self.assertEqual(ctx.exception.code, 1)

    @patch.dict(
        os.environ,
        {
            "WATCHDOG_BUN_PID": "123",
            "WATCHDOG_CLAUDE_PID": "456",
            "WATCHDOG_TMUX_PANE": "",
        },
        clear=False,
    )
    def test_missing_tmux_pane_exits_cleanly(self):
        """Should exit with code 0 when tmux pane is not set."""
        with self.assertRaises(SystemExit) as ctx:
            watchdog.main()
        self.assertEqual(ctx.exception.code, 0)

    @patch.dict(
        os.environ,
        {
            "WATCHDOG_BUN_PID": "abc",
            "WATCHDOG_CLAUDE_PID": "def",
            "WATCHDOG_TMUX_PANE": "%3",
        },
        clear=False,
    )
    def test_invalid_pid_format_exits(self):
        """Should exit with code 1 for non-numeric PIDs."""
        with self.assertRaises(SystemExit) as ctx:
            watchdog.main()
        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
