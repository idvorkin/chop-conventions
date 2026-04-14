#!/usr/bin/env python3
"""
Telegram MCP watchdog — auto-recover Claude Code's Telegram plugin.

When the bun process running server.ts dies, this watchdog detects the death
and sends /reload-plugins into Claude Code's tmux pane via tmux send-keys.
The reloaded plugin spawns a new bun process, which spawns a new watchdog,
so the old one exits (self-replacing chain).

Spawned by server.ts as a detached process. Accepts context via env vars:
  WATCHDOG_BUN_PID     - PID of the bun process to monitor
  WATCHDOG_CLAUDE_PID  - PID of the Claude Code process
  WATCHDOG_TMUX_PANE   - tmux pane identifier (e.g. %3)

Singleton: only one watchdog runs at a time (PID file at /tmp/telegram-watchdog.pid).
"""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PID_FILE = os.path.join(
    os.environ.get("HOME", "/tmp"), ".claude", "channels", "telegram", "watchdog.pid"
)
POLL_INTERVAL = 5  # seconds between liveness checks
SETTLE_DELAY = 2  # seconds to wait after bun death before recovery
NEW_BUN_TIMEOUT = 60  # seconds to wait for new bun to appear


def log(msg: str) -> None:
    """Log to stderr with timestamp and PID."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"[{ts}] watchdog[{os.getpid()}]: {msg}", file=sys.stderr, flush=True)


def is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — still alive
        return True
    except OSError:
        return False


def parse_proc_stat(data: str) -> tuple[str, int] | None:
    """Parse /proc/<pid>/stat contents into (comm, ppid).

    The comm field is wrapped in parens and can contain spaces or parens
    itself, so we anchor on the *last* ')' rather than splitting whitespace
    naively. Returns None on any malformed input.
    """
    rparen = data.rfind(")")
    lparen = data.find("(")
    if rparen == -1 or lparen == -1 or rparen < lparen:
        return None
    tail = data[rparen + 1 :].split()
    # tail[0] is state, tail[1] is ppid
    if len(tail) < 2:
        return None
    try:
        ppid = int(tail[1])
    except ValueError:
        return None
    comm = data[lparen + 1 : rparen]
    return (comm, ppid)


def _read_proc_stat(pid: int) -> tuple[str, int] | None:
    """Return (comm, ppid) for a PID, or None if the process is gone."""
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
    except (OSError, ValueError):
        return None
    return parse_proc_stat(data)


def find_ancestor_pane(
    pid: int,
    pane_pids: dict[int, str],
    *,
    stat_reader=_read_proc_stat,
    max_depth: int = 32,
) -> str | None:
    """Walk the ppid chain from `pid` upward looking for an entry in `pane_pids`.

    `pane_pids` maps a tmux pane's shell pid to its pane_id (e.g. %35).
    Returns the pane_id of the first ancestor whose pid appears in the map,
    or None if no ancestor in the chain is a known tmux pane shell.

    The walk includes `pid` itself (edge case: the caller *is* the pane's
    shell). Loop-guarded against pid-reuse cycles. `stat_reader` injected
    for tests.
    """
    if not pane_pids:
        return None
    seen: set[int] = set()
    current = pid
    for _ in range(max_depth):
        if current <= 1:
            return None
        if current in seen:
            return None
        seen.add(current)
        if current in pane_pids:
            return pane_pids[current]
        info = stat_reader(current)
        if info is None:
            return None
        _comm, ppid = info
        current = ppid
    return None


def list_tmux_pane_pids() -> dict[int, str]:
    """Return a {pane_pid: pane_id} map from `tmux list-panes -a`.

    Empty dict on any failure (no tmux, server not running, parse error).
    """
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_id} #{pane_pid}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}
    if result.returncode != 0:
        return {}
    out: dict[int, str] = {}
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            out[int(parts[1])] = parts[0]
        except ValueError:
            continue
    return out


def resolve_pane_for_pid(pid: int) -> str | None:
    """Find the tmux pane whose shell is an ancestor of `pid`.

    This is the correct way to ask "what pane am I running in?" when the
    tmux-*active* pane (what `tmux display-message -p '#{pane_id}'` returns)
    may belong to a different session. Walks ppid from `pid` upward, matches
    against the set of known tmux pane pids, returns the first hit.
    """
    pane_pids = list_tmux_pane_pids()
    return find_ancestor_pane(pid, pane_pids)


def write_pid_file() -> None:
    """Write our PID to the PID file."""
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def read_pid_file() -> int | None:
    """Read PID from the PID file, or None if missing/invalid."""
    try:
        with open(PID_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def cleanup_pid_file() -> None:
    """Remove PID file if it belongs to us."""
    try:
        existing = read_pid_file()
        if existing == os.getpid():
            os.unlink(PID_FILE)
            log("cleaned up PID file")
    except OSError:
        pass


_lock_fd: int | None = None


def acquire_singleton() -> bool:
    """Acquire singleton lock via PID file with flock to prevent races."""
    global _lock_fd
    try:
        _lock_fd = os.open(PID_FILE, os.O_CREAT | os.O_WRONLY, 0o644)
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(_lock_fd, 0)
        os.write(_lock_fd, str(os.getpid()).encode())
        os.fsync(_lock_fd)
        # Keep fd open — lock held for process lifetime
        return True
    except OSError:
        existing_pid = read_pid_file()
        log(f"another watchdog is running (PID {existing_pid}), exiting")
        if _lock_fd is not None:
            os.close(_lock_fd)
            _lock_fd = None
        return False


def tmux_send_keys(pane: str, *keys: str) -> bool:
    """Send keys to a tmux pane. Each arg is a separate send-keys argument.
    Use "Enter", "Escape" etc as separate args for special keys."""
    try:
        result = subprocess.run(
            ["tmux", "send-keys", "-t", pane, *keys],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            log(f"tmux send-keys failed: {result.stderr.strip()}")
            return False
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log(f"tmux send-keys error: {e}")
        return False


def wait_for_new_bun(timeout: int = NEW_BUN_TIMEOUT) -> bool:
    """Wait for a new bun server.ts process to appear."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["pgrep", "-f", "bun.*server.ts"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                log(
                    f"new bun process detected: PID {result.stdout.strip().splitlines()[0]}"
                )
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        time.sleep(2)
    return False


def wait_for_idle_prompt(tmux_pane: str, timeout: int = 30) -> bool:
    """Wait until Claude's TUI shows an idle prompt (empty ❯ line)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        capture = tmux_capture_pane(tmux_pane)
        lines = capture.rstrip().splitlines()
        # Look for an empty prompt: line with just "❯" or "❯ " (no user input)
        for line in lines[-5:]:
            stripped = line.strip()
            if stripped == "❯" or stripped == "❯ ":
                log("detected idle prompt")
                return True
        time.sleep(1)
    log(f"idle prompt not detected within {timeout}s")
    return False


def do_recovery(tmux_pane: str) -> bool:
    """Execute the recovery sequence via tmux send-keys."""
    log("starting recovery sequence")

    # Step 1: Let Claude notice the disconnect
    log("waiting 2s for Claude to notice disconnect")
    time.sleep(SETTLE_DELAY)

    # Step 2: Escape x2 to cancel generation + dismiss any prompts
    log("sending Escape x2 + Enter x3 to clear state")
    tmux_send_keys(tmux_pane, "Escape")
    time.sleep(0.3)
    tmux_send_keys(tmux_pane, "Escape")
    time.sleep(0.3)
    # Enter x3 to dismiss any queued input or confirmation prompts
    tmux_send_keys(tmux_pane, "Enter")
    time.sleep(0.3)
    tmux_send_keys(tmux_pane, "Enter")
    time.sleep(0.3)
    tmux_send_keys(tmux_pane, "Enter")
    time.sleep(0.5)

    # Step 3: Wait for Claude to return to idle prompt
    log("waiting for idle prompt...")
    if not wait_for_idle_prompt(tmux_pane, timeout=30):
        log("Claude not idle after 30s — trying /reload-plugins anyway")

    # Step 4: Clear any text in the input box, then send /reload-plugins
    tmux_send_keys(tmux_pane, "C-u")  # Ctrl-U clears the input line
    time.sleep(0.3)
    log("sending /reload-plugins")
    if not tmux_send_keys(tmux_pane, "/reload-plugins", "Enter"):
        log("failed to send /reload-plugins")
        return False

    # Step 5: Wait for reload and verify it ran
    log("waiting for reload to complete...")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        capture = tmux_capture_pane(tmux_pane)
        if "Reloaded:" in capture:
            log("confirmed: /reload-plugins executed successfully")
            return True
        time.sleep(1)

    log("could not confirm /reload-plugins ran — 'Reloaded:' not found in pane")
    return False


def tmux_active_pane() -> str:
    """Return the tmux-active pane ID via `display-message`.

    WARNING: this is the pane currently focused in the attached client,
    NOT necessarily the pane containing the caller. With multiple Claude
    sessions on one box, the active pane is often the wrong answer. Use
    `resolve_pane_for_pid(os.getpid())` first and fall back here only if
    the parent-chain walk cannot resolve a pane.
    """
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{pane_id}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def detect_tmux_pane() -> str:
    """Detect the tmux pane containing the caller.

    Prefers parent-chain resolution (correct across concurrent Claude
    sessions) and falls back to the tmux-active pane only if the walk
    fails. Logs which path was taken so failures surface.
    """
    resolved = resolve_pane_for_pid(os.getpid())
    if resolved:
        log(f"resolved pane {resolved} from parent chain (pid {os.getpid()})")
        return resolved
    fallback = tmux_active_pane()
    if fallback:
        log(
            f"could not resolve pane from parent chain, falling back to tmux active pane {fallback}"
        )
    return fallback


def find_claude_pid() -> int | None:
    """Find the Claude Code process PID by searching for the process."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "claude.*--dangerously-skip-permissions"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Return the first match
            return int(result.stdout.strip().splitlines()[0])
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return None


def find_bun_pid() -> int | None:
    """Find the bun server.ts process PID."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "bun.*server.ts"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().splitlines()[0])
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return None


def tmux_capture_pane(pane: str) -> str:
    """Capture the last 100 lines of a tmux pane (including scrollback)."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", pane, "-p", "-S", "-100"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def cmd_reload(tmux_pane: str | None = None, message: str | None = None) -> None:
    """Send /reload-plugins to Claude's tmux pane. Full live test."""
    pane = tmux_pane or detect_tmux_pane()
    if not pane:
        log("ERROR: not in a tmux session (no pane detected)")
        sys.exit(1)

    claude_pid = find_claude_pid()
    bun_pid = find_bun_pid()

    log("=== Watchdog Reload Test ===")
    log(f"tmux pane:  {pane}")
    log(f"claude PID: {claude_pid or 'NOT FOUND'}")
    log(f"bun PID:    {bun_pid or 'NOT FOUND'}")
    log(f"our PID:    {os.getpid()}")

    # Capture pane BEFORE
    log("--- BEFORE capture ---")
    before = tmux_capture_pane(pane)
    for line in before.rstrip().splitlines()[-10:]:
        log(f"  {line}")

    # Send the actual recovery sequence
    # Run the actual recovery sequence
    success = do_recovery(pane)

    # Capture pane AFTER
    log("--- AFTER capture ---")
    after = tmux_capture_pane(pane)
    for line in after.rstrip().splitlines()[-10:]:
        log(f"  {line}")

    if success:
        log("SUCCESS: recovery sequence completed")
        if message:
            log(f"sending follow-up message: {message}")
            time.sleep(2)  # let reload settle
            tmux_send_keys(pane, "C-u")
            time.sleep(0.2)
            tmux_send_keys(pane, message, "Enter")
    else:
        log("FAILED: recovery sequence did not confirm reload")

    # Write state file
    state_file = os.path.join(
        os.environ.get("HOME", "/tmp"),
        ".claude",
        "channels",
        "telegram",
        "watchdog_test.json",
    )
    state = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tmux_pane": pane,
        "claude_pid": claude_pid,
        "bun_pid": bun_pid,
        "watchdog_pid": os.getpid(),
        "command": "reload",
        "success": success,
        "before_last_line": before.rstrip().splitlines()[-1] if before.strip() else "",
        "after_last_line": after.rstrip().splitlines()[-1] if after.strip() else "",
    }
    try:
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)
        log(f"wrote state to {state_file}")
    except OSError as e:
        log(f"could not write state file: {e}")

    log("=== Reload Test Complete ===")


def _parse_cli():
    """Parse CLI args. Returns (command, pane, pid) or falls through to daemon mode."""
    # Usage:
    #   watchdog.py reload [--pane %17] [--pid 12345]
    #   watchdog.py                          (daemon mode, uses env vars)
    import argparse

    parser = argparse.ArgumentParser(description="Telegram MCP watchdog")
    sub = parser.add_subparsers(dest="command")

    reload_p = sub.add_parser(
        "reload", help="Send /reload-plugins to Claude's tmux pane"
    )
    reload_p.add_argument(
        "--pane", help="tmux pane ID (e.g. %%17). Auto-detects if omitted."
    )
    reload_p.add_argument(
        "--pid", type=int, help="PID of process in target pane. Used to find the pane."
    )
    reload_p.add_argument(
        "--message",
        "-m",
        help="Message to send to Claude after reload (e.g. 'Larry reloaded, I\\'m back')",
    )

    return parser.parse_args()


def pane_from_pid(pid: int) -> str | None:
    """Find the tmux pane containing a given PID.

    Walks the ppid chain from `pid` upward and matches each ancestor against
    the set of known tmux pane shell pids. Returns the matching pane_id, or
    None if no ancestor is a tmux pane shell (e.g., caller isn't running in
    tmux, or target pid is detached).
    """
    return resolve_pane_for_pid(pid)


def main() -> None:
    args = _parse_cli()

    if args.command == "reload":
        # Resolve pane
        pane = args.pane
        if not pane and args.pid:
            log(f"looking up tmux pane for PID {args.pid}...")
            pane = pane_from_pid(args.pid)
            if pane:
                log(f"found pane {pane} for PID {args.pid}")
            else:
                log(f"could not find tmux pane for PID {args.pid}")
                sys.exit(1)
        if not pane:
            pane = detect_tmux_pane()
        if not pane:
            log("ERROR: no pane specified and auto-detect failed. Use --pane or --pid.")
            sys.exit(1)
        cmd_reload(pane, message=args.message)
        return

    # --- Daemon mode: parse environment ---
    bun_pid_str = os.environ.get("WATCHDOG_BUN_PID", "")
    claude_pid_str = os.environ.get("WATCHDOG_CLAUDE_PID", "")
    tmux_pane = os.environ.get("WATCHDOG_TMUX_PANE", "")

    if not bun_pid_str or not claude_pid_str:
        log("WATCHDOG_BUN_PID and WATCHDOG_CLAUDE_PID must be set")
        sys.exit(1)

    if not tmux_pane:
        log("WATCHDOG_TMUX_PANE is empty/unset — not in a tmux session, exiting")
        sys.exit(0)

    try:
        bun_pid = int(bun_pid_str)
        claude_pid = int(claude_pid_str)
    except ValueError:
        log(f"invalid PID values: bun={bun_pid_str!r} claude={claude_pid_str!r}")
        sys.exit(1)

    log(f"starting: bun_pid={bun_pid} claude_pid={claude_pid} tmux_pane={tmux_pane}")

    # --- Singleton ---
    if not acquire_singleton():
        sys.exit(0)

    # --- Signal handling ---
    def handle_signal(signum: int, _frame: object) -> None:
        sig_name = signal.Signals(signum).name
        log(f"received {sig_name}, exiting")
        cleanup_pid_file()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # --- Main loop ---
    try:
        while True:
            time.sleep(POLL_INTERVAL)

            # Check Claude first — if Claude is gone, nothing to recover
            if not is_pid_alive(claude_pid):
                log("Claude process is dead, nothing to recover — exiting")
                break

            # Check bun
            if not is_pid_alive(bun_pid):
                log(f"bun process (PID {bun_pid}) is dead!")

                if do_recovery(tmux_pane):
                    log("recovery sequence sent, waiting for new bun process")
                    if wait_for_new_bun():
                        log(
                            "new bun process started — new watchdog will take over, exiting"
                        )
                    else:
                        log("no new bun process appeared within timeout")
                else:
                    log("recovery sequence failed")
                # Either way, exit — if recovery worked, new watchdog replaces us;
                # if it failed, we can't do more.
                break
    finally:
        cleanup_pid_file()

    log("watchdog exiting")


if __name__ == "__main__":
    main()
