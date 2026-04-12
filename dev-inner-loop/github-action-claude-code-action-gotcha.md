# `anthropics/claude-code-action@v1` gotchas

Lessons from debugging real workflows. Read this before editing any `.github/workflows/*.yml` that uses `anthropics/claude-code-action@v1`.

## 1. Workflow YAML must byte-match the default branch

The action exchanges its OIDC token for an app token against Anthropic's backend. **The backend refuses the exchange unless the running workflow file exactly matches the version on the repository's default branch.** Error signature:

```
App token exchange failed: 401 Unauthorized - Workflow validation failed.
The workflow file must exist and have identical content to the version
on the repository's default branch.
```

Implication: `workflow_dispatch` against a feature branch with modified workflow YAML **will not run**. To test workflow changes:

- **Option A**: Open a PR, land it, dispatch from `main` after merge.
- **Option B**: Fast-forward a fork's `main` to your feature branch, dispatch from fork main, then PR to upstream.

Anti-tamper exists to prevent malicious PRs from modifying the workflow to exfiltrate secrets — don't try to bypass it.

## 2. `show_full_output` defaults to `false` — turn it on

Without this set, the job log shows only:

```
Running Claude Code via SDK (full output hidden for security)...
Rerun in debug mode or enable `show_full_output: true` in your workflow file for full output.
```

No tool calls, no intermediate messages, just a terse result JSON at the end. `permission_denials_count: 1` in that summary tells you nothing about _which_ tool was denied. Set `show_full_output: true` on every workflow you need to audit. The log gets noisy but auditable.

## 3. Observability pattern: transcript artifact + step summary

The action writes a complete SDK event transcript to `/home/runner/work/_temp/claude-execution-output.json` — JSON array (not JSONL) with every tool call and a final `result` event carrying `duration_ms`, `num_turns`, `total_cost_usd`, `permission_denials_count`, `is_error`, and `permission_denials[]` with the specific denied tool calls.

Standard scaffold to surface it:

```yaml
- name: Upload Claude transcript
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: claude-execution-output
    path: /home/runner/work/_temp/claude-execution-output.json
    retention-days: 30
    if-no-files-found: warn

- name: Summarize Claude run
  if: always()
  run: |
    F=/home/runner/work/_temp/claude-execution-output.json
    [ -f "$F" ] || { echo "No transcript" >> "$GITHUB_STEP_SUMMARY"; exit 0; }
    jq -r '
      (map(select(.type == "result")) | last) as $r |
      "## Claude run summary\n\n" +
      "| Metric | Value |\n|---|---|\n" +
      "| Duration | \(($r.duration_ms // 0) / 1000 | floor)s |\n" +
      "| Turns | \($r.num_turns // "n/a") |\n" +
      "| Cost | $\($r.total_cost_usd // "n/a") |\n" +
      "| Denials | \($r.permission_denials_count // 0) |"
    ' "$F" >> "$GITHUB_STEP_SUMMARY"
```

`if: always()` so diagnostic steps run even when Claude fails. `if-no-files-found: warn` so runs that fail before Claude boots don't also fail the artifact step.

Worked example: `idvorkin/idvorkin.github.io` → `.github/workflows/changelog.yml`.

## 4. `--allowedTools` takes scoped `Skill(name)` permissions

Slash commands dispatch through the `Skill` tool, which must be in the allowlist. Use the scoped form rather than bare `Skill` to avoid opening up every skill:

```yaml
claude_args: |
  --allowedTools "Bash,Edit,Read,Write,Grep,Glob,Skill(changelog)"
```

Without `Skill(changelog)`, invoking `/changelog` from the prompt is denied. Claude typically falls back to reading `SKILL.md` directly and executing steps by hand, which often still produces a correct output but skips the formal slash-command flow and shows up as `permission_denials_count: 1`.

## 5. Fork PRs cannot get OIDC tokens — use `pull_request_target`

For `on: pull_request` events where the PR comes from a fork, GitHub Actions **does not set `ACTIONS_ID_TOKEN_REQUEST_URL`** — regardless of `id-token: write` in workflow permissions. The action reports this as:

```
error: Error message: Unable to get ACTIONS_ID_TOKEN_REQUEST_URL env variable
Attempt 1 failed: Could not fetch an OIDC token.
Did you remember to add `id-token: write` to your workflow permissions?
```

The error message is **misleading** — the permission _is_ already set. This is a hardcoded GitHub security boundary: fork PRs don't get OIDC tokens, period.

Standard fix: switch the trigger:

```yaml
on:
  pull_request_target:
    types: [opened, synchronize, ready_for_review, reopened]
```

`pull_request_target` runs the workflow file from the **base branch** (not the PR — so fork PRs can't modify the workflow itself) but gives it full write GITHUB_TOKEN and secrets. Safe for review-style workflows that only read the PR diff via API without executing PR code.

## 6. CONTRIBUTOR-tier authors auto-run workflows on fork PRs

Once an author has **one** merged PR to the repo, GitHub promotes them from `FIRST_TIME_CONTRIBUTOR` to `CONTRIBUTOR`, and subsequent fork PRs from that author run workflows **without maintainer approval**. Relevant for bot accounts (`idvorkin-ai-tools` etc.) — their PRs burn CI minutes without gating.

Fork PR workflow runs are still sandboxed: read-only `GITHUB_TOKEN`, no secrets, no OIDC. The blast radius is CI cost, not security.

To gate anyway: repo `Settings → Actions → General → Fork pull request workflows from outside collaborators` → **"Require approval for all outside collaborators"**. Or promote the bot from "fork contributor" to actual Collaborator, which both fixes the auto-run concern and allows its PRs to be opened from base-repo branches (unblocking the fork-PR-OIDC problem at the same time).

## 7. Node 20 → Node 24 deprecation is imminent (June 2, 2026)

GitHub Actions forces all JavaScript actions to Node 24 as the default runtime on **June 2, 2026**, and removes Node 20 entirely in **Fall 2026**. Source: https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/.

Runs of `anthropics/claude-code-action@v1` currently emit this annotation:

```
Node.js 20 actions are deprecated. The following actions are running on
Node.js 20 and may not work as expected: actions/upload-artifact@v4,
oven-sh/setup-bun@<SHA>.
```

The action bundles its own pinned `oven-sh/setup-bun` — you can't upgrade that SHA from your workflow, only Anthropic can. Two knobs, both set in the job's `env:`:

- **Opt in early** (recommended — verify now while you can still roll back): `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true`
- **Stay on Node 20 past June 2, 2026** (only until the Fall removal): `ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION=true`

If your own workflow uses `actions/upload-artifact@v4` (e.g., for the transcript pattern in #3), that's Node 20 too — watch for a `v5` release.

Extra gotchas: Node 24 is **incompatible with macOS 13.4 or earlier**, and drops ARM32 self-hosted runner support.
