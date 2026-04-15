"""Unit tests for telegram_bot.py — persistent Telegram poller."""

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# telegram_bot.py lives in the parent `server/` directory (vendored layout).
BOT = Path(__file__).resolve().parent.parent / "telegram_bot.py"

# Make telegram_bot importable without executing main()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_singleton_rejects_second_instance(tmp_path):
    """Second instance must exit non-zero when PID file is locked."""
    env = {"LARRY_TELEGRAM_DIR": str(tmp_path), "TELEGRAM_BOT_TOKEN": "dummy"}
    p1 = subprocess.Popen(
        [sys.executable, str(BOT), "--dry-run-singleton"],
        env={**env, "PATH": "/usr/bin:/bin"},
    )
    time.sleep(0.5)  # let p1 acquire the lock
    p2 = subprocess.run(
        [sys.executable, str(BOT), "--dry-run-singleton"],
        env={**env, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        timeout=5,
    )
    p1.terminate()
    p1.wait(timeout=5)
    assert p2.returncode != 0
    assert b"already running" in p2.stderr.lower()


def test_init_db_creates_schema(tmp_path):
    from telegram_bot import init_db_sync

    db_path = tmp_path / "inbound.db"
    init_db_sync(db_path)
    conn = sqlite3.connect(db_path)
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "inbound" in tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(inbound)")}
    required = {
        "id",
        "ts",
        "chat_id",
        "message_id",
        "user_id",
        "username",
        "message_type",
        "text",
        "attachment_kind",
        "attachment_path",
        "attachment_file_id",
        "attachment_size",
        "attachment_mime",
        "attachment_name",
        "callback_data",
        "gate_action",
        "delivered",
        "error",
    }
    assert required.issubset(cols), f"missing: {required - cols}"
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_access_load_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(tmp_path))
    import importlib

    import telegram_bot

    importlib.reload(telegram_bot)
    a = telegram_bot.load_access()
    assert a["dmPolicy"] == "pairing"
    assert a["allowFrom"] == []
    assert a["groups"] == {}
    assert a["pending"] == {}


def test_access_save_atomic(tmp_path, monkeypatch):
    import json as _json

    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(tmp_path))
    import importlib

    import telegram_bot

    importlib.reload(telegram_bot)
    acc = {
        "dmPolicy": "allowlist",
        "allowFrom": ["42"],
        "groups": {},
        "pending": {},
    }
    telegram_bot.save_access(acc)
    written = _json.loads((tmp_path / "access.json").read_text())
    assert written == acc
    # no leftover .tmp
    assert not (tmp_path / "access.json.tmp").exists()


def test_gate_dm_allowlisted(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(tmp_path))
    import importlib

    import telegram_bot

    importlib.reload(telegram_bot)
    telegram_bot.save_access(
        {
            "dmPolicy": "allowlist",
            "allowFrom": ["42"],
            "groups": {},
            "pending": {},
        }
    )
    evt = {
        "from_id": "42",
        "chat_id": "42",
        "chat_type": "private",
        "text": "hi",
    }
    res = telegram_bot.gate_message(evt)
    assert res["action"] == "allow"


def test_gate_dm_dropped_not_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(tmp_path))
    import importlib

    import telegram_bot

    importlib.reload(telegram_bot)
    telegram_bot.save_access(
        {
            "dmPolicy": "allowlist",
            "allowFrom": ["42"],
            "groups": {},
            "pending": {},
        }
    )
    evt = {
        "from_id": "99",
        "chat_id": "99",
        "chat_type": "private",
        "text": "hi",
    }
    assert telegram_bot.gate_message(evt)["action"] == "drop"


def test_gate_dm_pair_generates_code(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(tmp_path))
    import importlib

    import telegram_bot

    importlib.reload(telegram_bot)
    telegram_bot.save_access(
        {
            "dmPolicy": "pairing",
            "allowFrom": [],
            "groups": {},
            "pending": {},
        }
    )
    evt = {
        "from_id": "42",
        "chat_id": "42",
        "chat_type": "private",
        "text": "hi",
    }
    res = telegram_bot.gate_message(evt)
    assert res["action"] == "pair"
    assert len(res["code"]) == 6
    # code persisted
    assert res["code"] in telegram_bot.load_access()["pending"]


def test_gate_dm_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(tmp_path))
    import importlib

    import telegram_bot

    importlib.reload(telegram_bot)
    telegram_bot.save_access(
        {
            "dmPolicy": "disabled",
            "allowFrom": ["42"],
            "groups": {},
            "pending": {},
        }
    )
    evt = {
        "from_id": "42",
        "chat_id": "42",
        "chat_type": "private",
        "text": "hi",
    }
    assert telegram_bot.gate_message(evt)["action"] == "drop"


def test_gate_group_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(tmp_path))
    import importlib

    import telegram_bot

    importlib.reload(telegram_bot)
    telegram_bot.save_access(
        {
            "dmPolicy": "pairing",
            "allowFrom": [],
            "groups": {"-100500": {"requireMention": False, "allowFrom": []}},
            "pending": {},
        }
    )
    evt = {
        "from_id": "42",
        "chat_id": "-100500",
        "chat_type": "group",
        "text": "hi",
    }
    res = telegram_bot.gate_message(evt)
    assert res["action"] == "allow"
    assert res["require_mention"] is False


def test_gate_pair_resend_cap(tmp_path, monkeypatch):
    """After 2 replies for the same sender, further messages are dropped."""
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(tmp_path))
    import importlib

    import telegram_bot

    importlib.reload(telegram_bot)
    telegram_bot.save_access(
        {
            "dmPolicy": "pairing",
            "allowFrom": [],
            "groups": {},
            "pending": {},
        }
    )
    evt = {
        "from_id": "42",
        "chat_id": "42",
        "chat_type": "private",
        "text": "hi",
    }
    # First message → initial pair, replies=1
    r1 = telegram_bot.gate_message(evt)
    assert r1["action"] == "pair"
    assert r1["isResend"] is False
    # Second message → resend, replies=2
    r2 = telegram_bot.gate_message(evt)
    assert r2["action"] == "pair"
    assert r2["isResend"] is True
    # Third → drop
    r3 = telegram_bot.gate_message(evt)
    assert r3["action"] == "drop"


def test_persist_inbound_sync_writes_row(tmp_path):
    import importlib

    import telegram_bot

    importlib.reload(telegram_bot)

    db_path = tmp_path / "inbound.db"
    telegram_bot.init_db_sync(db_path)

    evt = {
        "from_id": "42",
        "chat_id": "42",
        "chat_type": "private",
        "text": "hello world",
        "message_id": "123",
        "username": "igor",
        "ts": "2026-04-12T16:30:00+00:00",
    }
    gate_res = {"action": "allow"}

    row_id = telegram_bot.persist_inbound_sync(db_path, evt, gate_res, "message")
    assert isinstance(row_id, int)
    assert row_id > 0

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT * FROM inbound WHERE id = ?", (row_id,)).fetchone()
    conn.close()
    assert row is not None


def test_read_env_token_env_wins(tmp_path, monkeypatch):
    """Real env vars win over .env file."""
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=file-token\n")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    import importlib

    import telegram_bot

    importlib.reload(telegram_bot)
    assert telegram_bot.read_env_token() == "env-token"


def test_read_env_token_reads_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=file-token\n")
    import importlib

    import telegram_bot

    importlib.reload(telegram_bot)
    assert telegram_bot.read_env_token() == "file-token"


def test_socket_notify_writes_newline(tmp_path):
    import socket
    import time as _time

    import telegram_bot

    sock_path = tmp_path / "bot.sock"
    server_thread, stop = telegram_bot.start_socket_server_sync(sock_path)
    try:
        # Wait for socket file to appear
        for _ in range(50):
            if sock_path.exists():
                break
            _time.sleep(0.05)
        assert sock_path.exists(), "socket file was not created"

        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(sock_path))
        # Small wait to ensure the server has registered the client
        _time.sleep(0.1)
        telegram_bot.notify_clients_sync()
        client.settimeout(2)
        data = client.recv(16)
        assert data == b"\n"
        client.close()
    finally:
        stop()
        server_thread.join(timeout=2)


def test_permission_reply_regex():
    import telegram_bot

    assert telegram_bot.PERMISSION_REPLY_RE.match("yes abcde")
    assert telegram_bot.PERMISSION_REPLY_RE.match("y abcde")
    assert telegram_bot.PERMISSION_REPLY_RE.match("no abcde")
    assert telegram_bot.PERMISSION_REPLY_RE.match("n abcde")
    assert telegram_bot.PERMISSION_REPLY_RE.match("YES ABCDE")
    assert telegram_bot.PERMISSION_REPLY_RE.match(" yes abcde ")
    # Case-insensitive but only 5 lowercase letters a-km-z accepted on the
    # ID — l is excluded to avoid 1-vs-l confusion.
    m = telegram_bot.PERMISSION_REPLY_RE.match("yes ABCDE")
    assert m is not None
    # Must NOT match: bare yes/no, wrong ID length, ID with 'l'
    assert telegram_bot.PERMISSION_REPLY_RE.match("yes") is None
    assert telegram_bot.PERMISSION_REPLY_RE.match("yes abcdef") is None
    assert telegram_bot.PERMISSION_REPLY_RE.match("yes abcd") is None
    assert telegram_bot.PERMISSION_REPLY_RE.match("yes able1") is None
    # l excluded
    assert telegram_bot.PERMISSION_REPLY_RE.match("yes ablem") is None


class _FakePhoto:
    def __init__(self, file_id, size):
        self.file_id = file_id
        self.file_size = size


class _FakeDoc:
    def __init__(self):
        self.file_id = "doc_file"
        self.file_size = 1234
        self.mime_type = "application/pdf"
        self.file_name = "bad<name>.pdf"


class _FakeMessage:
    def __init__(self, photo=None, document=None):
        self.photo = photo
        self.document = document
        self.voice = None
        self.audio = None
        self.video = None
        self.video_note = None
        self.sticker = None


def test_extract_attachment_photo_picks_largest():
    import telegram_bot

    photos = [
        _FakePhoto("small", 100),
        _FakePhoto("medium", 500),
        _FakePhoto("big", 9999),
    ]
    msg = _FakeMessage(photo=photos)
    att = telegram_bot._extract_attachment(msg)
    assert att is not None
    assert att["kind"] == "photo"
    assert att["file_id"] == "big"
    assert att["size"] == 9999


def test_extract_attachment_document_sanitizes_name():
    import telegram_bot

    msg = _FakeMessage(document=_FakeDoc())
    att = telegram_bot._extract_attachment(msg)
    assert att is not None
    assert att["kind"] == "document"
    assert att["file_id"] == "doc_file"
    assert att["mime"] == "application/pdf"
    # angle brackets must be stripped
    assert "<" not in att["name"]
    assert ">" not in att["name"]


def test_extract_attachment_none():
    import telegram_bot

    msg = _FakeMessage()
    assert telegram_bot._extract_attachment(msg) is None


def test_safe_ext_strips_junk():
    import telegram_bot

    assert telegram_bot._safe_ext("jpg") == "jpg"
    assert telegram_bot._safe_ext("jpg/../evil") == "jpgevil"
    assert telegram_bot._safe_ext(None) == "bin"
    assert telegram_bot._safe_ext("") == "bin"
    assert telegram_bot._safe_ext("...") == "bin"


def test_log_rotation(tmp_path, monkeypatch):
    """When server.log exceeds 5MB, it's renamed to .1 and a new file starts."""
    import importlib

    monkeypatch.setenv("LARRY_TELEGRAM_DIR", str(tmp_path))
    import telegram_bot

    importlib.reload(telegram_bot)
    log_file = tmp_path / "server.log"
    # Pre-write > 5MB to trigger rotation on next log() call
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_bytes(b"x" * (telegram_bot._LOG_MAX_BYTES + 1))
    telegram_bot.log("rotation trigger")
    # After rotate, .1 should exist and the main file should be small.
    assert (tmp_path / "server.log.1").exists()
    assert log_file.stat().st_size < 1024  # just the fresh line
    assert b"rotation trigger" in log_file.read_bytes()


def test_log_writes_line(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("LARRY_TELEGRAM_DIR", str(tmp_path))
    import telegram_bot

    importlib.reload(telegram_bot)
    telegram_bot.log("hello from test")
    content = (tmp_path / "server.log").read_text()
    assert "[bot] hello from test" in content
    # Leading ISO timestamp in brackets
    assert content.startswith("[")
