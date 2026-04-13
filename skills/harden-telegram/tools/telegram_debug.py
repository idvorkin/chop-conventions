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


def _source_dir() -> Path | None:
    """Canonical server.ts source directory (for hash-drift check).

    Set TELEGRAM_SOURCE_DIR to the dir containing server.ts. If unset, drift
    checks degrade to a note — the doctor still runs, it just can't tell you
    whether the plugin-cache copy matches an upstream source.
    """
    env = os.environ.get("TELEGRAM_SOURCE_DIR")
    return Path(env).expanduser() if env else None


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
    """Check if our custom server.ts is deployed and matches source."""
    # Find the version dir
    if not PLUGIN_DIR.exists():
        return {"installed": False}
    versions = sorted(
        [d for d in PLUGIN_DIR.iterdir() if d.is_dir() and d.name[0].isdigit()],
        reverse=True,
    )
    if not versions:
        return {"installed": False}
    version_dir = versions[0]  # newest version
    deployed_file = version_dir / "server.ts"
    source_ts = _source_server_ts()
    result: dict = {
        "installed": True,
        "version": version_dir.name,
        "deploy_path": str(deployed_file),
        "source_path": str(source_ts) if source_ts else None,
        "source_configured": source_ts is not None,
        "server_ts_exists": deployed_file.exists(),
    }
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
        # Compare source vs deployed — only if a source dir is configured.
        if source_ts is not None:
            source_hash = _file_hash(source_ts)
            deploy_hash = _file_hash(deployed_file)
            result["source_hash"] = source_hash
            result["deploy_hash"] = deploy_hash
            if source_hash and deploy_hash:
                result["in_sync"] = source_hash == deploy_hash
                if not result["in_sync"]:
                    result["WARNING_DRIFT"] = (
                        "Source and deployed server.ts differ! "
                        f"Run: cp {source_ts} {deployed_file}"
                    )
            result["source_exists"] = source_ts.exists()
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
    pids = [p for p in r.stdout.strip().split() if p]
    if len(pids) == 0:
        # 0 is OK — Claude may not be running. Informational only.
        report.warn("no server.ts process (OK if Claude isn't running)")
    elif len(pids) == 1:
        report.ok(f"1 process: pid={pids[0]}")
    else:
        report.fail(f"{len(pids)} processes running: {', '.join(pids)} — zombie bridge")


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
    report.section("DEPLOY")
    plugin_info = _find_plugin_server_ts()
    src = _source_server_ts()
    if src is None:
        # No canonical source configured — we can still validate the plugin
        # cache exists, just not whether it's in sync with an upstream copy.
        if plugin_info is None:
            report.warn("no plugin-cache server.ts found (TELEGRAM_SOURCE_DIR unset)")
            return
        plugin_path, plugin_hash = plugin_info
        report.note(
            f"plugin cache: {plugin_hash} ({plugin_path}) — "
            "set TELEGRAM_SOURCE_DIR to enable drift check"
        )
        return
    if not src.exists():
        report.fail(f"source server.ts missing at {src}")
        return
    src_hash = _file_hash(src)
    if plugin_info is None:
        report.warn(f"no plugin-cache server.ts found (src={src_hash})")
        return
    plugin_path, plugin_hash = plugin_info
    if src_hash and plugin_hash and src_hash == plugin_hash:
        report.ok(f"source == plugin: {src_hash} ({plugin_path})")
    else:
        report.warn(
            f"source/plugin drift: src={src_hash} plugin={plugin_hash} — "
            f"redeploy needed ({plugin_path})"
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
                    "Source tree (TELEGRAM_SOURCE_DIR)",
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
    args = parser.parse_args()

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
