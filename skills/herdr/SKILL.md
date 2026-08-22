---
name: herdr
description: Drive herdr (the terminal agent multiplexer) headlessly — create workspaces, start/prompt/wait-on coding agents in panes, read their output, and keep the claude/codex integrations healthy. Use when asked to create herdr workspaces or sessions, run or babysit another agent in a pane, check why an agent's status looks wrong, or install/repair herdr agent integrations.
allowed-tools: Bash, Read
---

# herdr — Drive the Agent Multiplexer

Create workspaces, run agents in panes, and keep integrations healthy — all via
`herdr`'s CLI, which wraps its Unix-socket API. Tiers:

| Invocation                   | Scope                                                            |
| ---------------------------- | ---------------------------------------------------------------- |
| `/herdr`                     | Health check — server/protocol, integration status, session refs |
| `/herdr new <repo> [text]`   | Create a workspace, start a claude agent, optionally prompt it   |
| `/herdr drive <pane> <text>` | Prompt an existing agent pane, wait, report its answer           |
| `/herdr integrations`        | Check + repair the claude/codex integration hooks                |

## Concept model

```text
session (one server + one socket; default ~/.config/herdr/herdr.sock)
└── workspace  w1         — tmux-session analog; has cwd + label
    └── tab    w1:t1      — tmux-window analog
        └── pane w1:p1    — a real PTY; an "agent" IS a pane whose foreground
                            process herdr detected as a known CLI agent
```

- **Agent targets are pane IDs** (`w6:p1`) or a name set via `agent start <name>` /
  `agent rename`. Workspace IDs and labels are NOT valid agent targets.
- Pane and tab numbers are **workspace-scoped** (`w5:p1`, `w5:t1`); a pane's tab
  lives in its `tab_id` field, not in the pane id.
- `agent_status` rolls up: `herdr workspace list` alone shows whether anything in
  a workspace needs attention.
- Inside a herdr-managed pane these env vars exist: `HERDR_ENV=1`,
  `HERDR_PANE_ID`, `HERDR_TAB_ID`, `HERDR_WORKSPACE_ID`, `HERDR_SOCKET_PATH`.
  Probe with `[ "${HERDR_ENV:-}" = 1 ]`; self-locate with `$HERDR_PANE_ID`, or
  `herdr pane current` — since 0.8.2 that resolves the _calling_ pane instead of
  another client's focused pane, so it stays correct after a pane is moved.
- Extra sessions are separate servers with their own sockets
  (`~/.config/herdr/sessions/<name>/herdr.sock`). List with
  `herdr session list --json`; target one with `--session <name>`. There is no
  API method to create a session — `herdr --session <name>` (interactive) does.

## Safety rules

- **NEVER run bare `herdr` from an agent** — it launches a blocking TUI. (herdr
  also refuses to launch inside its own pane, gated on `HERDR_ENV`.)
- **Pass `--no-focus`** on every `workspace create` / `tab create` /
  `pane split` / `worktree create` in automation — don't yank the human's view.
- **Never close, stop, or delete workspaces/panes/sessions you didn't create**
  unless explicitly asked. Read-only commands (`list`, `get`, `read`, `status`,
  `explain`, `api snapshot`) are always safe.
- **Always pass `--timeout <ms>`** to `agent wait`, `agent prompt --wait`, and
  `pane wait-output` — all three block forever without it.

## Recipe: workspace → agent → prompt → read → clean up

```bash
# 1. Create a workspace; capture the root pane id from the JSON envelope
PANE=$(herdr workspace create --cwd ~/gits/myrepo --label myrepo --no-focus \
       | jq -r .result.root_pane.pane_id)                      # e.g. "w6:p1"

# 2. Start an agent in that pane. Since 0.8.2 `agent start` waits for the pane
#    shell and any first-run agent prompt to be ready instead of racing them,
#    returning only once herdr sees the agent ready (30s default startup
#    timeout). If the agent is blocked during startup it returns
#    `agent_not_ready` immediately, but the name still works for `agent read`
#    and `agent send-keys` — wait for idle before prompting.
#    `herdr agent start --help` is the authority on --kind (22 values in 0.8.2:
#    claude, codex, gemini, cursor, copilot, opencode, qwen, grok, amp, ...).
#    Agent CLI args go after --  e.g. `... -- --model opus`
herdr agent start myagent --kind claude --pane "$PANE" --timeout 60000

# 3. Make sure it's not mid-turn, then prompt and wait for the turn to finish
herdr agent wait "$PANE" --until idle --until done --timeout 60000
herdr agent prompt "$PANE" "summarize the repo layout" \
  --wait --until idle --until done --until blocked --timeout 600000

# 4. Read the answer (raw text, no JSON envelope)
herdr agent read "$PANE" --source recent-unwrapped --lines 200

# 5. Clean up — only what you created
herdr workspace close w6
```

Worktree-backed variant: `herdr worktree create --cwd ~/gits/myrepo
--branch feature/x --base main --no-focus --json` returns `worktree_created`
with `workspace`/`tab`/`root_pane`/`worktree`; remove with
`herdr worktree remove --workspace w6 --force`.

Plain shell work in a pane (no agent): `herdr pane run <pane> <command>`, then
`herdr pane wait-output <pane> --regex '<done-marker>' --timeout 60000` and
`herdr pane read <pane> --lines 100`. Both accept `--flag=value` and take
options before or after the pane ID (0.8.2).

Moving things: **there is no cross-workspace tab move.** `herdr tab` is only
list/create/get/focus/rename/close, and the API's `tab_move` reorders within one
workspace (`insert_index`). To move a tab elsewhere, move its pane:
`herdr pane move <pane> --new-workspace [--label X]`, or `--workspace <id>`, or
`--tab <id> --split right|down`. A single-pane tab moves whole — its old tab is
closed, and the `pane_move` result reports the `previous_*` and `created_*` ids.

## Agent states and waiting

States: **`idle | working | blocked | done | unknown`**. `working` = mid-turn;
`done` = finished a turn, not yet re-engaged; `blocked` = waiting on a human
decision (e.g. a permission prompt) — surface it to the human or answer with
`herdr agent send-keys <pane> <key>...`; `unknown` = detection inconclusive.

- `--until` is repeatable. Without it, `wait` and `prompt --wait` match
  `idle|done|blocked`.
- **`prompt --wait` does not track turns.** Prompting an already-`working` agent
  can return when the _previous_ turn completes. Guard: `agent wait --until idle
--until done` _before_ prompting (step 3 above).
- **`agent_prompt_stalled`**: when prompting from a non-working state, `--wait`
  requires an observed state change within 5000 ms, else it returns
  `agent_prompt_stalled`; a `--timeout` under 5000 ms returns `timeout` instead.
  Handle both.
- **`agent_blocked`** (0.8.2): `agent prompt` now _refuses_ an agent already
  waiting at an approval or question dialog, returning `agent_blocked` without
  sending the text or the Enter. Don't retry blindly — read the blocked UI with
  `agent read` and ask the human before answering it via `agent send-keys`.

## Parsing herdr output

Three distinct conventions — don't assume one envelope:

1. **API-backed commands** (`workspace/tab/pane/agent list|get|create|...`)
   print a single-line envelope. Branch on `result` vs `error` (errors also
   exit 1), discriminate on `result.type`:
   `{"id":"cli:agent:get","result":{"type":"agent_info",...}}` /
   `{"id":"cli:agent:get","error":{"code":"agent_not_found","message":"..."}}`
   ```bash
   herdr agent get "$PANE" | jq -e '.result.agent.agent_status'
   ```
2. **`agent read` / `pane read`** print raw terminal text — no JSON, no `--json`.
3. **`--json` flags** (`session list --json`, `worktree * --json`,
   `agent explain --format json`) print bare JSON with no envelope.

CLI flag values use hyphens (`--source recent-unwrapped`); the raw socket API
uses underscores (`recent_unwrapped`). Don't copy CLI strings into socket
payloads.

## Integrations: claude and codex

An integration is a **SessionStart hook** the agent CLI runs; it POSTs
`pane.report_agent_session` over the herdr socket. For claude and codex it
reports **session identity only** — live `agent_status` always comes from
herdr's screen-detection manifests, integration or not. The payoff is native
session restore: with `[session] resume_agents_on_restore = true` (default),
herdr resumes agent panes into their native conversations after a server
restart — but only for panes that reported a session ref.

Health check:

```bash
herdr status                      # client/server version, protocol, compatible: yes
herdr integration status          # want: claude current, codex current
herdr agent list                  # every live claude/codex pane should carry
                                  # agent_session.source == "herdr:claude" / "herdr:codex"
```

Install / repair:

```bash
herdr integration install claude  # writes ~/.claude/hooks/herdr-agent-state.sh
                                  # + a SessionStart hook in settings.json
herdr integration install codex   # writes ~/.codex/herdr-agent-state.sh
                                  # + hooks.json + [features] hooks = true in config.toml
```

- Installed hook files are **herdr-managed** (stamped
  `HERDR_INTEGRATION_ID/VERSION`) — never edit them; add custom hooks beside
  them. `integration status` compares the stamp to the version bundled in the
  binary (`--outdated-only` filters).
- The hook fires on SessionStart only (`startup`/`resume`/`clear`/`compact`) —
  reinstalling does **not** retroactively fix already-running panes; a pane
  without `agent_session` needs a fresh agent session to start in it.
- **Codex gotcha:** codex gates hooks behind hook trust — the first interactive
  `codex` run after install may prompt to trust the new hook, and until trusted
  the pane reports no session ref. `codex doctor` verifies the setup.
- Installs respect `CLAUDE_CONFIG_DIR` / `CODEX_HOME`, and work without a
  running herdr server.

When `agent_status` looks wrong:

```bash
herdr agent explain <pane> --verbose   # winning detection rule + evidence
herdr server agent-manifests           # detection manifests (versioned separately)
herdr server update-agent-manifests    # refresh from herdr.dev
herdr server reload-agent-manifests    # reload local manifest overrides
```

Detection is screen-scraping (e.g. claude's `working` = a spinner frame in the
OSC title — braille, plus the half-circle frames `◐◓◑◒` that 0.8.2 also strips).
A pane with a static or custom terminal title degrades to `unknown`
— that's a manifest issue, not an integration issue.

## Gotchas

| Gotcha                                             | Handle                                                                 |
| -------------------------------------------------- | ---------------------------------------------------------------------- |
| Bare `herdr` opens a blocking TUI                  | Use subcommands only; `session attach`/`agent attach` are for humans   |
| `wait`/`prompt --wait`/`wait-output` block forever | Always `--timeout <ms>`                                                |
| `prompt --wait` can match the previous turn        | `agent wait --until idle --until done` before prompting                |
| Prompt from non-working state, no change in 5 s    | Treat `agent_prompt_stalled` like `timeout`                            |
| `agent get w6` / label → `agent_not_found`         | Agent targets are pane IDs or agent names only                         |
| Create/split may steal focus                       | `--no-focus` everywhere in automation                                  |
| Reinstalled integration, still no `agent_session`  | Hook is SessionStart-only — start a fresh agent session                |
| Codex hook silent after install                    | Hook trust not granted yet — run `codex` once, accept                  |
| `$PANE` lost between an agent's Bash tool calls    | Run create→read as one script, or reuse the literal id                 |
| `agent prompt` → `agent_blocked`                   | Agent sits at an approval dialog — read it, ask the human, `send-keys` |
| `agent start` → `agent_not_ready`                  | Blocked during startup; name still valid for `agent read`/`send-keys`  |
| Need to move a tab to another workspace            | No `tab move` across workspaces — `pane move <pane> --new-workspace`   |
| `read` output wraps unexpectedly in automation     | Headless panes default to 120×40 since 0.8.2 (was 80×24)               |
| `herdr update` errors on this machine              | Homebrew install: `brew upgrade herdr`                                 |

## References

- Docs: https://herdr.dev/docs/ — `cli-reference`, `socket-api`, `integrations`,
  `agent-skill`. `herdr api schema --json` prints the full protocol JSON Schema;
  `herdr --default-config` prints the annotated config.
- The binary ships upstream's own narrow agent skill: **`herdr --skill`** prints
  it, always in step with the installed version. **Do not `npx skills add
herdrdev/herdr --skill herdr -g`** — that writes `~/.agents/skills/herdr`,
  which shadows this skill via `~/.claude/skills/herdr` and then goes stale (the
  copy installed here predated 0.8.2 and already lacked `agent_blocked` and
  `agent_not_ready`). This skill covers the same command vocabulary plus
  integration health and automation pitfalls.
- Agent-facing docs, advertised in 0.8.2's CLI help:
  https://herdr.dev/llms.txt (debugging) and
  https://herdr.dev/agent-guide.md (first-time setup).
- Changelog on disk: `$(brew --prefix)/Cellar/herdr/<version>/CHANGELOG.md` —
  the only substantive local docs.
