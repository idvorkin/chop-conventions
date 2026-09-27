# External Tools

Things this setup depends on that are **not** in this repo: binaries, npm
packages, and third-party agent skills. One manifest — [`external-tools.yaml`](./external-tools.yaml) —
records every one of them, and [`tools/tool_doctor.py`](./tools/tool_doctor.py)
tells you which are missing.

```bash
./dev-setup/tools/tool_doctor.py     # ✅ / ⚠️ / ❌ per entry; exits 1 if an adopted tool is missing
```

## Not submodules

These are release artifacts, not source we build: a `curl | sh` binary, an
`npm -g` package, a skill folder someone else's CLI copies into `~/.agents/`.
A submodule would pin a source tree nothing in this repo compiles, and would
still leave the install unperformed — so the manifest records _how to get it_
instead, and the doctor checks whether you did.

## Rule per kind

- **binary** — manifest row with the exact install command. Never vendored, never
  committed. If a binary needs new behaviour, add it upstream (for `rmux_helper`,
  see CLAUDE.md "Environment Primitives").
- **npm** — manifest row; `npm install -g <pkg>`, or `npx -y <pkg>` when it is
  rare enough not to install at all.
- **agent-skill** (third-party, [agentskills.io](https://agentskills.io) format) —
  `npx skills add <owner>/<repo>` into `~/.agents/skills/`, then symlink into
  `~/.claude/skills/<name>`, then a manifest row. Do not copy the skill folder
  into `skills/` here: that directory is for skills this repo authors, which are
  installed by symlinking out (see CLAUDE.md "Skills").
- **plugin** — marketplace plugins are **not** in the manifest. They live in
  CLAUDE.md "Plugins" and [`../marketplace.md`](../marketplace.md), installed with
  `/plugin install <name>@<marketplace>`.

## Promotion path

`proposed` → `trial` → `adopted`. A tool enters as `proposed` (an idea with an
install command). It becomes `trial` once it is actually installed and being
used for real work, with the open question written into its `why`. It becomes
`adopted` when something here depends on it — at which point the doctor starts
failing when it is missing. Tools that lose are deleted from the manifest with a
one-line note below, not left as permanent dead rows.

**Rejected:** [TheAuditor](https://github.com/TheAuditorTool/Auditor) — evaluated,
not useful. [MCP Agent Mail](https://github.com/Dicklesworthstone/mcp_agent_mail)
and [CASS Memory System](https://github.com/Dicklesworthstone/cass_memory_system)
(`cm context "<task>" --json`) are unevaluated; revisit only if `cass` graduates
from `trial`.
