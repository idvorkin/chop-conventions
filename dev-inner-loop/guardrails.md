# Guardrails

Actions the agent can NEVER take without explicit user approval. Approval means the user must type "YES" to confirm.

> **Enforcement**: These rules can be mechanically enforced via [Claude Code safety hooks](../dev-setup/claude-safety-hooks.md). Instructions alone don't prevent accidents—hooks do.

## Requires "YES" Approval

- **Removing broken tests** - Fix the test or fix the code, but never delete a failing test without explicit approval
- **Pushing to main** - Always use feature branches and PRs
- **Force pushing** - Can destroy history and break collaborators
- **Accepting/merging PRs** - Human must review and approve
- **Any action that loses work** - Deleting branches with unmerged commits, hard resets, discarding uncommitted changes
- **Big refactors during bug fixes** - If you discover an architectural issue while fixing a bug, ask user before refactoring: "I found [issue]. Address now or just fix the immediate bug?"

## Encouraged (not losing work)

Cleaning up dead code is fine and encouraged - this is different from losing work:

- Deleting unused functions, classes, or files
- Removing commented-out code
- Cleaning up unused imports

These are preserved in git history and recoverable anytime via `git log` or `git checkout`.

## End of Session

When the human signals end of session, review the conversation for potential improvements:

- **Corrections made** - Did the human repeatedly correct a behavior? Consider adding it to CLAUDE.md
- **Workflow friction** - Were there patterns that slowed things down? Suggest optimizations
- **Missing context** - Did the agent lack knowledge it should have? Update project docs
- **New conventions** - Did we establish patterns worth codifying?

Propose updates to CLAUDE.md or project docs based on what was learned.

## End of Day Review (Human Practice)

Periodically review Claude conversation logs across sessions to identify patterns:

- **Repeated corrections** - Same feedback given multiple times across sessions? Codify it
- **Common questions** - Agent keeps asking for the same context? Add it to CLAUDE.md
- **Workflow patterns** - Successful approaches worth standardizing?
- **Pain points** - Recurring friction that could be eliminated with better setup?

Use this to continuously improve CLAUDE.md and project conventions.
