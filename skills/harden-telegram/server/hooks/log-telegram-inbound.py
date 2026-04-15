#!/usr/bin/env python3
"""Log inbound Telegram messages to SQLite. Runs as a UserPromptSubmit hook."""

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_DIR = Path(
    os.path.expanduser(os.environ.get("LARRY_TELEGRAM_DIR", "~/larry-telegram"))
)
DB_PATH = DB_DIR / "telegram_log.db"


def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            session_id TEXT,
            direction TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            chat_id TEXT,
            message_id TEXT,
            reply_to TEXT,
            text TEXT,
            files TEXT,
            format TEXT,
            tool_response TEXT,
            tool_input_raw TEXT
        )
    """)
    conn.commit()


def extract_channel_messages(text: str) -> list[dict]:
    """Extract all <channel source="plugin:telegram:telegram" ...> blocks.

    Attribute parsing is order-agnostic: we match the opening tag as a blob
    and then pull named attributes out of it via a second regex pass. This
    avoids silent drops when an upstream writer reorders tag attributes.
    """
    pattern = re.compile(
        r'<channel\s+source="plugin:telegram:telegram"'
        r'(?P<attrs>[^>]*)>'
        r"(?P<body>.*?)"
        r"</channel>",
        re.DOTALL,
    )

    messages = []
    for match in pattern.finditer(text):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', match.group("attrs")))
        msg = {
            "chat_id": attrs.get("chat_id"),
            "message_id": attrs.get("message_id"),
            "user": attrs.get("user"),
            "user_id": attrs.get("user_id"),
            "ts": attrs.get("ts"),
            "text": match.group("body").strip(),
        }
        # Optional attachment attributes — same attrs dict, no positional regex.
        if attrs.get("image_path"):
            msg["image_path"] = attrs["image_path"]
        if attrs.get("attachment_kind"):
            msg["attachment_kind"] = attrs["attachment_kind"]
        if attrs.get("attachment_file_id"):
            msg["attachment_file_id"] = attrs["attachment_file_id"]
        messages.append(msg)

    return messages


def main():
    data = json.load(sys.stdin)
    prompt = data.get("prompt", "")
    session_id = data.get("session_id", "")

    # Only process if there are telegram channel tags
    if 'source="plugin:telegram:telegram"' not in prompt:
        return

    messages = extract_channel_messages(prompt)
    if not messages:
        return

    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    init_db(conn)

    now = datetime.now(timezone.utc).isoformat()

    for msg in messages:
        # Dedupe by (chat_id, message_id). Telegram message_ids are only unique
        # within a chat — dedupe by message_id alone would silently drop
        # legitimate messages from different chats that share a message_id.
        if msg.get("chat_id") and msg.get("message_id"):
            existing = conn.execute(
                "SELECT id FROM messages WHERE chat_id = ? AND message_id = ? AND direction = 'inbound'",
                (msg["chat_id"], msg["message_id"]),
            ).fetchone()
            if existing:
                continue

        attachment_info = {}
        if msg.get("attachment_kind"):
            attachment_info["kind"] = msg["attachment_kind"]
        if msg.get("attachment_file_id"):
            attachment_info["file_id"] = msg["attachment_file_id"]
        if msg.get("image_path"):
            attachment_info["image_path"] = msg["image_path"]

        conn.execute(
            """INSERT INTO messages
               (timestamp, session_id, direction, tool_name, chat_id, message_id, text, files, tool_input_raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg.get("ts", now),
                session_id,
                "inbound",
                "telegram_inbound",
                msg.get("chat_id"),
                msg.get("message_id"),
                msg.get("text"),
                json.dumps(attachment_info) if attachment_info else None,
                json.dumps(msg),
            ),
        )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
