# Comic panels with Muse (the "C3" recipe)

_Loaded on demand from `SKILL.md`. The house path for multi-panel raccoon strips since 2026-09-26._

A style study rendered two panels of a four-panel strip three times each under seven prompt and reference arms, and scored every image on a 7-point canon checklist (felt material, chibi head with open bead eyes, thick round rainbow glasses, warm palette, warm diorama set, verbatim lettering, character and prop spec). Muse Image went from 29/42 with the GPT prompt pasted in unchanged to **41/42** with this recipe, for about $0.01 a panel. GPT Image scored 7/7 per panel at about $0.15 per call. The recipe below is that 41/42 arm.

## Shape of a strip

1. **One Muse call per panel**, 1:1, `2K`. Never ask Muse for the whole page.
2. **Composite the page with `magick`** (see below). Geometry passes by construction, and each panel gets the model's whole attention.
3. GPT Image draws only the things Muse can't: see [When GPT Image](#when-gpt-image).

```bash
GEN=~/.claude/skills/gen-image/openrouter-image.py   # or the chop-conventions checkout
$GEN @panel-1.txt panel-1.png --model muse --aspect 1:1 \
  --ref images/raccoon-nerd.webp \
  --ref images/raccoon-larry.webp --ref images/larry-claw-ref.png \
  --ref sets/attic-gpt-panel.png
```

Run the panels (and 2–3 spins of each) in parallel. Refusals are not billed, and the script retries each one once.

## References: named, in this order

OpenRouter's Image API is stateless: one request, one image, and the model can't tell which attached image is which unless the prompt names each one **by position**. Attach them in this order and name them in the same order:

1. **The canon raccoon** (`raccoon-nerd.webp`), always first, stated as the style authority: _"Reference 1 is the CANONICAL IGOR raccoon and the canonical house style for material, palette, lighting and proportions; when anything disagrees with reference 1 about style, reference 1 wins."_
2. **Character sheets** for the other characters in _this_ panel only (for Larry, add the claw close-up after his sheet). Leave out sheets for characters who aren't in the panel, and leave out old strips used as style refs; both cost points in the study.
3. **Last: one GPT-rendered reference panel for this panel's set**, named as _"a FINISHED PANEL from this same strip, rendered in the target look. Match its rendering exactly: felt material, head proportions, thick rainbow glasses, warm light, set and balloon lettering. Do NOT copy its pose, framing or balloon words; follow the text below for those."_

The set reference panel is the biggest single gain (+6 over the same prompt without it). Render it **once per recurring set** with GPT Image and reuse it in every strip on that set. With no set reference, Muse alone still scored 37/42, which is acceptable.

Do not montage the references into one sheet. Four named, separate references beat a single "reference row" on every axis.

## Prompt blocks, in order

Order matters: the style block must come before the model has committed to a look.

1. **LAYOUT**: "ONE single square comic panel, 1:1, with a uniform 8 px square-cornered black border and a thin cream margin outside it. It is one panel from a four-panel strip; draw only this one panel."
2. **RENDER STYLE, flagged as the most important instruction.** Use this block verbatim:

   > RENDER STYLE, THE MOST IMPORTANT INSTRUCTION: a high-end 3D render of handmade NEEDLE-FELTED plush toys, exactly like reference 1. Every surface of every character, including the t-shirt, shows a fine fuzzy felt-wool fibre texture with a soft halo of stray fibres at the silhouette, like a real felted toy photographed up close; no smooth skin, no glossy CG sheen, no realistic individual fur strands. CHIBI TOY PROPORTIONS: the head is HUGE, as wide as the shoulders and about HALF of the whole body height, the body is small and soft, the limbs are short stuffed tubes. Eyes are big round glossy dark-brown beads with a white catch-light, set in dark mask patches. The whole image is WARM: honey-gold and amber key light from one side, soft warm bounce fill, gentle film grain, toasted browns, khaki, olive and cream. Nothing cold, nothing blue-grey, no grey daylight cast. The set is a detailed miniature diorama of the same warm handmade quality, lit by the same warm light, with a shallow depth of field and creamy background blur.

3. **THE REFERENCE IMAGES, IN THE ORDER THEY ARE ATTACHED**: one sentence per reference, as above.
4. **The set, named once, in warm words.** Muse takes set prose literally: "grey-white brick", "clean daylight" or "blue-grey walls" produce a cold picture whatever the style block says. **Warm the set description itself** ("warm golden late-afternoon sun streaming through in soft hazy shafts", "warm cream-painted brick", "honey-brown wood-panelled walls", "a table lamp that floods the room with warm amber-orange light"). This was the biggest prose lever in the study.
5. **Recurring props**, once each. For any phone: **"THE PHONE IS A MODERN SMARTPHONE: a thin black glass slab with a rounded-rectangle screen, like a current iPhone; never a flip phone, never a keypad feature phone, never a walkie-talkie."** Without it Muse drew keypad phones 3/3.
6. **Characters.** Canon details, then:
   - **Eyes always wide open**: "his eyes are ALWAYS big, round and wide open like reference 1, with a white catch-light; show suspicion or surprise with a paw on his chin, a tilted head and a small mouth, never with heavy lowered eyelids, never a scowl, never a wink." A script that says "squinting" or "suspicious" otherwise gets heavy lids and a scowl (3/3), which is off-model.
   - **Glasses**: "two PERFECTLY ROUND, very THICK rainbow rings … glossy like moulded plastic … Never thin wire, never oval, never rectangular." (Untested next tweak: add "the glasses are never felt", since a stronger all-felt style line without the set reference felted them.)
   - **Larry's claw, raised with pincers up**: "The claw arm is raised beside him, elbow bent, the claw held up in open air at shoulder height with its pincers pointing UP, exactly like reference 2, touching NOTHING", plus "LARRY'S CLAW IS ONE SOFT PLUSH CLAW: a single smooth two-pincer red claw like a stuffed felt glove at the end of his sleeve; it has no lobster legs, no antennae, no tail, and is not a whole lobster." A hanging claw grew lobster legs in 7 of 15 images; raised pincers-up, 0 of 6. State the claw and the paw **as a pair** ("exactly ONE red claw and exactly ONE furry paw"). A bare "never two claws" deletes the claw.
   - **Larry's beard**: "a big bushy felted grey-white beard and moustache that covers his whole lower face and chin down to his chest."
7. **LETTERING**: all-capitals, sized, clear of the border, exactly the quoted words and no other text anywhere. Balloon colour is the speaker tag (white for characters, cream `#F7F0D4` with a jagged outline for a device voice, in every panel). "Every tail is a plain drawn tail… never a party balloon on a string."
8. **Balloons along the top**: "both balloons sit along the TOP of the panel, [first speaker] top-left and [second] top-right above his own head; nothing is lettered in the bottom half." Without it Muse put a balloon at the bottom in 4 of 9 panels.
9. **Full stops spelled out**: quote every balloon verbatim _and_ name its punctuation when it ends on a full stop: `The phone's balloon reads exactly "REX." with the full stop.` Muse dropped that full stop in 4 of 9 panels otherwise.
10. **In this panel**: blocking, who speaks first (on the left), each balloon's words and where its tail ends.
11. **CAMERA**, with "every character stays FULLY IN FRAME with head and face never cropped", and a blurred foreground element for depth ("the blurred corner of a bookshelf in the near foreground edge").
12. **CHECK BEFORE FINISHING**: the panel's must-haves as a short checklist.
13. **FINAL STYLE REMINDER** (one line, last): "needle-felted plush toys with a huge chibi head, thick round rainbow-ring glasses on Igor, warm honey-gold light, exactly like reference 1."

## Composite the page

```bash
magick -size 1600x1600 xc:'#F7F0D4' \
  \( panel-1.png -resize 752x752! -shave 8x8 -bordercolor black -border 8 \) -geometry +32+32   -composite \
  \( panel-2.png -resize 752x752! -shave 8x8 -bordercolor black -border 8 \) -geometry +816+32  -composite \
  \( panel-3.png -resize 752x752! -shave 8x8 -bordercolor black -border 8 \) -geometry +32+816  -composite \
  \( panel-4.png -resize 752x752! -shave 8x8 -bordercolor black -border 8 \) -geometry +816+816 -composite \
  page.webp
```

The panel geometry here (2x2, 32 px gutters on a 1600 px cream page) is one blog's contract; use your own.

## When GPT Image

`--model gpt` (`openai/gpt-image-2.5-sunburst`, ~$0.15, `size: "2048x2048"`) only for:

- **The set reference panel**: once per recurring set, then reused as Muse's last reference in every strip on that set.
- **New character designs and anchor sheets**, where a look is set for the first time.
- **One-shot pages**: a whole four-panel grid in one call. GPT holds the grid; Muse was not tested on whole pages.

## Known traps

- **Claw chirality is random** on every model and no reference fixes it (wrong arm 8/8 across three reference strategies, including sheets that show it correctly). Don't name a side and don't respin for it. Respinning for the side is what grows a second claw. Score the count (one claw, one paw), never the side.
- **An armed character sheet trips the content filter** on every panel it's attached to. With the weapon in prose only there were 0 refusals in 3; with the armed sheet attached, 1 pass in 6. Keep sheets empty-pawed and put the weapon in the panel text. A refusal is a reference-set problem before it's a wording problem.
- **Refusals are stochastic and tighten with reference count.** Retry once identically, then drop a reference, then reword. A beat that one Muse endpoint refuses, the other refuses too.
- **Meta's direct API is for a single refine turn only.** Muse is also served at `https://api.meta.ai/v1/responses` (Responses API, chain with `previous_response_id`). Chaining did not beat named references on OpenRouter (claw attached 1/3 vs 3/3; the set was lost in close-ups). Use it for ONE refine turn on a nearly-right panel: text and pose fixes landed 3/3, structural fixes 0/3.
- **Continuity drifts between separately rendered panels.** The set reference panel and a named set block carry it; check recurring faces side by side before shipping.
