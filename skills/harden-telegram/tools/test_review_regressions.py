"""Hermetic diagnostic and recovery regressions."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent))
import telegram_debug as debug
import watchdog


def test_unresolved_pane_never_uses_active_pane(monkeypatch):
    monkeypatch.setattr(watchdog, "resolve_pane_for_pid", lambda _: None)
    active = Mock(side_effect=AssertionError("active pane lookup"))
    monkeypatch.setattr(watchdog, "tmux_active_pane", active)
    assert watchdog.detect_tmux_pane() == ""
    active.assert_not_called()


def test_recovery_requires_ownership_idle_and_new_bridge(monkeypatch):
    send = Mock(return_value=True)
    monkeypatch.setattr(watchdog, "tmux_send_keys", send)
    monkeypatch.setattr(watchdog, "claude_for_pane", lambda _: None)
    assert not watchdog.do_recovery("%1")
    send.assert_not_called()
    monkeypatch.setattr(watchdog, "claude_for_pane", lambda _: 100)
    monkeypatch.setattr(watchdog, "bridge_pids", lambda _: {200})
    monkeypatch.setattr(watchdog, "wait_for_idle_prompt", lambda *a, **kw: True)
    wait = Mock(return_value=None)
    monkeypatch.setattr(watchdog, "wait_for_new_bun", wait)
    monkeypatch.setattr(watchdog, "tmux_capture_pane", lambda _: "Reloaded: old")
    assert not watchdog.do_recovery("%1")
    wait.assert_called_once_with(100, {200})
    wait.return_value = 201
    assert watchdog.do_recovery("%1")


def test_replacement_filters_preexisting_and_foreign_bridge(monkeypatch):
    monkeypatch.setattr(debug, "_find_telegram_bridge_pids", lambda: [1, 2, 3])
    monkeypatch.setattr(
        debug, "_find_owning_claude", lambda pid: 100 if pid in (1, 3) else 999
    )
    assert watchdog.wait_for_new_bun(100, {1}, timeout=1) == 3


def test_daemon_adopts_replacement_and_stays_alive(monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.setenv("WATCHDOG_BUN_PID", "200")
    monkeypatch.setenv("WATCHDOG_CLAUDE_PID", "100")
    monkeypatch.setenv("WATCHDOG_TMUX_PANE", "%1")
    monkeypatch.setattr(watchdog, "acquire_singleton", lambda: True)
    monkeypatch.setattr(watchdog, "cleanup_pid_file", lambda: None)
    monkeypatch.setattr(watchdog.signal, "signal", lambda *a: None)
    monkeypatch.setattr(watchdog.time, "sleep", lambda _: None)
    checked = []

    def alive(pid):
        checked.append(pid)
        if pid == 100:
            return checked.count(100) <= 2
        return pid == 201

    monkeypatch.setattr(watchdog, "is_pid_alive", alive)
    monkeypatch.setattr(watchdog, "resolve_pane_for_pid", lambda _: "%1")
    monkeypatch.setattr(watchdog, "do_recovery", lambda _: True)
    monkeypatch.setattr(watchdog, "bridge_pids", lambda _: {201})
    result = CliRunner().invoke(watchdog._build_app(), ["daemon"])
    assert result.exit_code == 0, result.output
    assert 201 in checked


def test_reload_failure_exit_status(monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.setattr(watchdog, "cmd_reload", lambda *a, **kw: False)
    assert (
        CliRunner().invoke(watchdog._build_app(), ["reload", "--pane", "%1"]).exit_code
        == 1
    )


def test_credentials_and_missing_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=file-token")
    assert debug._read_bot_token() == "file-token"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    assert debug._read_bot_token() == "env-token"
    monkeypatch.setattr(debug, "_find_telegram_bridge_pids", lambda: [])
    monkeypatch.setattr(debug, "_find_owning_claude", lambda _: 100)
    report = debug.DoctorReport()
    debug._doctor_check_server_ts(report)
    assert report.failures == 1


def test_inbound_hook_source_attribute_order():
    path = Path(__file__).parent.parent / "server/hooks/log-telegram-inbound.py"
    spec = importlib.util.spec_from_file_location("inbound_hook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (
        module.extract_channel_messages(
            '<channel chat_id="1" source="plugin:telegram:telegram" message_id="2">hello</channel>'
        )[0]["text"]
        == "hello"
    )
    assert (
        module.extract_channel_messages('<channel source="other">hello</channel>') == []
    )


def test_24h_count_normalizes_iso_timestamps(tmp_path, monkeypatch):
    import sqlite3
    from datetime import datetime, timedelta, timezone

    db = tmp_path / "log.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE messages (id INTEGER, timestamp TEXT, direction TEXT, text TEXT, chat_id TEXT, tool_name TEXT)"
    )
    now = datetime.now(timezone.utc)
    for i, delta in enumerate((timedelta(hours=1), timedelta(hours=30))):
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, 'in', 'test', '42', 'test')",
            (i, (now - delta).isoformat()),
        )
    conn.commit()
    conn.close()
    monkeypatch.setattr(debug, "LOG_DB", db)
    result = debug.check_telegram_db()
    assert result["recent_24h"] == 1
