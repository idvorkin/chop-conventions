# Telegram runtime safety

The poller resolves `--base-dir` once for its lock, queue, socket, attachments,
and logs. `TELEGRAM_STATE_DIR` remains the credential directory. With
`TELEGRAM_ACCESS_MODE=static`, access policy is frozen at startup and pairing
is disabled until the process restarts.

Permission decisions are accepted only from allowlisted private-chat senders.
Groups with `requireMention` accept Telegram mention entities naming this bot
or replies to this bot; ordinary group conversation is dropped.

Unattended diagnostic sends require `TELEGRAM_ALERT_CHAT_ID` to name a positive
private chat ID currently present in `access.json`'s `allowFrom`. There is no
history-based recipient fallback. Explicit `--chat-id` remains available for
operator-directed sends. Credentials resolve from `TELEGRAM_BOT_TOKEN` first,
then `.env` under `TELEGRAM_STATE_DIR`.

The default diagnostic runs the current two-process doctor. A session missing
its bridge fails the check. Watchdog reload requires a verified, idle Claude
pane and a newly started Telegram bridge belonging to that session. It does
not use old scrollback to confirm recovery. Optional daemon mode keeps running
and adopts the replacement bridge; unsuccessful recovery retries on the next
check. All recovery tests mock process and terminal operations.
