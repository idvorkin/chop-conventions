# Surge Deployment

Deploy static sites to [Surge.sh](https://surge.sh) with GitHub Actions. Supports PR previews and automatic teardown.

## Features

- **Production deploys** on push to main
- **PR preview deploys** with auto-comment showing preview URL
- **Auto-teardown** of PR previews when PR closes
- **Secure** two-stage workflow pattern (fork PRs can't access secrets)
- **Debuggable** with 3 distinct jobs in GitHub Actions logs:
  - `Deploy Main to Production`
  - `Deploy PR Preview`
  - `Teardown PR Preview`

## Quick Start

1. Copy the workflow files to your repo
2. Configure GitHub secrets
3. Push to main

## Workflow Files

### `.github/workflows/build.yml`

```yaml
name: Build and Test

on:
  push:
    branches:
      - main
  pull_request:
    branches:
      - main

permissions:
  contents: read

jobs:
  build:
    runs-on: ubuntu-latest
    name: Build and Test

    steps:
      - name: Checkout code
        uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4

      - name: Setup Node.js
        uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4
        with:
          node-version: 20
          cache: "npm"

      - name: Install just
        uses: extractions/setup-just@dd310ad5a97d8e7b41793f8ef055398d51ad4de6 # v2

      - name: Install dependencies
        run: npm ci

      - name: Run unit tests and build
        run: just test

      - name: Upload build artifact
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: dist-${{ github.event_name == 'push' && 'main' || format('pr-{0}', github.event.pull_request.number) }}
          path: dist
          retention-days: 7
```

### `.github/workflows/deploy-surge.yml`

```yaml
name: Deploy to Surge

on:
  workflow_run:
    workflows: ["Build and Test"]
    types: [completed]
  pull_request_target:
    types: [closed]
    branches:
      - main

permissions:
  contents: read
  pull-requests: write
  actions: read

jobs:
  deploy-main:
    if: |
      github.event_name == 'workflow_run' &&
      github.event.workflow_run.conclusion == 'success' &&
      github.event.workflow_run.event == 'push'
    runs-on: ubuntu-latest
    name: Deploy Main to Production
    environment: surge-deploy

    steps:
      - name: Download build artifact
        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4
        with:
          name: dist-main
          path: dist
          run-id: ${{ github.event.workflow_run.id }}
          github-token: ${{ secrets.GITHUB_TOKEN }}

      - name: Setup Node.js
        uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4
        with:
          node-version: 20

      - name: Deploy to Surge
        run: npx surge ./dist "${{ secrets.SURGE_DOMAIN }}" --token "${{ secrets.SURGE_TOKEN }}"

  deploy-pr:
    if: |
      github.event_name == 'workflow_run' &&
      github.event.workflow_run.conclusion == 'success' &&
      github.event.workflow_run.event == 'pull_request'
    runs-on: ubuntu-latest
    name: Deploy PR Preview
    environment: surge-deploy

    steps:
      - name: Get PR info
        id: pr
        uses: actions/github-script@f28e40c7f34bde8b3046d885e986cb6290c5673b # v7
        with:
          script: |
            const headSha = context.payload.workflow_run.head_sha;
            const { data: prs } = await github.rest.pulls.list({
              owner: context.repo.owner,
              repo: context.repo.repo,
              state: 'open',
              head: `${context.payload.workflow_run.head_repository.owner.login}:${context.payload.workflow_run.head_branch}`
            });

            const pr = prs.find(p => p.head.sha === headSha);
            if (pr) {
              core.setOutput('number', pr.number);
              core.setOutput('domain', `pr-${pr.number}-${{ secrets.SURGE_DOMAIN }}`);
            } else {
              core.setFailed(`Could not find PR for SHA ${headSha}`);
            }

      - name: Download build artifact
        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4
        with:
          name: dist-pr-${{ steps.pr.outputs.number }}
          path: dist
          run-id: ${{ github.event.workflow_run.id }}
          github-token: ${{ secrets.GITHUB_TOKEN }}

      - name: Setup Node.js
        uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4
        with:
          node-version: 20

      - name: Deploy to Surge
        run: npx surge ./dist "${{ steps.pr.outputs.domain }}" --token "${{ secrets.SURGE_TOKEN }}"

      - name: Comment PR with preview URL
        uses: actions/github-script@f28e40c7f34bde8b3046d885e986cb6290c5673b # v7
        with:
          script: |
            const prNumber = ${{ steps.pr.outputs.number }};
            const domain = '${{ steps.pr.outputs.domain }}';
            const body = `## Surge Preview

            Your preview is ready!

            **URL:** https://${domain}

            This preview will be automatically deleted when the PR is closed.`;

            const { data: comments } = await github.rest.issues.listComments({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: prNumber,
            });

            const botComment = comments.find(c => c.body.includes('Surge Preview'));

            if (botComment) {
              await github.rest.issues.updateComment({
                owner: context.repo.owner,
                repo: context.repo.repo,
                comment_id: botComment.id,
                body: body
              });
            } else {
              await github.rest.issues.createComment({
                owner: context.repo.owner,
                repo: context.repo.repo,
                issue_number: prNumber,
                body: body
              });
            }

  teardown-pr:
    if: |
      github.event_name == 'pull_request_target' &&
      github.event.action == 'closed'
    runs-on: ubuntu-latest
    name: Teardown PR Preview
    environment: surge-deploy

    steps:
      - name: Setup Node.js
        uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4
        with:
          node-version: 20

      - name: Teardown Surge deployment
        run: npx surge teardown "pr-${{ github.event.pull_request.number }}-${{ secrets.SURGE_DOMAIN }}" --token "${{ secrets.SURGE_TOKEN }}"
```

## GitHub Secrets Setup

### 1. Get Your Surge Token

```bash
npm install -g surge
surge login
surge token
```

### 2. Create GitHub Environment

1. Go to repo **Settings** → **Environments**
2. Click **New environment**
3. Name it `surge-deploy`

### 3. Add Secrets

In the `surge-deploy` environment:

| Secret         | Value                                    | Example           |
| -------------- | ---------------------------------------- | ----------------- |
| `SURGE_TOKEN`  | Your token from `surge token`            | `abc123...`       |
| `SURGE_DOMAIN` | Your surge domain (no `https://` prefix) | `my-app.surge.sh` |

### 4. Rotate Token

```bash
surge token | gh secret set SURGE_TOKEN --repo OWNER/REPO --env surge-deploy
```

## Security Model

The two-stage workflow pattern safely handles untrusted PR code:

```text
┌─────────────────────────────────────────────────────────────────┐
│ Stage 1: Build Workflow (build.yml)                             │
│ - Triggers on: push to main, pull_request                       │
│ - Permissions: contents: read (NO secrets access for fork PRs)  │
│ - Runs: npm ci, build, test                                     │
│ - Output: Static artifacts (dist/)                              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ workflow_run trigger
┌─────────────────────────────────────────────────────────────────┐
│ Stage 2: Deploy Workflow (deploy-surge.yml)                     │
│ - Triggers on: workflow_run completed                           │
│ - Permissions: HAS secrets access                               │
│ - Runs: Download artifact → Deploy static files                 │
│ - Key: Never executes PR code, only deploys pre-built artifacts │
└─────────────────────────────────────────────────────────────────┘
```

### Why This Is Secure

1. **Untrusted code runs without secrets**: Fork PR code executes in Stage 1, which has no access to `SURGE_TOKEN`.

2. **Secrets never touch PR code**: Stage 2 has secrets but only downloads and deploys static files—it never checks out or executes the PR's code.

3. **Artifact isolation**: Artifacts are downloaded from the specific workflow run ID, preventing artifact confusion attacks.

4. **SHA-pinned actions**: All GitHub Actions are pinned to specific commit SHAs, preventing supply chain attacks.

### Threat Mitigations

| Threat                                 | Mitigation                                            |
| -------------------------------------- | ----------------------------------------------------- |
| Malicious PR deploys bad frontend code | Only to PR preview URL, not production                |
| Compromised GitHub Action              | SHA pinning prevents auto-update to malicious version |
| Stolen secrets                         | Secrets in GitHub environment, never in code          |
| Artifact tampering                     | Downloaded from specific `run-id`, not by name lookup |

## Justfile Commands

Add to your `justfile` for local deployment:

```just
# Deploy to surge.sh (staging)
deploy-stage: test build
    npx surge dist your-app-stage.surge.sh

# Deploy to surge.sh (production)
deploy-prod: test build
    npx surge dist your-app.surge.sh
```

## Troubleshooting

### Build Fails

- Check GitHub Actions logs
- Ensure all dependencies are in `package.json`
- Test locally: `just test`

### Deployment Fails

- Verify `SURGE_TOKEN` is valid: `surge token`
- Check `SURGE_DOMAIN` format (no `https://` prefix)
- Ensure secrets are in the `surge-deploy` environment (not repo secrets)
- Check if `surge-deploy` environment has deployment branch restrictions that might be blocking

### Site Not Updating

- Clear browser cache
- Check GitHub Actions for successful deployment
- Verify correct branch was pushed

## Example Repos

- [idvorkin-ai-tools/hello-surge](https://github.com/idvorkin-ai-tools/hello-surge) - Minimal starter
- [idvorkin/igor-breathe](https://github.com/idvorkin/igor-breathe) - Full PWA example
