#!/usr/bin/env python3
"""
Telegram MCP diagnostic tool — gathers all logs and system state at once.

Usage:
    python3 telegram_debug.py                # Full diagnostic report
    python3 telegram_debug.py --json         # JSON output (pipe to jq)
    python3 telegram_debug.py --tail 50      # More log lines (default 20)
    python3 telegram_debug.py --json --tail 100 | jq '.server_log[-5:]'
    python3 telegram_debug.py --doctor       # Validate two-process chain end-to-end
"""

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get("HOME", "/tmp")) / ".claude" / "channels" / "telegram"
PLUGIN_DIR = (
    Path(os.environ.get("HOME", "/tmp"))
    / ".claude"
    / "plugins"
    / "cache"
    / "claude-plugins-official"
    / "telegram"
)
LOG_DB = Path(os.environ.get("HOME", "/tmp")) / ".claude" / "telegram_log.db"


def run(cmd: list[str], timeout: int = 5) -> str:
    """Run a command and return stdout, or error string."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return f"ERROR: {e}"


TELEGRAM_PLUGIN_MARKER = "claude-plugins-official/telegram"


def check_bun_processes() -> list[dict]:
    """Find all bun server.ts processes, distinguishing Telegram bot from other bun."""
    output = run(["bash", "-c", r"\ps -eo pid,ppid,tty,args"])
    if output.startswith("ERROR"):
        output = run(["/usr/bin/ps", "-eo", "pid,ppid,tty,args"])
    processes = []
    for line in output.splitlines():
        if "bun" in line and "server.ts" in line and "grep" not in line:
            parts = line.split(None, 3)  # pid, ppid, tty, cmd
            if len(parts) < 4:
                continue
            pid = parts[0].strip()
            ppid = parts[1].strip()
            tty = parts[2].strip()
            cmd = parts[3].strip()
            # Check /proc/<pid>/cwd to confirm this is the telegram plugin
            cwd = _proc_cwd(pid)
            is_telegram = TELEGRAM_PLUGIN_MARKER in (cwd or "")
            processes.append(
                {
                    "pid": pid,
                    "ppid": ppid,
                    "tty": tty,
                    "cmd": cmd,
                    "cwd": cwd,
                    "is_telegram": is_telegram,
                }
            )
    return processes


def _proc_cwd(pid: str) -> str | None:
    """Read /proc/<pid>/cwd symlink to get the process working directory."""
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except (OSError, ValueError):
        return None


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
    comm = data[lparen + 1 : rparen]
    tail = data[rparen + 1 :].split()
    # tail[0] is state, tail[1] is ppid
    if len(tail) < 2:
        return None
    try:
        ppid = int(tail[1])
    except ValueError:
        return None
    return (comm, ppid)


def _read_proc_stat(pid: int) -> tuple[str, int] | None:
    """Return (comm, ppid) for a PID, or None if the process is gone."""
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
    except (OSError, ValueError):
        return None
    return parse_proc_stat(data)


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness probe that works across UIDs."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Signal denied → the process exists but isn't ours. Still alive.
        return True
    except OSError:
        return False


def _find_owning_claude(
    pid: int,
    *,
    stat_reader=_read_proc_stat,
) -> int | None:
    """Walk the ppid chain from `pid` until we hit a process whose comm starts with `claude`.

    Returns the PID of the nearest claude ancestor, or None if the chain
    reaches init without finding one (or breaks because a process went
    away mid-walk). `stat_reader` is injected for tests.

    Matches `claude`, `claude-code`, `claude-1m`, etc. Linux truncates comm
    to TASK_COMM_LEN-1 = 15 chars, so any launcher/shim that starts with
    "claude" fits and still matches here.
    """
    seen: set[int] = set()
    current = pid
    while current and current > 1:
        # Guard against self-loops and pid-reuse cycles.
        if current in seen:
            return None
        seen.add(current)
        info = stat_reader(current)
        if info is None:
            return None
        comm, ppid = info
        if comm.startswith("claude"):
            return current
        current = ppid
    return None


def _read_proc_cmdline(pid: int) -> list[str] | None:
    """Return argv of a PID from /proc/<pid>/cmdline, or None if unreadable.

    The file is null-separated; a trailing null terminates the final arg.
    """
    try:
        data = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError):
        return None
    if not data:
        return None
    parts = data.rstrip(b"\x00").split(b"\x00")
    return [p.decode("utf-8", errors="replace") for p in parts]


def session_subscribed_to_telegram(argv: list[str]) -> bool:
    """Return True iff argv has a `--channels` value mentioning telegram.

    Accepts both `--channels X` and `--channels=X`. A single `--channels`
    flag may carry a comma-separated list (`a,b,c`) — we split and check
    each entry. Values are matched case-insensitively against the literal
    substring 'telegram' to stay tolerant of plugin name variations like
    `plugin:telegram@claude-plugins-official`.
    """
    i = 0
    while i < len(argv):
        arg = argv[i]
        value: str | None = None
        if arg == "--channels":
            if i + 1 < len(argv):
                value = argv[i + 1]
                i += 2
            else:
                i += 1
                continue
        elif arg.startswith("--channels="):
            value = arg[len("--channels=") :]
            i += 1
        else:
            i += 1
            continue
        if value is None:
            continue
        for entry in value.split(","):
            if "telegram" in entry.lower():
                return True
    return False


def classify_bridges(
    pids: list[int],
    our_claude_pid: int | None,
    *,
    stat_reader=_read_proc_stat,
    is_alive=_pid_alive,
) -> list[dict]:
    """Classify each bun server.ts PID by which Claude session owns it.

    Classifications:
      - "ours":          owning claude == our_claude_pid
      - "other-session": owning claude is alive but not ours
      - "orphaned":      no owning claude found, or it's dead

    Pure function — all I/O is injected via `stat_reader` / `is_alive`
    so tests can drive it without touching /proc.
    """
    bridges: list[dict] = []
    for pid in pids:
        owning = _find_owning_claude(pid, stat_reader=stat_reader)
        if owning is None:
            classification = "orphaned"
        elif owning == our_claude_pid:
            classification = "ours"
        elif is_alive(owning):
            classification = "other-session"
        else:
            classification = "orphaned"
        bridges.append(
            {
                "pid": pid,
                "owning_claude": owning,
                "classification": classification,
            }
        )
    return bridges


def check_claude_sessions() -> list[dict]:
    """Find all Claude Code sessions."""
    output = run(["bash", "-c", r"\ps -ef | grep -v grep | grep claude.*dangerously"])
    sessions = []
    for line in output.splitlines():
        if not line or "ERROR" in line:
            continue
        parts = line.split()
        sessions.append(
            {
                "pid": parts[1] if len(parts) > 1 else "?",
                "tty": parts[5] if len(parts) > 5 else "?",
                "has_channels": "--channels" in line,
                "cmd": " ".join(parts[7:]) if len(parts) > 7 else line,
            }
        )
    return sessions


def check_pid_file(_name: str, path: Path) -> dict:
    """Check a PID file and whether the process is alive."""
    result = {"file": str(path), "exists": path.exists()}
    if path.exists():
        try:
            pid = int(path.read_text().strip())
            result["pid"] = pid
            try:
                os.kill(pid, 0)
                result["alive"] = True
            except ProcessLookupError:
                result["alive"] = False
            except PermissionError:
                result["alive"] = True
        except (ValueError, OSError):
            result["pid"] = None
            result["alive"] = False
    return result


def check_server_log(n: int = 20) -> list[str]:
    """Read last N lines of server.log."""
    log_file = STATE_DIR / "server.log"
    if not log_file.exists():
        return ["(no server.log)"]
    try:
        lines = log_file.read_text().strip().splitlines()
        return lines[-n:]
    except OSError:
        return ["(error reading server.log)"]


def check_inbound_log(n: int = 20) -> list[dict]:
    """Read last N inbound.jsonl entries."""
    log_file = STATE_DIR / "inbound.jsonl"
    if not log_file.exists():
        return []
    try:
        lines = log_file.read_text().strip().splitlines()
        entries = []
        for line in lines[-n:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                entries.append({"raw": line})
        return entries
    except OSError:
        return []


def check_telegram_db(n: int = 10) -> dict:
    """Check telegram_log.db for recent messages."""
    if not LOG_DB.exists():
        return {"exists": False}
    try:
        conn = sqlite3.connect(str(LOG_DB))
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM messages")
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM messages WHERE timestamp >= datetime('now', '-24 hours')"
        )
        recent = cur.fetchone()[0]
        cur.execute(
            "SELECT timestamp, direction, tool_name, chat_id, substr(text,1,120) "
            "FROM messages ORDER BY timestamp DESC LIMIT ?",
            (n,),
        )
        last_n = [
            {"ts": r[0], "dir": r[1], "tool": r[2], "chat_id": r[3], "text": r[4]}
            for r in cur.fetchall()
        ]
        conn.close()
        return {
            "exists": True,
            "total": total,
            "recent_24h": recent,
            "last_messages": last_n,
        }
    except Exception as e:
        return {"exists": True, "error": str(e)}


def check_access_config() -> dict:
    """Read access.json summary."""
    access_file = STATE_DIR / "access.json"
    if not access_file.exists():
        return {"exists": False}
    try:
        data = json.loads(access_file.read_text())
        return {
            "exists": True,
            "dmPolicy": data.get("dmPolicy", "?"),
            "allowFrom_count": len(data.get("allowFrom", [])),
            "groups_count": len(data.get("groups", {})),
            "pending_count": len(data.get("pending", {})),
        }
    except (json.JSONDecodeError, OSError) as e:
        return {"exists": True, "error": str(e)}


# Hardcoded fallback for the canonical server.ts source directory.
# Matches Igor's box layout: the two-process telegram fork lives under
# ~/gits/igor2/telegram-server. Anyone else either sets TELEGRAM_SOURCE_DIR
# explicitly or degrades to the legacy "skipped" note.
_DEFAULT_SOURCE_DIR = Path.home() / "gits" / "igor2" / "telegram-server"


def _resolve_source_dir() -> tuple[Path | None, str | None]:
    """Resolve the canonical server.ts source directory and its lookup source.

    Lookup order (first hit wins):
      1. `TELEGRAM_SOURCE_DIR` env var — explicit override.
      2. `~/gits/igor2/telegram-server` — hardcoded default for Igor's layout.

    Returns (path, source) where source is "env", "default", or None. Path is
    only returned when the resolved dir exists AND contains a `server.ts`;
    otherwise returns (None, "env"|"default"|None) so the caller can tell the
    difference between "not configured" and "configured but missing".

    The "env" case short-circuits even if the dir is missing — callers should
    check `path is None` to decide whether to skip the drift check vs emit a
    louder warning about an explicitly-set-but-missing source. See
    `_doctor_check_deploy` for the degrade-gracefully path.
    """
    env = os.environ.get("TELEGRAM_SOURCE_DIR")
    if env:
        p = Path(env).expanduser()
        if (p / "server.ts").exists():
            return p, "env"
        # Explicitly set but missing — return None path with "env" source so
        # the doctor can emit the legacy skipped note instead of crashing.
        return None, "env"
    if (_DEFAULT_SOURCE_DIR / "server.ts").exists():
        return _DEFAULT_SOURCE_DIR, "default"
    return None, None


def _source_dir() -> Path | None:
    """Canonical server.ts source directory (for hash-drift check).

    Lookup order:
      1. `TELEGRAM_SOURCE_DIR` env var (explicit override)
      2. `~/gits/igor2/telegram-server` (hardcoded default for Igor's box)

    Returns None if neither resolves to a dir containing `server.ts` — the
    doctor still runs, it just can't tell you whether the plugin-cache copy
    matches an upstream source. Callers that need the lookup-source metadata
    (env vs default) should call `_resolve_source_dir()` directly.
    """
    path, _ = _resolve_source_dir()
    return path


def _source_server_ts() -> Path | None:
    d = _source_dir()
    return d / "server.ts" if d else None


def _file_hash(path: Path) -> str | None:
    """SHA-256 of a file, or None if unreadable."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def check_plugin_deploy() -> dict:
    """Check if our custom server.ts is deployed and matches source.

    Emits both the legacy fields (installed/version/deploy_path/WARNING_DRIFT/
    etc. — preserved for any existing consumers) and the structured `deploy`
    block that --json mode exposes for watchdog parsing:

        {
            "plugin_cache_path": "...",
            "plugin_cache_sha256": "...",
            "source_path": "..." | None,
            "source_sha256": "..." | None,
            "source_source": "env" | "default" | None,
            "drift_detected": True | False,
            "severity": "ok" | "error" | "skipped"
        }

    `severity` is the single field automation should key on:
      - "ok": source == plugin, everything matches
      - "error": drift detected OR plugin missing — watchdog should alarm
      - "skipped": no source configured or no plugin cache to compare against
    """
    # Find the version dir
    if not PLUGIN_DIR.exists():
        return {
            "installed": False,
            "deploy": {
                "plugin_cache_path": None,
                "plugin_cache_sha256": None,
                "source_path": None,
                "source_sha256": None,
                "source_source": None,
                "drift_detected": False,
                "severity": "skipped",
            },
        }
    versions = sorted(
        [d for d in PLUGIN_DIR.iterdir() if d.is_dir() and d.name[0].isdigit()],
        reverse=True,
    )
    if not versions:
        return {
            "installed": False,
            "deploy": {
                "plugin_cache_path": None,
                "plugin_cache_sha256": None,
                "source_path": None,
                "source_sha256": None,
                "source_source": None,
                "drift_detected": False,
                "severity": "skipped",
            },
        }
    version_dir = versions[0]  # newest version
    deployed_file = version_dir / "server.ts"
    source_path, source_source = _resolve_source_dir()
    source_ts = (source_path / "server.ts") if source_path else None
    result: dict = {
        "installed": True,
        "version": version_dir.name,
        "deploy_path": str(deployed_file),
        "source_path": str(source_ts) if source_ts else None,
        "source_configured": source_ts is not None,
        "source_source": source_source,
        "server_ts_exists": deployed_file.exists(),
    }
    source_hash: str | None = None
    deploy_hash: str | None = None
    drift_detected = False
    if deployed_file.exists():
        result["is_symlink"] = deployed_file.is_symlink()
        if deployed_file.is_symlink():
            result["symlink_target"] = str(deployed_file.resolve())
            result["WARNING"] = "Symlinks break bun module resolution! Use cp instead."
        # Check feature markers
        try:
            content = deployed_file.read_text()
            result["has_resilience"] = "logInbound" in content
            result["has_heartbeat"] = "heartbeat" in content
        except OSError:
            pass
        deploy_hash = _file_hash(deployed_file)
        # Compare source vs deployed — only if a source dir is configured.
        if source_ts is not None:
            source_hash = _file_hash(source_ts)
            result["source_hash"] = source_hash
            result["deploy_hash"] = deploy_hash
            if source_hash and deploy_hash:
                result["in_sync"] = source_hash == deploy_hash
                drift_detected = source_hash != deploy_hash
                if drift_detected:
                    result["WARNING_DRIFT"] = (
                        "Source and deployed server.ts differ! "
                        f"Run: cp {source_ts} {deployed_file}"
                    )
            result["source_exists"] = source_ts.exists()

    # Derive severity for the structured deploy block.
    if deploy_hash is None:
        severity = "skipped"  # plugin cache unreadable or missing
    elif source_hash is None:
        severity = "skipped"  # no source to compare against
    elif drift_detected:
        severity = "error"
    else:
        severity = "ok"

    result["deploy"] = {
        "plugin_cache_path": str(deployed_file),
        "plugin_cache_sha256": deploy_hash,
        "source_path": str(source_ts) if source_ts else None,
        "source_sha256": source_hash,
        "source_source": source_source,
        "drift_detected": drift_detected,
        "severity": severity,
    }
    return result


def check_watchdog() -> dict:
    """Check watchdog state."""
    pid_file = STATE_DIR / "watchdog.pid"
    test_file = STATE_DIR / "watchdog_test.json"
    result = {"pid": check_pid_file("watchdog", pid_file)}
    if test_file.exists():
        try:
            result["last_test"] = json.loads(test_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return result


def full_diagnostic(tail: int = 20) -> dict:
    """Run all diagnostic checks and return structured results."""
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "bun_processes": check_bun_processes(),
        "claude_sessions": check_claude_sessions(),
        "bot_pid": check_pid_file("bot", STATE_DIR / "bot.pid"),
        "server_log": check_server_log(n=tail),
        "inbound_log": check_inbound_log(n=tail),
        "telegram_db": check_telegram_db(n=tail),
        "access_config": check_access_config(),
        "plugin_deploy": check_plugin_deploy(),
        "watchdog": check_watchdog(),
    }


def print_report(diag: dict) -> None:
    """Print a human-readable diagnostic report."""
    print(f"=== Telegram MCP Diagnostic — {diag['timestamp']} ===\n")

    # Bun processes
    buns = diag["bun_processes"]
    tg_buns = [b for b in buns if b.get("is_telegram")]
    other_buns = [b for b in buns if not b.get("is_telegram")]
    print(
        f"BUN server.ts PROCESSES: {len(buns)} total ({len(tg_buns)} telegram, {len(other_buns)} other)"
    )
    if buns:
        for b in buns:
            tag = " [TELEGRAM]" if b.get("is_telegram") else ""
            print(f"  PID {b['pid']} (ppid {b['ppid']}, {b['tty']}){tag}")
            print(f"    cmd: {b['cmd']}")
            print(f"    cwd: {b.get('cwd', '?')}")
    else:
        print("  NONE RUNNING")

    # Claude sessions
    sessions = diag["claude_sessions"]
    print(f"\nCLAUDE SESSIONS: {len(sessions)}")
    for s in sessions:
        channels = " [+channels]" if s["has_channels"] else ""
        print(f"  PID {s['pid']} ({s['tty']}){channels}")

    # Bot PID
    bp = diag["bot_pid"]
    status = "ALIVE" if bp.get("alive") else "DEAD" if bp.get("exists") else "NO FILE"
    print(f"\nBOT PID: {bp.get('pid', 'n/a')} — {status}")

    # Plugin deploy
    pd = diag["plugin_deploy"]
    print("\nPLUGIN DEPLOY:")
    if pd.get("installed"):
        print(f"  Version: {pd.get('version')}")
        symlink = " ⚠️  SYMLINK (BROKEN!)" if pd.get("is_symlink") else " (real file)"
        print(f"  server.ts: {pd.get('server_ts_exists')}{symlink}")
        print(
            f"  Resilience: {pd.get('has_resilience', False)}  Heartbeat: {pd.get('has_heartbeat', False)}"
        )
        if pd.get("in_sync") is True:
            print(f"  Source ↔ Deploy: ✅ IN SYNC ({pd.get('deploy_hash')})")
        elif pd.get("in_sync") is False:
            print("  Source ↔ Deploy: ❌ DRIFTED")
            print(f"    source: {pd.get('source_hash')} ({pd.get('source_path')})")
            print(f"    deploy: {pd.get('deploy_hash')} ({pd.get('deploy_path')})")
            print(f"    Fix: cp {pd.get('source_path')} {pd.get('deploy_path')}")
        elif not pd.get("source_exists", True):
            print(f"  Source: ⚠️  {pd.get('source_path')} not found — can't verify sync")
    else:
        print("  NOT INSTALLED")

    # Server log
    print("\nSERVER LOG (last 10):")
    for line in diag["server_log"][-10:]:
        print(f"  {line}")

    # Inbound log
    inbound = diag["inbound_log"]
    print(f"\nINBOUND LOG: {len(inbound)} recent entries")
    for entry in inbound[-3:]:
        if "ts" in entry:
            print(
                f"  [{entry['ts']}] {entry.get('user', '?')}: {entry.get('text_preview', '')[:60]}"
            )

    # Telegram DB
    db = diag["telegram_db"]
    print("\nTELEGRAM DB:")
    if db.get("exists"):
        print(f"  Total messages: {db.get('total', '?')}")
        print(f"  Last 24h: {db.get('recent_24h', '?')}")
        for m in db.get("last_messages", []):
            text = (m.get("text") or "")[:70]
            print(f"  [{m['ts'][:19]}] {m['dir']:8s} {text}")
    else:
        print("  NOT FOUND")

    # Access config
    ac = diag["access_config"]
    print("\nACCESS CONFIG:")
    if ac.get("exists"):
        print(
            f"  dmPolicy: {ac.get('dmPolicy')}  allowFrom: {ac.get('allowFrom_count')}  groups: {ac.get('groups_count')}"
        )
    else:
        print("  NOT FOUND")

    # Watchdog
    wd = diag["watchdog"]
    wp = wd["pid"]
    ws = "ALIVE" if wp.get("alive") else "DEAD" if wp.get("exists") else "NO FILE"
    print(f"\nWATCHDOG: {wp.get('pid', 'n/a')} — {ws}")
    if "last_test" in wd:
        lt = wd["last_test"]
        print(f"  Last test: {lt.get('ts', '?')} — pane {lt.get('tmux_pane', '?')}")

    # Verdict
    print(f"\n{'=' * 60}")
    issues = []
    if len(tg_buns) == 0:
        issues.append("❌ No Telegram bun process running — bot is dead")
    if len(tg_buns) > 1:
        issues.append(
            f"⚠️  {len(tg_buns)} Telegram bun processes — zombie stealing updates"
        )
    if other_buns:
        pids = ", ".join(b["pid"] for b in other_buns)
        issues.append(
            f"ℹ️  {len(other_buns)} non-Telegram bun server.ts ({pids}) — ignored"
        )
    if pd.get("is_symlink"):
        issues.append("❌ server.ts is a symlink — bun can't resolve modules. Use cp!")
    if not pd.get("has_resilience"):
        issues.append("⚠️  Resilience features not deployed")
    if pd.get("in_sync") is False:
        issues.append("❌ Source/deploy server.ts DRIFTED — hot-patch is stale")
    sessions_with_channels = [s for s in sessions if s["has_channels"]]
    if len(sessions_with_channels) == 0:
        issues.append("⚠️  No Claude session launched with --channels flag")
    if len(sessions_with_channels) > 1:
        issues.append(
            f"⚠️  {len(sessions_with_channels)} sessions with --channels — will fight over bot token"
        )

    if issues:
        print("ISSUES FOUND:")
        for i in issues:
            print(f"  {i}")
    else:
        print("✅ All checks passed")


# ---------------------------------------------------------------------------
# Doctor mode — validate the full two-process chain end-to-end.
#
# Two-process layout (spec §telegram_debug.py doctor mode):
#   telegram_bot.py  (persistent)  → writes inbound.db + binds bot.sock
#   server.ts        (ephemeral)   → reads inbound.db, subscribes to bot.sock,
#                                    delivers to Claude over MCP
# Doctor prints a ✅/❌/⚠️ line per check and exits 1 on any failure.
# ---------------------------------------------------------------------------


OK = "✅"
BAD = "❌"
WARN = "⚠️ "


def _base_dir() -> Path:
    return Path(
        os.environ.get("LARRY_TELEGRAM_DIR", str(Path.home() / "larry-telegram"))
    ).expanduser()


def _fmt_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    if seconds < 86400:
        hours, rem = divmod(seconds, 3600)
        return f"{hours}h {rem // 60}m"
    days, rem = divmod(seconds, 86400)
    return f"{days}d {rem // 3600}h"


class DoctorReport:
    """Accumulator for doctor checks. Tracks pass/fail across sections."""

    def __init__(self) -> None:
        self.failures = 0
        self.lines: list[str] = []

    def section(self, name: str) -> None:
        self.lines.append(f"\n{name}:")

    def ok(self, msg: str) -> None:
        self.lines.append(f"  {OK} {msg}")

    def warn(self, msg: str) -> None:
        self.lines.append(f"  {WARN} {msg}")

    def fail(self, msg: str) -> None:
        self.lines.append(f"  {BAD} {msg}")
        self.failures += 1

    def note(self, msg: str) -> None:
        """Informational line with no pass/fail semantics — for log tails,
        secondary file paths, anything that adds context but isn't a check."""
        self.lines.append(f"  · {msg}")

    def render(self) -> str:
        out = ["=== Telegram Doctor ==="]
        out.extend(self.lines)
        out.append("")
        out.append("=" * 60)
        if self.failures == 0:
            out.append(f"{OK} All checks passed")
        else:
            out.append(f"{BAD} {self.failures} checks failed")
        return "\n".join(out)


def _doctor_check_bot_pid(report: DoctorReport, base: Path) -> None:
    report.section("TELEGRAM_BOT.PY")
    pid_file = base / "bot.pid"
    if not pid_file.exists():
        report.fail(f"bot.pid missing at {pid_file}")
        return
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError) as e:
        report.fail(f"bot.pid unreadable: {e}")
        return
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        report.fail(f"bot.pid says {pid} but process is dead")
        return
    except PermissionError:
        pass  # alive, owned by another user (shouldn't happen but OK)
    try:
        uptime = _fmt_age(time.time() - pid_file.stat().st_mtime)
    except OSError:
        uptime = "unknown"
    report.ok(f"PID alive: {pid} (uptime {uptime})")


def _doctor_check_socket(report: DoctorReport, base: Path) -> None:
    report.section("UNIX SOCKET")
    sock_path = base / "bot.sock"
    if not sock_path.exists():
        report.fail(f"bot.sock missing at {sock_path}")
        return
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect(str(sock_path))
    except (OSError, socket.timeout) as e:
        report.fail(f"bot.sock at {sock_path} not accepting connections: {e}")
        return
    finally:
        try:
            s.close()
        except OSError:
            pass
    report.ok(f"bot.sock: {sock_path} accepting connections")


def _doctor_check_inbound_db(report: DoctorReport, base: Path) -> None:
    report.section("INBOUND.DB")
    db_path = base / "inbound.db"
    if not db_path.exists():
        report.fail(f"inbound.db missing at {db_path}")
        return
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA busy_timeout=2000")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM inbound")
        total = int(cur.fetchone()[0])
        cur.execute(
            "SELECT COUNT(*) FROM inbound WHERE delivered = 0 AND gate_action = 'allow'"
        )
        undelivered = int(cur.fetchone()[0])
        cur.execute("SELECT MAX(ts) FROM inbound")
        row = cur.fetchone()
        last_ts = row[0] if row else None
        conn.close()
    except sqlite3.Error as e:
        report.fail(f"inbound.db query failed: {e}")
        return

    last_write_age = "never"
    last_write_secs: float | None = None
    if last_ts:
        # ts is ISO-8601 UTC (see telegram_bot.py). Be tolerant of fractional seconds.
        parsed: float | None = None
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                import datetime as _dt

                parsed = (
                    _dt.datetime.strptime(last_ts, fmt)
                    .replace(tzinfo=_dt.timezone.utc)
                    .timestamp()
                )
                break
            except ValueError:
                continue
        if parsed is None:
            try:
                import datetime as _dt

                parsed = _dt.datetime.fromisoformat(
                    last_ts.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                parsed = None
        if parsed is not None:
            last_write_secs = time.time() - parsed
            last_write_age = f"{_fmt_age(last_write_secs)} ago"

    summary = (
        f"total messages: {total:,} / undelivered: {undelivered} / "
        f"last write: {last_write_age}"
    )

    if undelivered > 100:
        report.fail(
            f"{summary} — backlog >100 means server.ts isn't draining the queue"
        )
    elif undelivered > 10:
        report.warn(f"{summary} — backlog >10 rows, server.ts may be slow")
    elif last_write_secs is not None and last_write_secs > 3600:
        report.warn(f"{summary} — no writes in >1h (bot may be idle or stuck)")
    else:
        report.ok(summary)


def _doctor_check_server_ts(report: DoctorReport) -> None:
    report.section("SERVER.TS")
    try:
        r = subprocess.run(
            ["pgrep", "-f", "bun.*server.ts"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        report.fail(f"pgrep failed: {e}")
        return

    # Filter to the Telegram plugin's bridges by /proc/<pid>/cwd.
    # Other bun server.ts processes (unrelated projects) don't count here.
    candidate_pids = [int(p) for p in r.stdout.strip().split() if p.isdigit()]
    tg_pids = [
        pid
        for pid in candidate_pids
        if TELEGRAM_PLUGIN_MARKER in (_proc_cwd(str(pid)) or "")
    ]

    our_claude = _find_owning_claude(os.getpid())
    bridges = classify_bridges(tg_pids, our_claude)

    ours = [b for b in bridges if b["classification"] == "ours"]
    others = [b for b in bridges if b["classification"] == "other-session"]
    orphans = [b for b in bridges if b["classification"] == "orphaned"]

    if our_claude is None:
        # Running outside a Claude session (cron, shell, CI). We can't do
        # session-scoped ownership — fall back to a looser status line.
        if not tg_pids:
            report.warn("no telegram server.ts process (OK if no Claude is running)")
        else:
            pids_str = ", ".join(str(p) for p in tg_pids)
            report.note(
                f"{len(tg_pids)} telegram bridge(s) running ({pids_str}) — "
                "doctor not inside a Claude session, skipping ownership check"
            )
        if orphans:
            pids_str = ", ".join(str(b["pid"]) for b in orphans)
            report.warn(
                f"{len(orphans)} orphaned bridge(s): {pids_str} — owning Claude is gone"
            )
        return

    if len(ours) == 0:
        report.warn(
            f"no bridge owned by this Claude session (pid={our_claude}) — "
            "MCP tools may be disconnected; /reload-plugins to respawn"
        )
    elif len(ours) == 1:
        report.ok(
            f"1 bridge for this session: pid={ours[0]['pid']} (claude={our_claude})"
        )
    else:
        pids_str = ", ".join(str(b["pid"]) for b in ours)
        report.fail(
            f"{len(ours)} bridges owned by this Claude session ({pids_str}) — "
            "true zombie, kill the extras"
        )

    if others:
        summary = ", ".join(f"{b['pid']}→claude:{b['owning_claude']}" for b in others)
        report.note(
            f"{len(others)} bridge(s) in other Claude sessions: {summary} — ignored"
        )

    if orphans:
        pids_str = ", ".join(str(b["pid"]) for b in orphans)
        report.warn(
            f"{len(orphans)} orphaned bridge(s): {pids_str} — "
            "owning Claude is dead, safe to kill"
        )


def _doctor_check_session_subscription(report: DoctorReport) -> None:
    """Warn if this Claude session wasn't launched with --channels telegram.

    Without `--channels plugin:telegram@claude-plugins-official`, the MCP
    bridge can still send messages (plain tool call) but inbound Telegram
    messages never surface as `<channel source="telegram">` blocks — the
    harness drops the notifications. This check catches the silent-
    receive-path failure that is otherwise only detectable by sending a
    test message and watching it vanish.
    """
    report.section("SESSION")
    our_claude = _find_owning_claude(os.getpid())
    if our_claude is None:
        report.note(
            "doctor not running inside a Claude session — subscription check skipped"
        )
        return
    argv = _read_proc_cmdline(our_claude)
    if argv is None:
        report.warn(
            f"could not read /proc/{our_claude}/cmdline — subscription check skipped"
        )
        return
    if session_subscribed_to_telegram(argv):
        report.ok(
            f"claude pid={our_claude} launched with --channels telegram; "
            "inbound messages will surface as channel blocks"
        )
    else:
        # warn, not fail: send-only sessions (outbound MCP tool calls without
        # needing inbound notifications) are a legitimate use case, so don't
        # poison the doctor's exit code.
        report.warn(
            f"claude pid={our_claude} launched WITHOUT --channels telegram — "
            "bridge can send but incoming messages won't surface. "
            "Relaunch with: claude ... "
            "--channels plugin:telegram@claude-plugins-official"
        )


def _find_plugin_server_ts() -> tuple[Path, str | None] | None:
    """Locate the newest plugin-cache server.ts and return (path, hash).

    Returns None if no cached server.ts is found. The hash may itself be
    None if the file exists but can't be read (permissions, race with
    plugin update, etc.) — callers should handle the (path, None) case.

    Shared between the deploy check and the --paths inventory so both
    agree on which file is the canonical deploy target.
    """
    try:
        r = subprocess.run(
            [
                "find",
                str(Path.home() / ".claude" / "plugins" / "cache"),
                "-name",
                "server.ts",
                "-path",
                "*telegram*",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    plugin_hits = [Path(line) for line in r.stdout.strip().splitlines() if line]
    if not plugin_hits:
        return None
    # Prefer newest version dir — sort by mtime.
    plugin_hits.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    plugin = plugin_hits[0]
    return plugin, _file_hash(plugin)


def _doctor_check_deploy(report: DoctorReport) -> None:
    """Auto-run plugin-cache drift check.

    Source dir is resolved in this order:
      1. `$TELEGRAM_SOURCE_DIR` (explicit override)
      2. `~/gits/igor2/telegram-server` (hardcoded default for Igor's box)
      3. skipped — degrades to legacy note for setups without either

    If the source is resolved and the plugin cache is readable, sha256-compares
    both files:
      - match   → ✅ green, nothing to worry about
      - drift   → ❌ red, fails the doctor (exit non-zero) so the hourly
                   watchdog catches plugin auto-update damage automatically
      - skipped → legacy "·" note, unchanged behavior for non-Igor setups

    This check existed before but only ran when `TELEGRAM_SOURCE_DIR` was
    explicitly exported. On 2026-04-14 a plugin auto-update 0.0.5→0.0.6
    silently replaced the deployed two-process fork with upstream vanilla,
    re-enabling polling and causing 409 Conflicts with `telegram_bot.py`.
    Making the check default-on closes that silent-miss.
    """
    report.section("DEPLOY")
    plugin_info = _find_plugin_server_ts()
    source_path, source_source = _resolve_source_dir()
    src = (source_path / "server.ts") if source_path else None

    if src is None:
        # No canonical source resolved (neither env var nor default).
        # We can still validate the plugin cache exists; we just can't tell
        # whether it's in sync with an upstream copy. Legacy skipped note.
        if plugin_info is None:
            report.warn(
                "no plugin-cache server.ts found "
                "(set TELEGRAM_SOURCE_DIR or populate ~/gits/igor2/telegram-server)"
            )
            return
        plugin_path, plugin_hash = plugin_info
        # If TELEGRAM_SOURCE_DIR was explicitly set but pointed somewhere that
        # doesn't contain server.ts, say so loudly — otherwise the operator
        # thinks the check ran.
        if source_source == "env":
            report.note(
                f"plugin cache: {plugin_hash} ({plugin_path}) — "
                "TELEGRAM_SOURCE_DIR set but missing server.ts, drift check skipped"
            )
        else:
            report.note(
                f"plugin cache: {plugin_hash} ({plugin_path}) — "
                "no source dir resolvable, drift check skipped"
            )
        return

    src_hash = _file_hash(src)
    if plugin_info is None:
        report.warn(f"no plugin-cache server.ts found (src={src_hash})")
        return
    plugin_path, plugin_hash = plugin_info
    if src_hash and plugin_hash and src_hash == plugin_hash:
        report.ok(
            f"plugin cache matches source (sha256: {src_hash}) — "
            f"source={src} [{source_source}]"
        )
    else:
        # Red X, not warning — doctor exits non-zero and the watchdog will
        # catch it. This is the whole point of auto-running the check.
        report.fail(
            f"plugin cache DRIFT: source={src_hash} cache={plugin_hash} — "
            f"run: cp {src} {plugin_path}"
        )


def _doctor_check_token(report: DoctorReport) -> None:
    token_file = Path.home() / ".claude" / "channels" / "telegram" / ".env"
    if not token_file.exists():
        report.fail(f"token file missing: {token_file}")
        return
    if not os.access(token_file, os.R_OK):
        report.fail(f"token file {token_file} not readable")
        return
    try:
        content = token_file.read_text()
    except OSError as e:
        report.fail(f"token file read failed: {e}")
        return
    has_token = any(
        line.strip().startswith("TELEGRAM_BOT_TOKEN=")
        and len(line.split("=", 1)[1].strip()) > 0
        for line in content.splitlines()
    )
    if has_token:
        report.ok("token: present")
    else:
        report.fail("token: missing TELEGRAM_BOT_TOKEN= line")


def _doctor_check_access(report: DoctorReport) -> None:
    access_file = Path.home() / ".claude" / "channels" / "telegram" / "access.json"
    if not access_file.exists():
        report.fail(f"access.json missing at {access_file}")
        return
    try:
        data = json.loads(access_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        report.fail(f"access.json parse failed: {e}")
        return
    if "dmPolicy" not in data or "allowFrom" not in data:
        report.fail("access.json missing dmPolicy/allowFrom keys")
        return
    allow = len(data.get("allowFrom", []) or [])
    groups = len(data.get("groups", {}) or {})
    pending = len(data.get("pending", {}) or {})
    report.ok(f"access.json: {allow} allowed, {groups} groups, {pending} pending")


def _doctor_check_hooks(report: DoctorReport, base: Path) -> None:
    report.section("HOOKS")
    settings_file = Path.home() / ".claude" / "settings.json"
    if not settings_file.exists():
        report.fail(f"settings.json missing at {settings_file}")
        return
    try:
        settings = json.loads(settings_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        report.fail(f"settings.json parse failed: {e}")
        return

    # Walk all hook command strings looking for log-telegram*.py references.
    hook_paths: list[tuple[str, str]] = []  # (basename, resolved_path)

    def _walk(obj) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "command" and isinstance(v, str) and "log-telegram" in v:
                    # Heuristic: last .py token in the command is the script path.
                    tokens = v.split()
                    for tok in tokens:
                        if tok.endswith(".py") and "log-telegram" in tok:
                            hook_paths.append((Path(tok).name, tok))
                            break
                else:
                    _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(settings.get("hooks", {}))

    if not hook_paths:
        report.warn("no log-telegram*.py hook entries found in settings.json")
    else:
        seen: set[str] = set()
        for name, path in hook_paths:
            if path in seen:
                continue
            seen.add(path)
            if Path(path).exists():
                report.ok(f"{name}: {path}")
            else:
                report.fail(f"{name}: {path} does not exist on disk")

    # telegram_log.db writable check.
    log_db = base / "telegram_log.db"
    if not log_db.exists():
        try:
            log_db.parent.mkdir(parents=True, exist_ok=True)
            log_db.touch()
        except OSError as e:
            report.fail(f"telegram_log.db: cannot create at {log_db}: {e}")
            return
    if os.access(log_db, os.W_OK):
        report.ok(f"telegram_log.db: writable ({log_db})")
    else:
        report.fail(f"telegram_log.db: {log_db} not writable")


def _doctor_check_logs(report: DoctorReport, base: Path) -> None:
    """Check log file state and surface the last few lines for context.

    Igor's debugging rule: when something breaks, you almost always want to
    see the actual log content, not just 'last write N ago'. The doctor
    shows the tail inline so you don't have to cat a second file.
    """
    report.section("LOGS")

    # server.log is the shared log stream — both telegram_bot.py (via
    # its Python log() helper) and server.ts (via its bun log() helper)
    # append to this file, tagged with [bot] or [mcp] respectively.
    log_file = base / "server.log"
    if not log_file.exists():
        report.warn(f"server.log missing at {log_file}")
    else:
        try:
            age = time.time() - log_file.stat().st_mtime
        except OSError as e:
            report.warn(f"server.log stat failed: {e}")
        else:
            if age < 600:
                report.ok(f"server.log: last write {_fmt_age(age)} ago ({log_file})")
            else:
                report.warn(
                    f"server.log: last write {_fmt_age(age)} ago (>10m — may be stale) ({log_file})"
                )
            # Tail the last 8 lines so the operator sees recent activity
            # without leaving the doctor.
            try:
                lines = log_file.read_text(errors="replace").splitlines()[-8:]
            except OSError as e:
                report.warn(f"server.log unreadable: {e}")
            else:
                report.note(f"server.log tail ({len(lines)} lines):")
                for line in lines:
                    report.note(f"    {line}")

    # Known auxiliary log locations. Show them even when present-but-stale
    # so the operator knows where to look by hand.
    aux_logs = [
        ("startup.log", base / "startup.log"),
        (
            "legacy server.log",
            Path.home() / ".claude" / "channels" / "telegram" / "server.log",
        ),
        ("watchdog reload log", Path("/tmp/watchdog_reload.log")),
    ]
    for label, p in aux_logs:
        if p.exists():
            try:
                age = time.time() - p.stat().st_mtime
            except OSError:
                report.note(f"{label}: exists at {p}")
            else:
                report.note(f"{label}: {_fmt_age(age)} ago — {p}")


def run_doctor() -> int:
    """Run all doctor checks and print a report. Returns exit code."""
    base = _base_dir()
    report = DoctorReport()
    _doctor_check_bot_pid(report, base)
    _doctor_check_socket(report, base)
    _doctor_check_inbound_db(report, base)
    _doctor_check_server_ts(report)
    _doctor_check_session_subscription(report)
    _doctor_check_deploy(report)
    report.section("CONFIG")
    _doctor_check_token(report)
    _doctor_check_access(report)
    _doctor_check_hooks(report, base)
    _doctor_check_logs(report, base)
    print(report.render())
    return 1 if report.failures > 0 else 0


def run_paths() -> int:
    """Print every file the two-process Telegram chain cares about.

    This used to live as a table in the /telegram-debug skill — it kept
    rotting as paths moved (e.g. the STATE_DIR→BASE_DIR migration in
    2026-04 made the old table actively wrong). Putting it in code means
    the single source of truth is the constants at the top of this file
    and the --paths output is always correct.

    Skill author's rule: if a diagnostic is "here's a list of files and
    whether they exist," it goes here, not in prose.
    """
    base = _base_dir()
    plugin_info = _find_plugin_server_ts()
    plugin_path = plugin_info[0] if plugin_info else None

    groups: list[tuple[str, list[tuple[str, Path]]]] = [
        (
            "Runtime state (base = LARRY_TELEGRAM_DIR)",
            [
                ("bot.pid", base / "bot.pid"),
                ("bot.sock", base / "bot.sock"),
                ("inbound.db", base / "inbound.db"),
                ("telegram_log.db", base / "telegram_log.db"),
                ("server.log", base / "server.log"),
                ("startup.log", base / "startup.log"),
                ("attachments/", base / "attachments"),
            ],
        ),
        (
            "Credential store (plugin-managed)",
            [
                (
                    ".env (BOT_TOKEN)",
                    Path.home() / ".claude" / "channels" / "telegram" / ".env",
                ),
                (
                    "access.json",
                    Path.home() / ".claude" / "channels" / "telegram" / "access.json",
                ),
                (
                    "approved/",
                    Path.home() / ".claude" / "channels" / "telegram" / "approved",
                ),
            ],
        ),
        *(
            [
                (
                    f"Source tree ({_resolve_source_dir()[1] or '?'}: {_source_dir()})",
                    [
                        ("telegram_bot.py", _source_dir() / "telegram_bot.py"),  # type: ignore[operator]
                        ("server.ts", _source_dir() / "server.ts"),  # type: ignore[operator]
                        ("hooks/", _source_dir() / "hooks"),  # type: ignore[operator]
                    ],
                )
            ]
            if _source_dir()
            else []
        ),
        (
            "Plugin cache (deploy target)",
            [
                (
                    "server.ts (deployed)",
                    plugin_path if plugin_path else Path("/nonexistent"),
                ),
            ],
        ),
        (
            "Legacy / transition (should be empty post-migration)",
            [
                (
                    "~/.claude/channels/telegram/server.log",
                    Path.home() / ".claude" / "channels" / "telegram" / "server.log",
                ),
                (
                    "~/.claude/channels/telegram/bot.pid",
                    Path.home() / ".claude" / "channels" / "telegram" / "bot.pid",
                ),
                (
                    "~/.claude/channels/telegram/inbound.jsonl",
                    Path.home() / ".claude" / "channels" / "telegram" / "inbound.jsonl",
                ),
                (
                    "~/.claude/telegram_log.db",
                    Path.home() / ".claude" / "telegram_log.db",
                ),
            ],
        ),
    ]

    print("=== Telegram chain file map ===\n")
    for heading, items in groups:
        print(f"{heading}:")
        for label, path in items:
            if path == Path("/nonexistent"):
                print(f"  ? {label:<40s} (plugin cache not found)")
                continue
            if path.exists():
                mark = OK
                try:
                    size = path.stat().st_size
                    extra = f" ({size}B)" if path.is_file() and size < 1024 else ""
                except OSError:
                    extra = ""
                print(f"  {mark} {label:<40s} {path}{extra}")
            else:
                print(f"  {BAD} {label:<40s} {path}  (missing)")
        print()
    return 0


def parse_env_token(data: str) -> str | None:
    """Extract TELEGRAM_BOT_TOKEN from .env-style text.

    Handles `export ` prefix, surrounding quotes, inline `# comments` on
    unquoted values, and leading whitespace. Returns None if no match.
    Pure — no filesystem access, so it can be unit-tested directly.
    """
    for line in data.splitlines():
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()
        if not stripped.startswith("TELEGRAM_BOT_TOKEN="):
            continue
        value = stripped.split("=", 1)[1].strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            # Quoted: take the content as-is (no comment stripping).
            return value[1:-1]
        # Unquoted: a `#` starts an inline comment.
        if "#" in value:
            value = value.split("#", 1)[0].rstrip()
        return value or None
    return None


def _read_bot_token(token_file: Path | None = None) -> str:
    """Read TELEGRAM_BOT_TOKEN from ~/.claude/channels/telegram/.env.

    `token_file` is injected for tests; defaults to the canonical path.
    """
    if token_file is None:
        token_file = Path.home() / ".claude" / "channels" / "telegram" / ".env"
    if not token_file.exists():
        raise RuntimeError(f"token file missing: {token_file}")
    token = parse_env_token(token_file.read_text())
    if not token:
        raise RuntimeError(f"TELEGRAM_BOT_TOKEN= not found in {token_file}")
    return token


def _default_chat_id() -> str | None:
    """Pull the most recent inbound chat_id from inbound.db as a sensible default."""
    db = (
        Path(os.environ.get("LARRY_TELEGRAM_DIR", Path.home() / "larry-telegram"))
        / "inbound.db"
    )
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        row = con.execute(
            "SELECT chat_id FROM inbound ORDER BY id DESC LIMIT 1"
        ).fetchone()
        con.close()
        return str(row[0]) if row else None
    except Exception as e:
        # Don't swallow silently — the caller's "no chat_id" error is
        # confusing if the real failure was a corrupt DB or locked file.
        print(f"default chat_id lookup failed: {e}", file=sys.stderr)
        return None


TELEGRAM_API_BASE = "https://api.telegram.org"
DIRECT_SEND_TAG = "[direct-send]"


def build_direct_request(
    token: str,
    chat_id: str,
    text: str,
) -> tuple[str, bytes]:
    """Build the (url, body) for a Bot API sendMessage request.

    Pure — no network, no token leak risk in tests. Auto-prefixes the
    `[direct-send]` tag so the operator can see on their phone that MCP
    was down when the message landed.
    """
    import urllib.parse

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    tagged = f"{DIRECT_SEND_TAG} {text}"
    body = urllib.parse.urlencode({"chat_id": str(chat_id), "text": tagged}).encode()
    return url, body


def _redact(s: str, token: str) -> str:
    """Replace every occurrence of `token` in `s` with `<redacted>`.

    Defense in depth — CPython's urllib error strings currently don't
    embed the URL, but that's not a contract. Scrub before printing to
    stderr so the token can never appear in operator bug reports.
    """
    if token and token in s:
        return s.replace(token, "<redacted>")
    return s


def send_direct(text: str, chat_id: str | None = None) -> int:
    """Emergency direct-send: POST to Telegram Bot API, bypassing MCP entirely.

    Use when server.ts (the MCP bridge) might be down — depends only on the bot
    token and Telegram's HTTPS endpoint, nothing on the local two-process chain.
    """
    import urllib.request

    try:
        token = _read_bot_token()
    except RuntimeError as e:
        print(f"direct send failed: {e}", file=sys.stderr)
        return 1

    if chat_id is None:
        chat_id = _default_chat_id()
    if not chat_id:
        print(
            "direct send failed: no chat_id (pass --chat-id or have an inbound message on record)",
            file=sys.stderr,
        )
        return 1

    url, data = build_direct_request(token, chat_id, text)
    try:
        with urllib.request.urlopen(url, data=data, timeout=10) as resp:
            body = resp.read().decode()
            if resp.status != 200:
                print(
                    f"direct send HTTP {resp.status}: {_redact(body, token)}",
                    file=sys.stderr,
                )
                return 1
    except Exception as e:
        print(f"direct send failed: {_redact(str(e), token)}", file=sys.stderr)
        return 1

    print(f"sent to chat_id={chat_id}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Telegram MCP diagnostic tool")
    parser.add_argument("--json", action="store_true", help="JSON output (pipe to jq)")
    parser.add_argument(
        "--tail",
        type=int,
        default=20,
        help="Number of log lines/messages to show (default: 20)",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Validate the two-process chain end-to-end (exit 1 on failure)",
    )
    parser.add_argument(
        "--paths",
        action="store_true",
        help="Print every file the two-process chain cares about, with existence check",
    )
    parser.add_argument(
        "--direct-send",
        "--send",
        dest="direct_send",
        metavar="TEXT",
        help="EMERGENCY DIRECT-SEND via Telegram Bot API — bypasses MCP entirely. Use when server.ts is down and you still need to reach Igor. Message is auto-tagged with [direct-send] so it's visually distinct in Telegram. Runs alone; ignores --doctor/--paths if combined.",
    )
    parser.add_argument(
        "--chat-id",
        help="Target chat_id for --direct-send (defaults to last inbound chat_id from inbound.db)",
    )
    args = parser.parse_args()

    if args.direct_send is not None:
        sys.exit(send_direct(args.direct_send, chat_id=args.chat_id))

    if args.doctor:
        sys.exit(run_doctor())

    if args.paths:
        sys.exit(run_paths())

    diag = full_diagnostic(tail=args.tail)

    if args.json:
        print(json.dumps(diag, indent=2, default=str))
    else:
        print_report(diag)


if __name__ == "__main__":
    main()
