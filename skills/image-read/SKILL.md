---
name: image-read
description: "Read (not generate) images via a subagent so the pixel payload stays out of the parent conversation. Dispatches Haiku by default, escalates to Sonnet/Opus when the caller flags ambiguity or needs load-bearing detail."
allowed-tools: Agent
---

# Image Read

Parent Claude is handed an image path (Telegram attachment, screenshot the user pasted, photo dropped into the session). Reading it directly with the `Read` tool loads ~1000–2000 tokens of image payload into the parent's context and keeps them there for the rest of the session. Over a day of photos that adds up fast.

Instead: dispatch a subagent at a cheap model (Haiku 4.5), have it return a rich text description, and keep the pixel payload out of the parent. Only the text summary lands in main context.

**Sibling scope.** This skill is about READ/describe only. For image _generation_ see `gen-image` and `image-explore`. For hosting images in PRs see `gist-image`.

## When to use

Use when the parent just needs to know what's in an image but is not about to pixel-edit it:

- Telegram attachments the user sent for context (receipts, whiteboards, screenshots of errors)
- Photos the user drops mid-conversation to describe a situation
- Screenshots attached to PR reviews or bug reports
- Any inbound image where a text description is enough to act on

**Do NOT use** when:

- The parent needs to visually reason over the image itself (e.g. image explicitly asked about by the user as "does this look right?", where a summary strips the evidence)
- The parent is about to modify or diff the image pixels
- The image is something the parent produced and needs to verify end-to-end

## Default pattern (Haiku)

Dispatch a subagent with a purpose-aware prompt. The `<purpose>` slot is the most important knob — it tells the subagent what the caller cares about so the description stays rich where it matters.

```
Agent(
  description: "Read image",
  subagent_type: "general-purpose",
  model: "claude-haiku-4-5",
  prompt: """
Read the image at <ABSOLUTE_PATH>. Do NOT write anything to disk, do NOT
call any other tools — just read it and describe it.

<purpose>
<PURPOSE_FROM_CALLER>
</purpose>

Return a rich description, not a caption. Someone reading your output later
should be able to answer targeted questions about this image without
re-reading the file. Cover:

- Objects and people (count, positions, relative sizes)
- All visible text, transcribed verbatim (OCR — quote exactly, flag anything illegible)
- Spatial composition (what's in foreground/background, left/right)
- Color palette and lighting, if they carry meaning
- Emotional tone or mood of the scene, if applicable
- Anything unusual, incongruous, or worth flagging

End with a single line:
  confidence: N/10
where N reflects how sure you are that your description captures what the
caller needs given the <purpose> above. Lower the score if text was hard
to read, if the image was low-resolution, or if the purpose asked about
something you couldn't clearly see.
""",
  run_in_background: false
)
```

Haiku is cheap enough that this is fine to run synchronously in the default case. The parent blocks briefly, gets the text, and moves on.

## Escalation

Re-dispatch at Sonnet 4.6 or Opus 4.7 when any of these fire:

- Subagent returned `confidence: 6/10` or lower
- The returned summary reads as ambiguous or hand-wavy on a point the parent needs (e.g. caller asked "what does the error say" and the summary just says "there's an error message")
- Caller now needs a specific visual detail the Haiku pass didn't cover

To escalate, re-invoke the same template with `model: "claude-sonnet-4-6"` (or `claude-opus-4-7` for load-bearing detail), and **tighten `<purpose>`** to name the specific detail you need. Don't re-run Haiku with the same prompt — Haiku already returned its best pass.

Never escalate past what's needed. Most inbound images are fine at Haiku.

## Cost signal

Rough order-of-magnitude for a single image read:

- **Haiku 4.5 subagent** (default): cheap, ~25× cheaper than Opus for the same image
- **Sonnet 4.6 subagent** (escalate): mid-tier, use when Haiku's confidence was low
- **Opus 4.7 subagent** (escalate): expensive, only for load-bearing detail
- **Parent `Read` direct** (avoid): cheapest per single operation but pixels stay in the parent's context for the rest of the session — the cost isn't the read, it's the carried payload

The tradeoff: one-shot sessions with a single image are fine to Read directly. Anything resembling a multi-turn session with several images should go through this skill.

## Example `<purpose>` slots

Substituted into the template above — the rest of the prompt stays unchanged.

- Telegram home-office photo: _"User sent this as context for a conversation about their home office setup. I need what's on the desk, any visible monitors / devices, and the general layout. Transcribe any on-screen or on-paper text."_
- PR screenshot of a failing test: _"This is a screenshot of a test runner output. I need the exact failing test name, the assertion error verbatim, and any stack-trace frames that mention project source files."_
- Receipt: _"User wants expenses extracted. I need vendor, date, itemized line items with prices, subtotal, tax, total, and payment method."_

Keep `<purpose>` specific. Vague purpose ("describe the image") gets a vague summary, which forces an escalation that a sharper purpose would have avoided.
