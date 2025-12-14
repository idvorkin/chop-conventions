# Before Implementing Checklist

Run through this checklist before starting any non-trivial implementation. Prevents wasted effort and ensures alignment.

## The Checklist

| Step                         | Question                                   | Why                                          |
| ---------------------------- | ------------------------------------------ | -------------------------------------------- |
| 1. **Spec first**            | Do I understand what success looks like?   | Prevents building wrong thing                |
| 2. **Confirm understanding** | Have I confirmed with user what they want? | Catches misunderstandings early              |
| 3. **Read existing code**    | What's already implemented?                | Avoids duplicating or breaking existing work |
| 4. **Check for patterns**    | How do similar features work here?         | Maintains consistency                        |
| 5. **Update docs if needed** | Will this change architecture docs?        | Keeps docs accurate                          |
| 6. **Plan for context loss** | Is there a tracking issue?                 | Work can continue if session ends            |

## When to Use

**Always use for:**

- New features
- Refactoring
- Bug fixes that touch multiple files
- Anything taking more than 15 minutes

**Skip for:**

- Typo fixes
- Single-line changes
- Adding a log statement

## Detailed Steps

### 1. Spec First

Before writing code, articulate:

- What is the user trying to accomplish?
- What are the acceptance criteria?
- What are the edge cases?

If unclear, ask: "Just to confirm, you want [X] which will [Y]. Is that right?"

### 2. Confirm Understanding

Red flags that you may have misunderstood:

- User used vague terms ("make it better", "fix this")
- Request seems to conflict with existing code
- Multiple interpretations are possible

Ask clarifying questions before diving in.

### 3. Read Existing Code

Before implementing:

```bash
# Search for related code
grep -r "relatedTerm" src/
# Check recent changes to area
git log --oneline -10 -- src/path/to/area/
# Read the files you'll modify
```

Understanding context prevents:

- Duplicating existing functionality
- Breaking existing behavior
- Inconsistent patterns

### 4. Check for Patterns

Look at how similar things are done:

- How are other components structured?
- What naming conventions are used?
- What error handling patterns exist?
- What testing patterns are used?

Match existing patterns unless there's a good reason not to.

### 5. Update Docs If Needed

Consider if your change affects:

- Architecture documentation
- API documentation
- README or setup guides
- CLAUDE.md conventions

Update docs as part of the implementation, not as an afterthought.

### 6. Plan for Context Loss

If the work is non-trivial:

- Create a beads issue or todo
- Document the approach in the issue
- Note any decisions made

This ensures work can continue if:

- Session times out
- Context gets compacted
- Another agent picks up the work

## Anti-Patterns

| Anti-Pattern                    | Problem                     | Better                       |
| ------------------------------- | --------------------------- | ---------------------------- |
| Jump straight to coding         | May build wrong thing       | Spec first                   |
| Assume you understand           | Misinterpret request        | Confirm explicitly           |
| Ignore existing code            | Break things, inconsistency | Read first                   |
| Skip planning for "quick" tasks | Tasks expand, context lost  | Always plan non-trivial work |

## Reference in CLAUDE.md

Add to your project's CLAUDE.md:

```markdown
## Before Implementing

Follow checklist in [chop-conventions/dev-inner-loop/before-implementing.md](https://github.com/idvorkin/chop-conventions/blob/main/dev-inner-loop/before-implementing.md).

Quick version:

1. Spec first - understand what success looks like
2. Confirm understanding - ask if unclear
3. Read existing code - understand context
4. Check patterns - match existing conventions
5. Plan for context loss - create tracking issue
```
