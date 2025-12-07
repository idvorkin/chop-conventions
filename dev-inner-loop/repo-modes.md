# Repository Mode Guardrails

Claude Code operates differently depending on which repository it's running in. This document defines two modes and their respective guardrails.

## Two Modes of Operation

### 1. AI-Tools Mode (Autonomous Agents)

**Applies to:** Repositories in the `idvorkin-ai-tools` organization (forks for agent work)

**Characteristics:**

- Agents work autonomously with minimal human intervention
- Multiple agents may work in parallel on different branches
- Speed and throughput are prioritized

**Guardrails:**

- ✅ Can merge directly to main
- ✅ Can push to main without PR
- ✅ Can make architectural decisions within established patterns
- ⚠️ Must run tests if merge had conflicts
- ⚠️ Must revert quickly if main is broken

### 2. Human-Supervised Mode (Primary Repos)

**Applies to:** Primary repositories owned by humans (e.g., `idvorkin/*`, `night-work/*`)

**Characteristics:**

- Human explicitly runs Claude Code
- Changes require human review and approval
- Quality and correctness are prioritized over speed

**Guardrails:**

- 🚫 NEVER push directly to main
- 🚫 NEVER merge PRs without explicit "YES" from user
- 🚫 NEVER force push without explicit "yes" from user
- ✅ Always use feature branches
- ✅ Always create PRs for review
- ✅ Always wait for human approval

## Detecting Repository Mode

Check the git remote URL to determine mode:

```bash
# Get the remote URL
remote_url=$(git remote get-url origin 2>/dev/null || echo "")

# Check if it's an AI-tools repo
if [[ "$remote_url" == *"idvorkin-ai-tools"* ]]; then
    # AI-Tools Mode - more permissive
    MODE="ai-tools"
else
    # Human-Supervised Mode - more restrictive
    MODE="human-supervised"
fi
```

## Pre-Push Hook (Remote-Aware)

Use this hook to enforce guardrails based on which remote you're pushing to:

```bash
#!/bin/bash
# Block direct pushes to main on human-supervised repos only

remote_name="$1"
remote_url="$2"
protected_branch="main"

# Determine if this is an AI-tools repo (permissive) or human repo (restrictive)
is_ai_tools=false
if [[ "$remote_url" == *"idvorkin-ai-tools"* ]]; then
    is_ai_tools=true
fi

while read local_ref local_sha remote_ref remote_sha; do
    if [[ "$remote_ref" == "refs/heads/$protected_branch" ]]; then
        if $is_ai_tools; then
            # AI-tools repo - allow push to main
            echo "✅ Pushing to main (AI-tools repo)"
        else
            # Human-supervised repo - block push to main
            echo ""
            echo "🚫 Direct push to '$protected_branch' is blocked!"
            echo ""
            echo "   This is a human-supervised repo. All changes require PRs."
            echo ""
            echo "   Instead:"
            echo "   1. Create a feature branch: git checkout -b fix/my-change"
            echo "   2. Push the branch: git push -u origin fix/my-change"
            echo "   3. Create a PR: gh pr create"
            echo ""
            exit 1
        fi
    fi
done

exit 0
```

## Actions Requiring Explicit "YES" (Human-Supervised Mode)

In human-supervised repositories, these actions require the user to type "YES" (uppercase):

| Action                               | Why                                      |
| ------------------------------------ | ---------------------------------------- |
| Merging PRs to main                  | Human must review and approve changes    |
| Force pushing                        | Can destroy history and lose work        |
| Deleting branches with unmerged work | Prevents accidental work loss            |
| Major refactoring                    | Architectural decisions need human input |

**Note:** Phrases like "go ahead", "do it", or "merge it" are NOT sufficient. The user must explicitly type "YES".

## Actions Allowed Without Approval (AI-Tools Mode)

In AI-tools repositories, agents can:

| Action                         | Condition              |
| ------------------------------ | ---------------------- |
| Merge to main                  | After tests pass       |
| Push to main                   | After successful merge |
| Create/delete feature branches | As needed for work     |
| Revert broken commits          | To quickly fix main    |

## Syncing Between Modes

When changes need to flow from AI-tools repo to human-supervised repo:

```bash
# From AI-tools repo, create PR to human repo
gh pr create --repo idvorkin/repo-name --head idvorkin-ai-tools:main --base main

# Human reviews and approves (requires "YES")
# Human merges the PR
```

## Quick Reference

```
┌─────────────────────────────────────────────────────────────┐
│                    REPOSITORY MODE                          │
├─────────────────────────────────────────────────────────────┤
│  idvorkin-ai-tools/*     │  idvorkin/* (or other human)    │
│  ─────────────────────   │  ────────────────────────────   │
│  AI-Tools Mode           │  Human-Supervised Mode          │
│  • Agents work freely    │  • Human runs Claude Code       │
│  • Direct main pushes OK │  • PRs required for main        │
│  • Speed prioritized     │  • Quality prioritized          │
│  • Self-healing (revert) │  • Explicit approval needed     │
└─────────────────────────────────────────────────────────────┘
```
