#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = [
#   "python-telegram-bot>=20.7",
#   "aiosqlite>=0.19",
# ]
# ///
# ^ 3.14 is excluded: python-telegram-bot doesn't yet support cpython 3.14.
#   Host default is 3.14, so an unpinned `>=3.11` picks the wrong interpreter.
#   uv resolves this to 3.12 automatically with the upper bound set.
# pyright: reportMissingImports=false
# ^ deps live in the PEP-723 script block above; Pyright can't see uv's ephemeral venv.
"""
telegram_bot.py — persistent Telegram poller.

Splits with server.ts: this process owns getUpdates and writes all inbound
events to SQLite; server.ts reads from SQLite and delivers to Claude via MCP.

See: docs/superpowers/specs/2026-04-12-telegram-two-process-design.md
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import fcntl
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import Application, ContextTypes

# Imported eagerly (not lazy) so the reaction call sites have the class
# available. Tiny import cost, major correctness win — passing a dict here
# instead of a ReactionType instance fails with "unhashable type: 'dict'"
# because python-telegram-bot hashes reactions internally for deduplication.
try:
    from telegram import ReactionTypeEmoji
except Exception:  # pragma: no cover — only triggers if telegram isn't installed
    ReactionTypeEmoji = None  # type: ignore[assignment,misc]


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS inbound (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    message_id TEXT,
    user_id TEXT,
    username TEXT,
    message_type TEXT NOT NULL DEFAULT 'message',
    text TEXT,
    attachment_kind TEXT,
    attachment_path TEXT,
    attachment_file_id TEXT,
    attachment_size INTEGER,
    attachment_mime TEXT,
    attachment_name TEXT,
    callback_data TEXT,
    gate_action TEXT NOT NULL,
    delivered INTEGER DEFAULT 0,
    error TEXT,
    previous_text TEXT,
    edit_count INTEGER NOT NULL DEFAULT 0,
    edited_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_inbound_undelivered ON inbound(delivered, id) WHERE delivered = 0;
CREATE INDEX IF NOT EXISTS idx_inbound_ts ON inbound(ts);
"""


# Columns added post-v1 of the schema. Applied idempotently on every init —
# ALTER TABLE ADD COLUMN is safe concurrent with reads/writes in WAL mode,
# so deploying this does NOT require restarting telegram_bot.py for the DB
# itself; only picking up the new handler logic requires a restart.
# Tuple shape: (column_name, ALTER fragment).
_INBOUND_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("previous_text", "ALTER TABLE inbound ADD COLUMN previous_text TEXT"),
    (
        "edit_count",
        "ALTER TABLE inbound ADD COLUMN edit_count INTEGER NOT NULL DEFAULT 0",
    ),
    ("edited_at", "ALTER TABLE inbound ADD COLUMN edited_at TEXT"),
)


def _existing_inbound_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(inbound)").fetchall()
    return {r[1] for r in rows}


def migrate_inbound_sync(conn: sqlite3.Connection) -> list[str]:
    """Apply idempotent ADD COLUMN migrations to the `inbound` table.

    Returns the list of column names that were added (empty if already up
    to date). Safe to call every startup; safe to run concurrent with
    reads/writes in WAL mode.
    """
    existing = _existing_inbound_columns(conn)
    added: list[str] = []
    for col, ddl in _INBOUND_MIGRATIONS:
        if col in existing:
            continue
        conn.execute(ddl)
        added.append(col)
    if added:
        conn.commit()
    return added


def init_db_sync(db_path: Path) -> None:
    """Sync DB init — called before the asyncio loop starts so tests can use it."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        migrate_inbound_sync(conn)
        conn.commit()
    finally:
        conn.close()


def _state_dir() -> Path:
    return Path(
        os.environ.get(
            "TELEGRAM_STATE_DIR", str(Path.home() / ".claude" / "channels" / "telegram")
        )
    ).expanduser()


def _access_file() -> Path:
    return _state_dir() / "access.json"


def _default_access() -> dict[str, Any]:
    return {"dmPolicy": "pairing", "allowFrom": [], "groups": {}, "pending": {}}


def load_access() -> dict[str, Any]:
    """Read access.json; return defaults if missing; quarantine corrupt file."""
    p = _access_file()
    try:
        raw = p.read_text()
    except FileNotFoundError:
        return _default_access()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        ts = int(time.time())
        p.rename(p.with_suffix(f".corrupt-{ts}"))
        return _default_access()
    merged = _default_access()
    merged.update(parsed)
    return merged


def save_access(access: dict[str, Any]) -> None:
    p = _access_file()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(access, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def gate_message(evt: dict[str, Any]) -> dict[str, Any]:
    """Port of server.ts:gate() — evaluate allowlist + pairing for an inbound event.

    evt keys: from_id, chat_id, chat_type, text (optional).
    Returns dict with at minimum {"action": "allow"|"drop"|"pair"}.
    For pair: also {"code": str, "isResend": bool}.
    For allow in a group: also {"require_mention": bool}.
    """
    access = load_access()
    # prune expired pending
    now_ms = int(time.time() * 1000)
    changed = False
    for code, p in list(access["pending"].items()):
        if p.get("expiresAt", 0) < now_ms:
            del access["pending"][code]
            changed = True
    if changed:
        save_access(access)

    if access["dmPolicy"] == "disabled":
        return {"action": "drop"}

    from_id = str(evt["from_id"])
    chat_type = evt["chat_type"]
    chat_id = str(evt["chat_id"])

    if chat_type == "private":
        if from_id in access["allowFrom"]:
            return {"action": "allow"}
        if access["dmPolicy"] == "allowlist":
            return {"action": "drop"}

        # pairing mode: resend for existing pending
        for code, p in access["pending"].items():
            if p["senderId"] == from_id:
                if p.get("replies", 1) >= 2:
                    return {"action": "drop"}
                p["replies"] = p.get("replies", 1) + 1
                save_access(access)
                return {"action": "pair", "code": code, "isResend": True}

        if len(access["pending"]) >= 3:
            return {"action": "drop"}

        code = secrets.token_hex(3)  # 6 hex chars — matches server.ts randomBytes(3)
        access["pending"][code] = {
            "senderId": from_id,
            "chatId": chat_id,
            "createdAt": now_ms,
            "expiresAt": now_ms + 3600 * 1000,  # 1h, matches server.ts
            "replies": 1,
        }
        save_access(access)
        return {"action": "pair", "code": code, "isResend": False}

    if chat_type in ("group", "supergroup"):
        policy = access["groups"].get(chat_id)
        if not policy:
            return {"action": "drop"}
        allow_from = policy.get("allowFrom", [])
        if allow_from and from_id not in allow_from:
            return {"action": "drop"}
        # requireMention + mention detection handled at the handler layer
        return {
            "action": "allow",
            "require_mention": policy.get("requireMention", True),
        }

    return {"action": "drop"}


def read_env_token() -> str:
    """Read bot token from ~/.claude/channels/telegram/.env; real env wins.

    Port of server.ts:65-72 idiom. Chmods .env to 0600 as a safety net.
    """
    env_file = _state_dir() / ".env"
    try:
        os.chmod(env_file, 0o600)
    except OSError:
        pass
    try:
        for line in env_file.read_text().splitlines():
            m = line.strip()
            if not m or m.startswith("#"):
                continue
            if "=" in m:
                k, v = m.split("=", 1)
                if k and os.environ.get(k) is None:
                    os.environ[k] = v
    except FileNotFoundError:
        pass
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError(
            f"TELEGRAM_BOT_TOKEN required — set in {env_file}, "
            "format: TELEGRAM_BOT_TOKEN=123456789:AAH..."
        )
    return token


MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024  # 50MB — matches server.ts

# Dual-reaction liveness (bead igor2-bgt.1):
#   INNER (this process): 👀 — "saw it, queued to SQLite"
#   OUTER (server.ts):    ✅ — "delivered to Claude"
# Both emojis must stay in Telegram's fixed reaction whitelist.
INNER_ACK_EMOJI = "👀"

# Permission-reply spec (port of server.ts:115):
# 5 lowercase letters a-z minus 'l' (l-vs-1 confusion). Case-insensitive for
# phone autocorrect. Strict: no bare yes/no, no prefix/suffix chatter.
PERMISSION_REPLY_RE = re.compile(r"^\s*(y|yes|n|no)\s+([a-km-z]{5})\s*$", re.IGNORECASE)

# Shared log path — both bot and server.ts append here.
_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5MB


def _log_path() -> Path:
    base = Path(
        os.environ.get("LARRY_TELEGRAM_DIR", str(Path.home() / "larry-telegram"))
    ).expanduser()
    return base / "server.log"


def log(msg: str) -> None:
    """Append a [bot]-tagged line to server.log + stderr. Rotates at 5MB."""
    ts = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")
    if ts.endswith("+00:00"):
        ts = ts[:-6] + "Z"
    line = f"[{ts}] [bot] {msg}\n"
    try:
        sys.stderr.write(line)
    except Exception:
        pass
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        # Rotate if over cap
        try:
            st = p.stat()
            if st.st_size > _LOG_MAX_BYTES:
                p.rename(p.with_suffix(p.suffix + ".1"))
        except FileNotFoundError:
            pass
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def persist_inbound_sync(
    db_path: Path,
    evt: dict[str, Any],
    gate_res: dict[str, Any],
    message_type: str = "message",
    callback_data: str | None = None,
) -> int:
    """Sync insert for tests + internal reuse. Returns the new row id.

    The async path (handle_any_message) uses aiosqlite directly.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        cur = conn.execute(
            """INSERT INTO inbound
               (ts, chat_id, message_id, user_id, username, message_type, text,
                callback_data, gate_action)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evt.get("ts", ""),
                str(evt.get("chat_id", "")),
                str(evt.get("message_id", "") or ""),
                str(evt.get("from_id", "") or ""),
                evt.get("username", "") or "",
                message_type,
                evt.get("text", "") or "",
                callback_data,
                gate_res["action"],
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Unix domain socket wakeup server
# -----------------------------------------------------------------------------

# Connected writers. Mutated only from the event loop that owns the server.
_CLIENTS: set[asyncio.StreamWriter] = set()

# Set by start_socket_server_sync so tests can drive notify from another thread.
_SYNC_LOOP: asyncio.AbstractEventLoop | None = None


async def _handle_socket_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    _CLIENTS.add(writer)
    try:
        while True:
            data = await reader.read(1024)
            if not data:
                break
    finally:
        _CLIENTS.discard(writer)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def start_socket_server(sock_path: Path) -> asyncio.base_events.Server:
    """Bind Unix socket listener. Unlinks stale file first. Returns server."""
    sock_path = Path(sock_path)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        sock_path.unlink()
    except FileNotFoundError:
        pass
    server = await asyncio.start_unix_server(_handle_socket_client, path=str(sock_path))
    try:
        os.chmod(sock_path, 0o600)
    except OSError:
        pass
    return server


async def notify_clients() -> None:
    """Push a bare '\\n' wakeup to every connected client. Drops dead writers."""
    dead: list[asyncio.StreamWriter] = []
    for w in list(_CLIENTS):
        try:
            w.write(b"\n")
            await w.drain()
        except Exception:
            dead.append(w)
    for w in dead:
        _CLIENTS.discard(w)


def start_socket_server_sync(sock_path: Path):
    """Test shim: run the async socket server in a background thread.

    Returns (thread, stop_fn). stop_fn blocks until the loop finishes.
    """
    import threading

    global _SYNC_LOOP
    started = threading.Event()
    loop_holder: dict[str, Any] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_holder["loop"] = loop
        try:
            server = loop.run_until_complete(start_socket_server(sock_path))
            loop_holder["server"] = server
            started.set()
            loop.run_forever()
        finally:
            try:
                server = loop_holder.get("server")
                if server is not None:
                    server.close()
                    loop.run_until_complete(server.wait_closed())
            except Exception:
                pass
            loop.close()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    started.wait(timeout=5)
    _SYNC_LOOP = loop_holder["loop"]

    def stop() -> None:
        loop = loop_holder.get("loop")
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)

    return thread, stop


def notify_clients_sync() -> None:
    """Test shim: schedule notify_clients() on the sync-shim loop."""
    global _SYNC_LOOP
    loop = _SYNC_LOOP
    if loop is None or not loop.is_running():
        raise RuntimeError(
            "socket loop not running — call start_socket_server_sync first"
        )
    fut = asyncio.run_coroutine_threadsafe(notify_clients(), loop)
    fut.result(timeout=5)


def acquire_singleton(pid_file: Path) -> int:
    """Acquire exclusive PID file lock. Returns fd (keep open to hold lock).

    Exits non-zero if another instance holds the lock.
    """
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(pid_file, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.stderr.write(f"telegram_bot.py: already running (lock: {pid_file})\n")
        sys.exit(2)
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-dir",
        default=os.environ.get(
            "LARRY_TELEGRAM_DIR", str(Path.home() / "larry-telegram")
        ),
    )
    parser.add_argument(
        "--dry-run-singleton",
        action="store_true",
        help="acquire lock and sleep; used by tests",
    )
    args = parser.parse_args()

    base = Path(args.base_dir).expanduser()
    base.mkdir(parents=True, exist_ok=True)

    acquire_singleton(base / "bot.pid")

    if args.dry_run_singleton:
        try:
            import signal

            signal.pause()
        except KeyboardInterrupt:
            pass
        return

    asyncio.run(run())


async def run() -> None:
    """Main asyncio entry — wires python-telegram-bot Application and polls forever."""
    import aiosqlite
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        MessageHandler,
        filters,
    )

    token = read_env_token()
    base = Path(
        os.environ.get("LARRY_TELEGRAM_DIR", str(Path.home() / "larry-telegram"))
    ).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    db_path = base / "inbound.db"
    init_db_sync(db_path)

    state: dict[str, Any] = {
        "db_path": str(db_path),
        "base": base,
        "started_at": time.time(),
    }

    async def _post_init(app: "Application") -> None:
        # Long-lived aiosqlite connection in autocommit mode so we can issue
        # BEGIN IMMEDIATE manually (per spec §telegram_bot.py).
        state["db"] = await aiosqlite.connect(str(db_path), isolation_level=None)
        await state["db"].execute("PRAGMA busy_timeout=5000")
        await state["db"].execute("PRAGMA journal_mode=WAL")
        me = await app.bot.get_me()
        state["bot_username"] = me.username or ""
        # Bind Unix domain socket for wakeup signaling.
        sock_path = base / "bot.sock"
        state["socket_server"] = await start_socket_server(sock_path)
        # Background tasks — approved/ dir poller + periodic heartbeat.
        state["tasks"] = [
            asyncio.create_task(_approved_poller(app)),
            asyncio.create_task(_heartbeat_loop(state)),
        ]
        log(
            f"polling as @{state['bot_username']} pid={os.getpid()} "
            f"dmPolicy={load_access()['dmPolicy']}"
        )

    app = Application.builder().token(token).post_init(_post_init).build()
    app.bot_data["state"] = state
    # Commands get their own handlers (DM-only guard inside each).
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    # Generic handler for everything else. Exclude commands so they don't
    # double-fire through the gate and create extra rows.
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_any_message))

    await app.initialize()

    # python-telegram-bot only calls the .post_init() callback from
    # Application.run_polling()/run_webhook(). In this manual lifecycle
    # (initialize → start → updater.start_polling) we have to invoke it
    # ourselves between initialize() and start(), matching the order
    # run_polling would have used. Without this, _post_init never runs —
    # bot.sock is never bound, no DB connection, no background tasks.
    await _post_init(app)

    await app.start()

    # 409 Conflict + error supervisor. Updater.start_polling accepts an
    # error_callback that fires on network/telegram errors. We flag a Conflict
    # via the shared signal and wake the supervisor loop to kill the stale
    # poller and retry with exponential backoff.
    from telegram.error import Conflict

    conflict_event = asyncio.Event()
    state["conflict_event"] = conflict_event
    state["last_error"] = None

    loop = asyncio.get_running_loop()

    def _on_polling_error(err: Any) -> None:
        state["last_error"] = err
        if isinstance(err, Conflict):
            log(f"409 Conflict from getUpdates: {err}")
            loop.call_soon_threadsafe(conflict_event.set)
        else:
            log(f"polling error (will retry internally): {err}")

    attempt = 0
    try:
        while True:
            try:
                await app.updater.start_polling(error_callback=_on_polling_error)
            except Conflict as e:
                _on_polling_error(e)
            except Exception as e:
                log(f"start_polling raised: {e}")
                state["last_error"] = e

            # Wait for a conflict signal, or sleep forever on clean start.
            # On conflict: kill stale bun server.ts and back off.
            try:
                await conflict_event.wait()
            except asyncio.CancelledError:
                raise

            # Stop current updater (best-effort) and let the retry loop
            # back off — Telegram drops the stale poller on its own.
            try:
                await app.updater.stop()
            except Exception as e:
                log(f"updater.stop during conflict failed: {e}")

            conflict_event.clear()

            attempt += 1
            # Exponential backoff: 1, 2, 4, 8, 16, 30, 30, 30, …
            delay = min(2 ** (attempt - 1), 30)
            if attempt == 8:
                log(
                    f"409 Conflict persists after {attempt} attempts — "
                    "another poller is holding the bot token. Will keep retrying."
                )
            if attempt >= 8:
                delay = 30
            log(f"409 retry in {delay}s (attempt {attempt})")
            await asyncio.sleep(delay)
    finally:
        try:
            await app.updater.stop()
        except Exception:
            pass
        try:
            await app.stop()
        except Exception:
            pass
        try:
            await app.shutdown()
        except Exception:
            pass


def _safe_name(s: str | None) -> str | None:
    """Strip delimiters/newlines — port of server.ts:safeName()."""
    if s is None:
        return None
    return re.sub(r"[<>\[\]\r\n;]", "_", s)


def _safe_ext(raw_ext: str | None) -> str:
    """Sanitize a filename extension — port of server.ts's download_attachment."""
    if not raw_ext:
        return "bin"
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", raw_ext)
    return cleaned or "bin"


def _extract_attachment(msg: Any) -> dict[str, Any] | None:
    """Inspect a telegram Message and return an attachment descriptor dict.

    Keys: kind, file_id, size (optional), mime (optional), name (optional).
    Returns None if the message has no attachment we care about.
    Mirrors server.ts bot.on('message:*') handlers — photo/document/voice/
    audio/video/video_note/sticker.
    """
    # photo is a list of PhotoSize — largest is last (server.ts:830-832).
    if getattr(msg, "photo", None):
        photos = list(msg.photo)
        if photos:
            best = photos[-1]
            return {
                "kind": "photo",
                "file_id": best.file_id,
                "size": getattr(best, "file_size", None),
                "mime": None,
                "name": None,
            }
    if getattr(msg, "voice", None):
        v = msg.voice
        return {
            "kind": "voice",
            "file_id": v.file_id,
            "size": getattr(v, "file_size", None),
            "mime": getattr(v, "mime_type", None),
            "name": None,
        }
    if getattr(msg, "document", None):
        d = msg.document
        return {
            "kind": "document",
            "file_id": d.file_id,
            "size": getattr(d, "file_size", None),
            "mime": getattr(d, "mime_type", None),
            "name": _safe_name(getattr(d, "file_name", None)),
        }
    if getattr(msg, "audio", None):
        a = msg.audio
        return {
            "kind": "audio",
            "file_id": a.file_id,
            "size": getattr(a, "file_size", None),
            "mime": getattr(a, "mime_type", None),
            "name": _safe_name(getattr(a, "file_name", None)),
        }
    if getattr(msg, "video", None):
        v = msg.video
        return {
            "kind": "video",
            "file_id": v.file_id,
            "size": getattr(v, "file_size", None),
            "mime": getattr(v, "mime_type", None),
            "name": _safe_name(getattr(v, "file_name", None)),
        }
    if getattr(msg, "video_note", None):
        vn = msg.video_note
        return {
            "kind": "video_note",
            "file_id": vn.file_id,
            "size": getattr(vn, "file_size", None),
            "mime": None,
            "name": None,
        }
    if getattr(msg, "sticker", None):
        s = msg.sticker
        return {
            "kind": "sticker",
            "file_id": s.file_id,
            "size": getattr(s, "file_size", None),
            "mime": None,
            "name": None,
        }
    return None


async def _download_attachment(
    ctx: "ContextTypes.DEFAULT_TYPE",
    attachment: dict[str, Any],
    chat_id: str,
    base: Path,
) -> tuple[str | None, str | None]:
    """Download an attachment to <base>/attachments/<chat_id>/<file_id>.<ext>.

    Returns (local_path, error). On success error is None. On too-large the
    local_path is None and error='too_large'. On any other failure error is
    'download_failed'.
    """
    size = attachment.get("size")
    if size is not None and size > MAX_ATTACHMENT_BYTES:
        return None, "too_large"
    try:
        file = await ctx.bot.get_file(attachment["file_id"])
        # Telegram returns a path like "photos/file_1.jpg" — sniff the extension.
        file_path = getattr(file, "file_path", "") or ""
        raw_ext = file_path.rsplit(".", 1)[-1] if "." in file_path else ""
        ext = _safe_ext(raw_ext)
        out_dir = base / "attachments" / chat_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{attachment['file_id']}.{ext}"
        await file.download_to_drive(custom_path=str(out_path))
        return str(out_path), None
    except Exception as e:
        log(f"attachment download failed: {e}")
        return None, "download_failed"


HEARTBEAT_INTERVAL_S = 30 * 60  # 30 minutes


async def _heartbeat_loop(state: dict[str, Any]) -> None:
    """Every HEARTBEAT_INTERVAL_S emit a `[bot] heartbeat pid=... uptime=...` line.

    Includes total message count + undelivered queue depth so server.log
    gives one-shot visibility into the pipeline state.
    """
    db_path = state.get("db_path")
    started = state.get("started_at", time.time())
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
        except asyncio.CancelledError:
            raise
        try:
            msgs = 0
            undelivered = 0
            if db_path:
                conn = sqlite3.connect(db_path)
                try:
                    row = conn.execute("SELECT COUNT(*) FROM inbound").fetchone()
                    if row:
                        msgs = int(row[0])
                    row = conn.execute(
                        "SELECT COUNT(*) FROM inbound WHERE delivered = 0 AND gate_action = 'allow'"
                    ).fetchone()
                    if row:
                        undelivered = int(row[0])
                finally:
                    conn.close()
            uptime_s = int(time.time() - started)
            log(
                f"heartbeat pid={os.getpid()} uptime={uptime_s}s "
                f"msgs={msgs} undelivered={undelivered}"
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log(f"heartbeat error: {e}")


async def _approved_poller(app: "Application") -> None:
    """Port of server.ts:346-368 — poll approved/ for pairing completions.

    The /telegram:access skill drops a file at approved/<senderId> after the
    user approves a pairing in Claude Code. For DMs senderId == chatId, so
    the filename is the destination chat.
    """
    approved_dir = _state_dir() / "approved"
    while True:
        try:
            try:
                entries = list(approved_dir.iterdir())
            except FileNotFoundError:
                entries = []
            for entry in entries:
                sender_id = entry.name
                try:
                    await app.bot.send_message(
                        chat_id=int(sender_id),
                        text="Paired! Say hi to Claude.",
                    )
                except Exception as e:
                    log(f"failed to send approval confirm to {sender_id}: {e}")
                # Remove regardless of send outcome — don't loop on a broken send.
                try:
                    entry.unlink()
                except Exception as e:
                    log(f"failed to unlink approved/{sender_id}: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log(f"approved poller error: {e}")
        await asyncio.sleep(5)


async def cmd_start(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
    """Port of server.ts:711-725 — DM-only welcome + pairing instructions."""
    msg = update.effective_message
    if msg is None or msg.chat.type != "private":
        return
    access = load_access()
    if access["dmPolicy"] == "disabled":
        await ctx.bot.send_message(
            chat_id=msg.chat.id,
            text="This bot isn't accepting new connections.",
        )
        return
    await ctx.bot.send_message(
        chat_id=msg.chat.id,
        text=(
            "This bot bridges Telegram to a Claude Code session.\n\n"
            "To pair:\n"
            "1. DM me anything — you'll get a 6-char code\n"
            "2. In Claude Code: /telegram:access pair <code>\n\n"
            "After that, DMs here reach that session."
        ),
    )


async def cmd_help(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
    """Port of server.ts:727-735 — DM-only help text."""
    msg = update.effective_message
    if msg is None or msg.chat.type != "private":
        return
    await ctx.bot.send_message(
        chat_id=msg.chat.id,
        text=(
            "Messages you send here route to a paired Claude Code session. "
            "Text and photos are forwarded; replies and reactions come back.\n\n"
            "/start — pairing instructions\n"
            "/status — check your pairing state"
        ),
    )


async def cmd_status(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
    """Port of server.ts:737-760 — DM-only pairing status.

    Augmented with bot-local fields: username, uptime, allowlist size,
    bot.pid, undelivered row count (spec §Task 1.8).
    """
    msg = update.effective_message
    if msg is None or msg.chat.type != "private":
        return
    user = update.effective_user
    if user is None:
        return
    sender_id = str(user.id)
    access = load_access()

    if sender_id in access["allowFrom"]:
        name = f"@{user.username}" if user.username else sender_id
        state = ctx.application.bot_data.get("state", {})
        uptime_s = int(time.time() - state.get("started_at", time.time()))
        bot_username = state.get("bot_username", "")
        undelivered = 0
        try:
            db_path = state.get("db_path")
            if db_path:
                conn = sqlite3.connect(db_path)
                try:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM inbound WHERE delivered = 0 AND gate_action = 'allow'"
                    ).fetchone()
                    if row:
                        undelivered = int(row[0])
                finally:
                    conn.close()
        except Exception as e:
            log(f"/status undelivered query failed: {e}")
        await ctx.bot.send_message(
            chat_id=msg.chat.id,
            text=(
                f"Paired as {name}.\n\n"
                f"bot: @{bot_username}\n"
                f"uptime: {uptime_s}s\n"
                f"allowlist: {len(access['allowFrom'])} user(s)\n"
                f"pid: {os.getpid()}\n"
                f"undelivered: {undelivered}"
            ),
        )
        return

    for code, p in access["pending"].items():
        if p["senderId"] == sender_id:
            await ctx.bot.send_message(
                chat_id=msg.chat.id,
                text=f"Pending pairing — run in Claude Code:\n\n/telegram:access pair {code}",
            )
            return

    await ctx.bot.send_message(
        chat_id=msg.chat.id,
        text="Not paired. Send me a message to get a pairing code.",
    )


async def handle_any_message(
    update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"
) -> None:
    """Gate-eval + INSERT (or UPDATE for edits) for every inbound message event.

    Edits preserve the pre-edit text. python-telegram-bot surfaces edits via
    `update.edited_message` (with `update.message is None`); we detect that
    and route to the edit branch, which SELECTs the current row by
    (chat_id, message_id), moves `text` into `previous_text`, writes the
    new text, and bumps `edit_count` + `edited_at`. Only the most-recent
    pre-edit version is preserved — full history is out of scope.
    """
    # Distinguish edits from new messages. PTB surfaces edits as one of
    # `update.edited_message` (DM/group) or `update.edited_channel_post`
    # (broadcast channel). `update.message` / `update.channel_post` are the
    # fresh-message counterparts. `effective_message` collapses all four,
    # which is the right source for the content but NOT for deciding whether
    # this is an edit. We treat "the edit counterpart is set AND the fresh
    # counterpart is None" as the edit signal — guarding against weird
    # shapes where PTB might populate both (hasn't been observed, but the
    # cost of the extra `is None` check is zero).
    is_edit = (
        update.edited_message is not None and update.message is None
    ) or (
        update.edited_channel_post is not None and update.channel_post is None
    )
    msg = update.effective_message
    if msg is None:
        return
    user = update.effective_user
    evt = {
        "from_id": str(user.id) if user else "",
        "chat_id": str(msg.chat.id),
        "chat_type": msg.chat.type,
        "text": msg.text or msg.caption or "",
        "message_id": str(msg.message_id),
        "username": (user.username if user else "") or "",
        "ts": msg.date.isoformat() if msg.date else "",
    }
    gate_res = gate_message(evt)
    state = ctx.application.bot_data["state"]
    db = state["db"]

    # Classify message_type. Permission replies (e.g. "yes abcde") only count
    # once the sender has cleared the gate — mirrors server.ts which runs the
    # permission intercept *after* gate() returns deliver.
    message_type = "message"
    perm_match = None
    if gate_res["action"] == "allow":
        perm_match = PERMISSION_REPLY_RE.match(evt["text"] or "")
        if perm_match:
            message_type = "permission_reply"

    # Edit path: look up the existing row by (chat_id, message_id). If we
    # find one, UPDATE instead of INSERT so we preserve the prior `text` as
    # `previous_text` and bump counters. If we don't find one (bot was down
    # when the original was sent, or the DB was wiped), fall through to the
    # INSERT path — a fresh row for the edit is strictly better than dropping
    # the message entirely. In that fallback the row gets `edit_count = 1`
    # with `previous_text = NULL`, so Claude sees "edited, prior unseen" via
    # the server.ts meta — more informative than treating it as fresh.
    #
    # Re-delivery is gated on the CURRENT gate result: only bump delivered=0
    # when gate says 'allow'. If the sender's access was revoked between the
    # original message and the edit, we still UPDATE the row (preserving the
    # diff for post-hoc inspection) but do NOT reset `delivered` — that would
    # smuggle a newly-unauthorized edit through the allowlist because the
    # existing row's gate_action is 'allow'.
    row_id: int | None = None
    edited_at_iso: str | None = None
    if is_edit:
        edited_at_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        )
        existing = await db.execute_fetchall(
            "SELECT id, text FROM inbound WHERE chat_id = ? AND message_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (evt["chat_id"], evt["message_id"]),
        )
        if existing:
            prior_id, prior_text = existing[0]
            # Only re-deliver if the CURRENT gate still says allow. Drop/pair
            # leaves the row UPDATE-d (for audit) but delivered unchanged.
            reset_delivered = gate_res["action"] == "allow"
            await db.execute("BEGIN IMMEDIATE")
            if reset_delivered:
                await db.execute(
                    """UPDATE inbound
                       SET previous_text = ?,
                           text = ?,
                           edit_count = COALESCE(edit_count, 0) + 1,
                           edited_at = ?,
                           delivered = 0
                       WHERE id = ?""",
                    (prior_text, evt["text"], edited_at_iso, prior_id),
                )
            else:
                await db.execute(
                    """UPDATE inbound
                       SET previous_text = ?,
                           text = ?,
                           edit_count = COALESCE(edit_count, 0) + 1,
                           edited_at = ?
                       WHERE id = ?""",
                    (prior_text, evt["text"], edited_at_iso, prior_id),
                )
            await db.commit()
            row_id = int(prior_id)
            prev_len = len(prior_text or "")
            new_len = len(evt["text"] or "")
            delivery_tag = "re-delivering" if reset_delivered else f"gate={gate_res['action']} no-redeliver"
            log(
                f"inbound edit: {evt['username'] or evt['from_id']}: "
                f"msg_id={evt['message_id']} → row={row_id} "
                f"prev_len={prev_len} new_len={new_len} ({delivery_tag})"
            )

    if row_id is None:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            """INSERT INTO inbound
               (ts, chat_id, message_id, user_id, username, message_type, text,
                gate_action, edit_count, edited_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evt["ts"],
                evt["chat_id"],
                evt["message_id"],
                evt["from_id"],
                evt["username"],
                message_type,
                evt["text"],
                gate_res["action"],
                1 if is_edit else 0,
                edited_at_iso,
            ),
        )
        row_id = cur.lastrowid
        await db.commit()

    # Inner 👀 reaction FIRST — must hit Telegram before server.ts fires its
    # own setMessageReaction with 🫡. Telegram's free-tier API only allows
    # one reaction per bot per message: each call REPLACES the prior one.
    # By awaiting the 👀 call before notify_clients(), we guarantee that
    # 👀 lands first and server.ts's 🫡 overwrites it to become the final
    # visible state. If server.ts is dead/slow, the 👀 stays visible as a
    # "queued but not delivered" liveness signal.
    #
    # Permission replies get the outcome emoji (✔️/✖️) instead of the
    # generic ack — port of server.ts:977-981. Those don't get overwritten
    # by server.ts because the catchup code uses the generic outer reaction.
    if gate_res["action"] == "allow" and ReactionTypeEmoji is not None:
        try:
            if message_type == "permission_reply" and perm_match is not None:
                outcome = "✔️" if perm_match.group(1).lower().startswith("y") else "✖️"
                await ctx.bot.set_message_reaction(
                    chat_id=int(evt["chat_id"]),
                    message_id=int(evt["message_id"]),
                    reaction=[ReactionTypeEmoji(emoji=outcome)],
                )
            else:
                await ctx.bot.set_message_reaction(
                    chat_id=int(evt["chat_id"]),
                    message_id=int(evt["message_id"]),
                    reaction=[ReactionTypeEmoji(emoji=INNER_ACK_EMOJI)],
                )
        except Exception as e:
            log(f"reaction failed: {e}")

    # Wake up any connected server.ts clients — DB is the source of truth,
    # the socket is just a latency shortcut so they don't have to poll.
    # Determine if this message has an attachment — if so, DEFER
    # notify_clients() until after the attachment UPDATE below. Otherwise
    # server.ts wakes on the initial NULL-attachments row, marks it
    # delivered, and never re-reads once the UPDATE populates fields.
    # Race-condition fix: 2026-04-15.
    has_attachment = gate_res["action"] == "allow" and _extract_attachment(msg) is not None

    # Fired AFTER the inner reaction so the race with server.ts's outer
    # reaction is deterministic (see comment above). For attachment-bearing
    # messages, the attachment block below handles the wakeup after UPDATE.
    if not has_attachment:
        await notify_clients()
    log(
        f"inbound [{gate_res['action']}/{message_type}]: "
        f"{evt['username'] or evt['from_id']}: "
        f"{(evt['text'] or '')[:60]!r} → id={row_id}"
    )

    # Pairing flow — reply with the code (initial or resend). Port from
    # server.ts:947-952. Code + template are identical to current behavior.
    if gate_res["action"] == "pair":
        lead = "Still pending" if gate_res.get("isResend") else "Pairing required"
        try:
            await ctx.bot.send_message(
                chat_id=int(evt["chat_id"]),
                text=f"{lead} — run in Claude Code:\n\n/telegram:access pair {gate_res['code']}",
            )
        except Exception as e:
            log(f"pair reply failed: {e}")

    # Attachment pre-download — only for allow (spec §telegram_bot.py: don't
    # burn quota on drop/pair). On any error we still have the row, we just
    # set `error` so server.ts's download_attachment tool serves as fallback.
    if gate_res["action"] == "allow":
        attachment = _extract_attachment(msg)
        if attachment is not None:
            base_dir = state["base"]
            local_path, err = await _download_attachment(
                ctx, attachment, evt["chat_id"], base_dir
            )
            update_ok = False
            try:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute(
                    """UPDATE inbound
                       SET attachment_kind = ?,
                           attachment_path = ?,
                           attachment_file_id = ?,
                           attachment_size = ?,
                           attachment_mime = ?,
                           attachment_name = ?,
                           error = ?
                       WHERE id = ?""",
                    (
                        attachment["kind"],
                        local_path,
                        attachment["file_id"],
                        attachment.get("size"),
                        attachment.get("mime"),
                        attachment.get("name"),
                        err,
                        row_id,
                    ),
                )
                await db.commit()
                update_ok = True
            except Exception as e:
                log(f"attachment UPDATE failed: {e}")
            # Always notify — attachment UPDATE success populates the fields,
            # failure still means server.ts should deliver the row (with NULL
            # attachments) rather than silently drop. has_attachment branch
            # above deferred the initial notify; this is the gated wakeup.
            await notify_clients()
            if not update_ok:
                log(f"attachment UPDATE failed for row {row_id} — delivering with NULL attachments")


async def handle_callback_query(
    update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"
) -> None:
    """Handle inline-keyboard button presses for permission requests.

    Port of server.ts:765-819. callback_data format is
    `perm:(allow|deny|more):<request_id>` where request_id is 5 chars [a-km-z].

    Behavior split with server.ts:
      - allow/deny: we answerCallbackQuery immediately (UX), then INSERT a
        row for server.ts to route to MCP. server.ts also edits the message
        text to show the outcome.
      - more: we answerCallbackQuery + INSERT; server.ts holds the permission
        details in pendingPermissions and is responsible for editMessageText.
    """
    cq = update.callback_query
    if cq is None or cq.data is None:
        return
    data = cq.data
    m = re.match(r"^perm:(allow|deny|more):([a-km-z]{5})$", data)
    if not m:
        try:
            await cq.answer()
        except Exception:
            pass
        return

    access = load_access()
    user = update.effective_user
    sender_id = str(user.id) if user else ""
    if sender_id not in access["allowFrom"]:
        try:
            await cq.answer(text="Not authorized.")
        except Exception:
            pass
        return

    behavior, request_id = m.group(1), m.group(2)

    # Write the event to SQLite first — server.ts reads this row to route the
    # MCP notification. Even "more" flows through the DB so catch-up is correct.
    state = ctx.application.bot_data.get("state", {})
    db = state.get("db")
    chat_id = ""
    message_id = ""
    if cq.message is not None:
        chat_id = str(cq.message.chat.id)
        message_id = str(cq.message.message_id)
    ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
    username = (user.username if user else "") or ""

    row_id: int | None = None
    if db is not None:
        try:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """INSERT INTO inbound
                   (ts, chat_id, message_id, user_id, username, message_type, text,
                    callback_data, gate_action)
                   VALUES (?, ?, ?, ?, ?, 'callback_query', ?, ?, 'allow')""",
                (
                    ts,
                    chat_id,
                    message_id,
                    sender_id,
                    username,
                    "",
                    data,
                ),
            )
            row_id = cur.lastrowid
            await db.commit()
            await notify_clients()
        except Exception as e:
            log(f"callback_query INSERT failed: {e}")

    # UX: acknowledge the button press. For allow/deny, surface the outcome
    # label — matches current server.ts text.
    try:
        if behavior == "more":
            await cq.answer()
        else:
            label = "✅ Allowed" if behavior == "allow" else "❌ Denied"
            await cq.answer(text=label)
    except Exception as e:
        log(f"answerCallbackQuery failed: {e}")

    log(f"callback_query [{behavior}:{request_id}] from {sender_id} → id={row_id}")


if __name__ == "__main__":
    main()
