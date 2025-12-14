# Bug Investigation Protocol

Before fixing ANY bug, stop and answer these questions. This prevents patching symptoms while missing root causes.

## The Three Question Categories

### 1. Spec Questions

| Question                                              | Why It Matters                                           |
| ----------------------------------------------------- | -------------------------------------------------------- |
| Is this actually a bug, or is my understanding wrong? | Prevents "fixing" correct behavior                       |
| Is there a missing or unclear spec?                   | Identifies documentation gaps                            |
| Ask the user if ambiguous                             | "The behavior is X, but I expected Y. Which is correct?" |

### 2. Test Coverage Questions

| Question                                              | Why It Matters                               |
| ----------------------------------------------------- | -------------------------------------------- |
| Why didn't tests catch this?                          | Reveals testing gaps                         |
| What level of test pyramid could catch this earliest? | Unit → Integration → E2E (earlier = cheaper) |
| Add the missing test BEFORE fixing                    | Proves the fix works, prevents regression    |

**Cost of catching bugs at each level:**

- Unit test: 1x
- Integration test: 10x
- E2E test: 100x
- Production: 1000x

### 3. Architecture Questions

| Question                                                       | Why It Matters                                                                              |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Is there an architectural problem that made this bug possible? | Prevents repeated similar bugs                                                              |
| If yes, create an issue to track it                            | Don't lose the insight                                                                      |
| Ask the user before big refactors                              | "I found an architectural issue: [description]. Address now or just fix the immediate bug?" |

## The Protocol

```
1. STOP - Don't immediately fix
2. SPEC - Is this actually a bug? Ask if unclear
3. TEST - Why no test? Add one first
4. ARCH - Deeper problem? Create issue
5. FIX - Now fix with confidence
```

## Example Workflow

```markdown
**Bug**: Button click doesn't save data

**Spec check**:

- Expected: Click saves immediately
- Actual: Click does nothing
- User confirmed this is a bug ✓

**Test check**:

- No E2E test for save button
- Unit test exists but mocks the API
- → Add E2E test first that fails

**Architecture check**:

- Save handler swallows errors silently
- This pattern exists in 3 other places
- → Create issue: "Add error boundaries to all save handlers"

**Fix**: Add error handling, verify E2E test passes
```

## Anti-Patterns

| Anti-Pattern                          | Problem             | Better Approach                    |
| ------------------------------------- | ------------------- | ---------------------------------- |
| Fix immediately without understanding | May fix wrong thing | Ask clarifying questions first     |
| Fix without adding test               | Bug can regress     | Test first, then fix               |
| Big refactor without asking           | Scope creep         | Ask user if they want arch fix now |
| Patch around bad architecture         | Technical debt      | At minimum, create tracking issue  |

## Reference in CLAUDE.md

Add to your project's CLAUDE.md:

```markdown
## Bug Investigation

Before fixing bugs, follow the protocol in [chop-conventions/dev-inner-loop/bug-investigation.md](https://github.com/idvorkin/chop-conventions/blob/main/dev-inner-loop/bug-investigation.md).

Quick version:

1. **Spec**: Is this actually a bug? Ask if unclear.
2. **Test**: Add missing test BEFORE fixing.
3. **Arch**: Deeper problem? Create issue.
```
