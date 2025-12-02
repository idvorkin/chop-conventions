Prompt: Clean Up PR Commit History (Safely, Story-First)

I have a PR with working code, but the commit history is messy and hard to review. I want to create a new PR with the same final result, but with clean, logical commits that tell a clear story.

Your job: 1. Analyze the changes in this PR and help me break them into a sequence of well-structured commits. 2. Think like a senior engineer optimizing for code quality, clarity, and reviewability. 3. Respect the safety & workflow rules below.

⸻

Commit structure I want

Please propose a commit sequence that follows this pattern: 1. Housekeeping first (no behavior changes)
• Linting fixes, formatting, import cleanup, purely mechanical refactors
• These should be clearly separated from functional changes. 2. Bug fixes individually
• Each meaningful bug fix in its own commit.
• Commit message must clearly explain:
• What was broken
• How it’s fixed
• Any relevant edge cases. 3. Features / final changes
• Remaining substantive changes:
• New behavior, new APIs, new UI, etc.
• Group into logical units that are easy to understand and review.

⸻

Important note about history

We do not need to preserve the original sequence of changes in the messy PR.
• It’s fine (and preferred) to:
• Fold “address PR comments” commits into the relevant feature/bugfix commits.
• Fold “oops fix typo / fix tests” into the commit that introduced the change.
• Reorder commits so they tell a clean, logical story, even if that’s not the order things were originally written.

The goal is a reviewer-friendly history, not an archaeological record.

⸻

For each proposed commit

For every commit in the new clean sequence, provide: 1. Contents description
• Which files and types of changes belong in this commit (paths + high-level description).
• Call out any tests that should move with these changes. 2. Commit message
• Title: concise and descriptive.
• Body:
• What changed
• Why it changed (motivation)
• Any risks, tradeoffs, or follow-ups
• If it’s a bug fix, explicitly describe previous behavior vs new behavior. 3. Grouping rationale
• Why this commit is a good unit of review.
• How it supports a clear narrative through the PR.

⸻

Safety & Workflow Rules

Follow these rules to keep things safe and predictable: 1. Always work on a new branch
• Assume we:
• Have an existing PR branch: e.g. feature/original-pr.
• Create a new branch from the same base: e.g. git checkout -b feature/clean-history <base-branch>.
• All re-construction of history should happen on the new branch, not the original PR branch. 2. Final state must match the original PR
• The new branch must end in the exact same final code state as the original PR branch.
• Explicitly describe how to verify this, e.g.:
• git diff feature/original-pr..feature/clean-history (should be empty), and/or
• Running the same test suite as for the original PR.
• If these checks aren’t clean, explicitly say: “The branches are not yet equivalent.” 3. No destructive operations on shared branches by default
• Prefer:
• New branches
• cherry-pick
• git add -p and fresh commits
• git rebase -i only on local branches you control.
• If you suggest potentially dangerous commands like:
• git reset --hard
• git push --force
You must:
• Call out that they rewrite history and can lose work if used incorrectly.
• Recommend using them only on branches the user controls and understands. 4. Do not fabricate changes
• Base your breakdown only on the diff / information I provide.
• If you need to infer intent, label it clearly as an assumption rather than a fact. 5. Secrets and sensitive data
• If the diff appears to include tokens, passwords, keys, or secrets:
• Call them out and recommend removal / rotation.
• Do not repeat the secret value in your response. 6. Respect project conventions
• If commit message style is obvious (e.g., Conventional Commits), follow it.
• If not, default to something like:
• fix: describe bug & behavior change
• refactor: describe housekeeping change
• feat: describe new capability. 7. You only suggest commands
• You are not running git commands, only suggesting them.
• Encourage the user to review commands before running them, especially anything that modifies history.

⸻

Git workflow output

After proposing the commit sequence, provide a concrete, step-by-step workflow to build this new history safely, starting from a clean base branch and resulting in a clean new branch.

You can choose a suitable strategy, for example:
• Fresh branch, build up commits from scratch 1. git checkout <base-branch> 2. git checkout -b feature/clean-history 3. Use git cherry-pick, git add -p, and git commit to rebuild the history in the proposed order.
• Or interactive rebase on a local working branch, if appropriate.

For your chosen approach: 1. List commands in order, with brief explanations. 2. Indicate where to:
• Manually stage hunks (git add -p)
• Manually split / reorder changes
• Run tests. 3. Clearly highlight any step that:
• Rewrites history (e.g., rebase, reset, push --force).
• Needs extra care.

⸻

Output format 1. Proposed Commit Sequence
• Numbered list of commits.
• For each: contents summary, commit message, and rationale. 2. Git Workflow
• Ordered list of suggested commands + explanations.
• Explicit verification step to ensure:
• feature/clean-history final state == feature/original-pr final state.

If you’re missing context for some changes, state your assumptions explicitly.
