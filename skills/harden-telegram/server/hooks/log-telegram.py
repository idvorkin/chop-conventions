#!/usr/bin/env python3
"""Log all Telegram tool calls (inbound reads + outbound sends) to SQLite."""

import json
import os
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
            direction TEXT NOT NULL,  -- 'outbound' for reply/edit/react, 'download' for attachments
            tool_name TEXT NOT NULL,
            chat_id TEXT,
            message_id TEXT,
            reply_to TEXT,
            text TEXT,
            files TEXT,  -- JSON array of file paths
            format TEXT,
            tool_response TEXT,  -- raw JSON of the response
            tool_input_raw TEXT  -- raw JSON of the input for debugging
        )
    """)
    conn.commit()


def main():
    data = json.load(sys.stdin)

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", {})
    session_id = data.get("session_id", "")

    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    init_db(conn)

    now = datetime.now(timezone.utc).isoformat()

    if "reply" in tool_name or "edit_message" in tool_name:
        direction = "outbound"
        conn.execute(
            """INSERT INTO messages
               (timestamp, session_id, direction, tool_name, chat_id, message_id, reply_to, text, files, format, tool_response, tool_input_raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now,
                session_id,
                direction,
                tool_name,
                tool_input.get("chat_id"),
                None,  # message_id comes from response
                tool_input.get("reply_to"),
                tool_input.get("text"),
                json.dumps(tool_input.get("files"))
                if tool_input.get("files")
                else None,
                tool_input.get("format"),
                json.dumps(tool_response) if tool_response else None,
                json.dumps(tool_input),
            ),
        )
    elif "react" in tool_name:
        direction = "outbound"
        conn.execute(
            """INSERT INTO messages
               (timestamp, session_id, direction, tool_name, chat_id, message_id, reply_to, text, tool_response, tool_input_raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now,
                session_id,
                direction,
                tool_name,
                tool_input.get("chat_id"),
                tool_input.get("message_id"),
                None,
                tool_input.get("emoji"),
                json.dumps(tool_response) if tool_response else None,
                json.dumps(tool_input),
            ),
        )
    elif "download" in tool_name:
        direction = "download"
        conn.execute(
            """INSERT INTO messages
               (timestamp, session_id, direction, tool_name, text, tool_response, tool_input_raw)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                now,
                session_id,
                direction,
                tool_name,
                tool_input.get("file_id"),
                json.dumps(tool_response) if tool_response else None,
                json.dumps(tool_input),
            ),
        )
    else:
        # Catch-all for any other telegram tools
        conn.execute(
            """INSERT INTO messages
               (timestamp, session_id, direction, tool_name, tool_response, tool_input_raw)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                now,
                session_id,
                "unknown",
                tool_name,
                json.dumps(tool_response) if tool_response else None,
                json.dumps(tool_input),
            ),
        )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
