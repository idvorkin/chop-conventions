# Experimental Tools

Tools we're evaluating for potential integration into our workflow. Status and notes tracked here.

## ~~TheAuditor~~ (Not Recommended)

**Repository:** https://github.com/TheAuditorTool/Auditor

Evaluated and found not useful. Skip.

---

## CASS (Coding Agent Session Search)

**Repository:** https://github.com/Dicklesworthstone/coding_agent_session_search

Unified search tool that indexes conversation history from multiple AI coding agents (Claude Code, Codex, Gemini CLI, Cline, Cursor, ChatGPT, Aider, etc.) into a single searchable timeline.

### Key Features

- Normalizes disparate storage formats (JSONL, SQLite, markdown) into common schema
- Sub-60ms full-text search
- Interactive TUI with three-pane layout
- Multi-machine sync via SSH
- MCP Server mode for direct agent integration

### Installation

```bash
# Linux/macOS
curl -fsSL https://raw.githubusercontent.com/Dicklesworthstone/coding_agent_session_search/main/install.sh | bash -s -- --easy-mode --verify
```

### Status

- [x] Local install exists
- [ ] Current session search is broken - needs debugging
- [ ] Test MCP server mode

---

## CASS Memory System

**Repository:** https://github.com/Dicklesworthstone/cass_memory_system

Procedural memory system that transforms scattered session logs into persistent, cross-agent knowledge. Creates a unified knowledge base where insights from one agent automatically benefit other agents.

### Architecture

Three cognitive layers:

1. **Episodic Memory** - Raw session logs from all agents (ground truth)
2. **Working Memory** - Structured diary entries summarizing sessions
3. **Procedural Memory** - Distilled rules with confidence tracking ("playbook")

### Key Features

- **Cross-Agent Learning**: Sessions from different AI tools feed one unified playbook
- **Confidence Decay**: 90-day half-life; harmful marks count 4x more than successes
- **Anti-Pattern Detection**: Bad rules become warnings rather than disappearing
- **Scientific Validation**: New rules checked against historical sessions before acceptance

### Usage

```bash
# Essential command for agents - get context before starting work
cm context "<task>" --json
```

Returns relevant rules, anti-patterns, and historical context as structured JSON.

### Status

- [ ] Install and configure
- [ ] Test integration with existing workflow

---

## Related Tools (Not Yet Evaluated)

- [MCP Agent Mail](https://github.com/Dicklesworthstone/mcp_agent_mail) - Inter-agent communication ("gmail for coding agents")
