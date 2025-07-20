# Pull Request Workflow

## Core Principles

- **Never make changes directly on the main branch**
- Always create an issue first and iterate with the user until requirements are clear
- Use the `gh` CLI for all GitHub operations
- Include test plans in every issue and PR

## Workflow Steps

### 1. Create and Refine Issue

Before starting any work:

1. **Draft the issue with the user**

   - Discuss the problem/feature with the user
   - Create a draft issue description
   - Iterate on the issue until you're confident you understand:
     - What needs to be done
     - Why it's needed
     - Success criteria
     - Edge cases and constraints

2. **Issue Template**

   ```markdown
   ## Problem/Feature Description

   [Clear description of what needs to be done]

   ## Acceptance Criteria

   - [ ] Specific requirement 1
   - [ ] Specific requirement 2
   - [ ] ...

   ## How to Test

   1. Step-by-step testing instructions
   2. Expected results
   3. Edge cases to verify

   ## Additional Context

   [Any relevant information, links, or considerations]
   ```

3. **Create the issue**
   ```bash
   gh issue create --title "Brief descriptive title" --body "$(cat <<'EOF'
   [Issue content from template above]
   EOF
   )"
   ```

### 2. Create Feature Branch

After the issue is created and agreed upon:

```bash
# Create and switch to a new branch
git checkout -b feature/issue-NUMBER-brief-description

# Or if you prefer separate commands
git branch feature/issue-NUMBER-brief-description
git checkout feature/issue-NUMBER-brief-description
```

### 3. Development Process

- Make changes following clean-code.md guidelines
- Commit frequently following clean-commits.md guidelines
- Run tests after each significant change
- Keep commits atomic and logical

### 4. Create Pull Request

When ready to create the PR:

```bash
# Push branch to remote
git push -u origin feature/issue-NUMBER-brief-description

# Create PR linking to issue
gh pr create --title "Brief description" --body "$(cat <<'EOF'
## Summary
[1-3 bullet points summarizing the changes]

## Related Issue
Fixes #NUMBER

## Changes Made
- Specific change 1
- Specific change 2
- ...

## How to Test
[Copy test plan from issue or provide updated instructions]

## Checklist
- [ ] Tests pass
- [ ] Code follows project conventions
- [ ] Documentation updated (if applicable)
EOF
)"
```

### 5. PR Review Process

During code review:

1. **Check PR comments regularly**

   ```bash
   gh pr view --comments
   ```

2. **After fixing review comments**

   - Ask the user: "Should I update the review comment to show it's been addressed?"
   - If yes, respond to the comment explaining what was changed

   ```bash
   gh pr comment --body "Fixed: [explanation of change]"
   ```

3. **Always verify tests pass**

   ```bash
   # Run tests before pushing fixes
   just test  # or appropriate test command

   # Check CI status
   gh pr checks
   ```

### 6. Keeping PR Updated

- Keep the PR branch up to date with main

  ```bash
  git checkout main
  git pull origin main
  git checkout feature/issue-NUMBER-brief-description
  git merge main  # or rebase if preferred
  ```

- Update PR description if scope changes
  ```bash
  gh pr edit --body "Updated description"
  ```

## Important Reminders

- **Always iterate with the user** on issue creation until requirements are crystal clear
- **Include "How to Test" section** in every issue and PR
- **Use `gh` commands** for all GitHub interactions
- **Check test output** before and after making changes
- **Ask before updating review comments** - some teams prefer different approaches
- **Never merge without passing tests** and approved reviews

## Quick Reference

```bash
# Common gh commands for PR workflow
gh issue create              # Create new issue
gh issue list               # List open issues
gh pr create                # Create PR
gh pr view                  # View current PR
gh pr checks                # Check CI status
gh pr comment               # Add comment to PR
gh pr review                # Start a review
gh pr merge                 # Merge PR (when approved)
```
