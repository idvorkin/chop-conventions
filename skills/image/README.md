# `image/` skill group

Image lifecycle skills: create, host, and explore visual content.

| Skill | Description |
|---|---|
| [`gen-image`](gen-image/SKILL.md) | Generate illustrations via the Gemini image API |
| [`gist-image`](gist-image/SKILL.md) | Host binary images on GitHub gists for PRs/issues/docs |
| [`image-explore`](image-explore/SKILL.md) | Brainstorm multiple visual directions in parallel and build a comparison page |

## How they fit together

`gen-image` and `image-explore` both call `image-explore/generate.py`, which in turn
calls `gen-image/gemini-image.sh` and reads `gen-image/raccoon-style.txt`.
`gist-image` is the hosting primitive used by `image-explore`'s publish step.

## Installing

Skills are symlinked individually at the **flat** skill name — the `image/` nesting is
purely organizational in this repo:

```bash
# source directory after grouping
CHOP="$HOME/gits/chop-conventions"

ln -sf "$CHOP/skills/image/gen-image"     ~/.claude/skills/gen-image
ln -sf "$CHOP/skills/image/gist-image"    ~/.claude/skills/gist-image
ln -sf "$CHOP/skills/image/image-explore" ~/.claude/skills/image-explore
```

The Claude Code plugin loader discovers skills by their symlink name (e.g.,
`~/.claude/skills/gen-image`), not by the source path hierarchy. Grouping
is a repo-organization concern only — no loader changes required.
