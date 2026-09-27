"""Authorization regressions; all state and Telegram calls are isolated."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import telegram_bot as bot


def policy(tmp_path, monkeypatch, **overrides):
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LARRY_TELEGRAM_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "_static_access", None)
    access = {
        "dmPolicy": "allowlist",
        "allowFrom": ["42"],
        "groups": {"-1": {"requireMention": True}},
        "pending": {},
    }
    access.update(overrides)
    (tmp_path / "access.json").write_text(json.dumps(access))
    return access


def test_static_snapshot_downgrades_pairing_and_never_writes(tmp_path, monkeypatch):
    policy(tmp_path, monkeypatch, dmPolicy="pairing")
    monkeypatch.setenv("TELEGRAM_ACCESS_MODE", "static")
    bot.initialize_access()
    (tmp_path / "access.json").write_text(json.dumps({"allowFrom": ["99"]}))
    monkeypatch.setattr(
        bot, "save_access", lambda _: (_ for _ in ()).throw(AssertionError("write"))
    )
    assert (
        bot.gate_message({"from_id": "99", "chat_id": "99", "chat_type": "private"})[
            "action"
        ]
        == "drop"
    )
    assert (
        bot.gate_message({"from_id": "42", "chat_id": "42", "chat_type": "private"})[
            "action"
        ]
        == "allow"
    )


def test_base_dir_is_passed_to_runtime(tmp_path, monkeypatch):
    import os

    monkeypatch.setattr(sys, "argv", ["bot", "--base-dir", str(tmp_path)])
    monkeypatch.setattr(bot, "acquire_singleton", lambda path: None)
    seen = []

    async def run(base):
        seen.append(base)
        assert bot._log_path() == base / "server.log"
        assert os.environ["LARRY_TELEGRAM_DIR"] == str(base)

    monkeypatch.setattr(bot, "run", run)
    monkeypatch.setenv("LARRY_TELEGRAM_DIR", "/unused")
    bot.main()
    assert seen == [tmp_path]


def test_handler_group_gate_and_permission_classification(tmp_path, monkeypatch):
    policy(tmp_path, monkeypatch)
    monkeypatch.setattr(bot, "notify_clients", AsyncMock())
    monkeypatch.setattr(bot, "_extract_attachment", lambda _: None)
    monkeypatch.setattr(bot, "ReactionTypeEmoji", None)

    async def exercise(chat_type, text, entities=()):
        db = NS(execute=AsyncMock(return_value=NS(lastrowid=1)), commit=AsyncMock())
        msg = NS(
            chat=NS(id=-1 if chat_type == "group" else 42, type=chat_type),
            text=text,
            caption=None,
            message_id=1,
            date=None,
            entities=entities,
            caption_entities=(),
            reply_to_message=None,
        )
        ctx = NS(
            application=NS(
                bot_data={"state": {"db": db, "bot_username": "testbot", "bot_id": 7}}
            ),
            bot=NS(),
        )
        await bot.handle_any_message(
            NS(effective_message=msg, effective_user=NS(id=42, username="test")), ctx
        )
        insert = next(
            call for call in db.execute.call_args_list if "INSERT" in call.args[0]
        )
        return insert.args[1]

    assert asyncio.run(exercise("group", "yes abcde"))[-1] == "drop"
    values = asyncio.run(exercise("private", "yes abcde"))
    assert values[5] == "permission_reply"
    # An explicitly unrestricted group may deliver chat, but never permissions.
    access = bot.load_access()
    access["groups"]["-1"]["requireMention"] = False
    bot.save_access(access)
    values = asyncio.run(exercise("group", "yes abcde"))
    assert values[5] == "message" and values[-1] == "allow"
    access["groups"]["-1"]["requireMention"] = True
    bot.save_access(access)
    values = asyncio.run(
        exercise("group", "@testbot hello", [NS(type="mention", offset=0, length=8)])
    )
    assert values[-1] == "allow"


def test_mentions_accept_reply_and_utf16_caption():
    msg = NS(
        text=None,
        caption="😀 @testbot",
        caption_entities=[NS(type="mention", offset=3, length=8)],
    )
    assert bot.mentions_bot(msg, "testbot", 7)
    assert not bot.mentions_bot(msg, "other", 7)
    msg.reply_to_message = NS(from_user=NS(id=7))
    assert bot.mentions_bot(msg, "other", 7)
