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
    """Extract all <channel source="plugin:telegram:telegram" ...> blocks."""
    pattern = re.compile(
        r'<channel\s+source="plugin:telegram:telegram"'
        r'(?:\s+chat_id="(?P<chat_id>[^"]*)")?'
        r'(?:\s+message_id="(?P<message_id>[^"]*)")?'
        r'(?:\s+user="(?P<user>[^"]*)")?'
        r'(?:\s+user_id="(?P<user_id>[^"]*)")?'
        r'(?:\s+ts="(?P<ts>[^"]*)")?'
        r'(?:\s+(?:image_path|attachment_kind|attachment_file_id|attachment_size|attachment_mime)="[^"]*")*'
        r"\s*>"
        r"(?P<body>.*?)"
        r"</channel>",
        re.DOTALL,
    )

    messages = []
    for match in pattern.finditer(text):
        msg = {
            "chat_id": match.group("chat_id"),
            "message_id": match.group("message_id"),
            "user": match.group("user"),
            "user_id": match.group("user_id"),
            "ts": match.group("ts"),
            "text": match.group("body").strip(),
        }
        # Extract optional attributes with a broader search on the tag
        tag_text = match.group(0)
        img = re.search(r'image_path="([^"]*)"', tag_text)
        if img:
            msg["image_path"] = img.group(1)
        att_kind = re.search(r'attachment_kind="([^"]*)"', tag_text)
        if att_kind:
            msg["attachment_kind"] = att_kind.group(1)
        att_file = re.search(r'attachment_file_id="([^"]*)"', tag_text)
        if att_file:
            msg["attachment_file_id"] = att_file.group(1)
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
        # Check for duplicate (same message_id already logged)
        if msg.get("message_id"):
            existing = conn.execute(
                "SELECT id FROM messages WHERE message_id = ? AND direction = 'inbound'",
                (msg["message_id"],),
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
