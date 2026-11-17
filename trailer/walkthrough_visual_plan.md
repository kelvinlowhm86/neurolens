# Walkthrough v6: shot-by-shot visual plan

Goes with `walkthrough_script.md` (section numbers match). 1920 × 1080, 30 fps, Remotion.

## Feel
A person showing you their project, not an ad. Sleek and smooth, feels interactive (a cursor
clicks, the page responds), motion graphics where they explain. Style reference: AI SUS launch
video (tight, rebuilt UI filling the frame, glides over still screens, one coloured keyword).

## Rules for every shot (lessons from v5)
- **Designed, not cropped.** No dimmed crops of a laptop screen recording. Each page moment is
  rebuilt as a large component from the real data (`public/walkthrough/swee/result.json`, the
  clip, the brain mesh), in the site's look (Geist, site colours). Readable at 1080p on a phone:
  body text at least 36 px, numbers and labels at least 44 px.
- **One idea per shot.** One thing is brightest: the thing the voice names. Everything else is
  quiet or absent.
- **Calm camera.** At most one slow move per shot (push or glide, ease-in-out). No pans while
  text is being read.
- **Cuts on the voice.** Each shot starts on the first word of its line and holds 0.5 s after
  the last. Timing comes from the voice file at the end; until then, use scratch timings.
- **One diagram, built up.** Sections 3-7b are one architecture diagram that grows piece by
  piece, never a new diagram per section. AWS service names appear as small labels under plain
  names ("Storage" / S3).
- **Motion that explains.** A dot (the job) travels the diagram; parts light when used and grey
  when asleep. No decorative motion.

## Shots

| # | Script | Shot | What moves | Brightest thing |
|---|---|---|---|---|
| 1a | "You made a video." | Swee Choon clip full-bleed, muted, playing. | Nothing but the clip. | The clip. |
| 1b | "NeuroLens predicts…" | Clip shrinks into a phone frame on the left; the brain fades in on the right, lighting second by second in sync. | Shrink (0.6 s), then brain activity. | The brain. |
| 1c | "Let's put one through." | Cut to the upload page hero (rebuilt, large). | Fade. | Headline. |
| 2a | "This is a TikTok…" | Upload card centred, large. File icon with "swee_choon.mp4" drops in. | Cursor drags the file in. | The file. |
| 2b | "…see the price…" | Card shows length and price in large type. | Price counts up once. | Price. |
| 2c | "…held from your credit…" | Credit balance beside it; a slice of the bar turns hatched with "held". | Hatch fills. | "Held". |
| 3a | "So where does the video go?" | Page card slides left and becomes the first box ("Website") of an empty diagram on a dark canvas. | Morph (0.8 s). | Website box. |
| 3b | "Straight into storage…" | "Storage" (S3) appears; the file travels browser → storage on its own line, bypassing "Website". A crossed-out line through Website. | File dot travels. | The direct path. |
| 3c | "…puts the job in a queue…" | "Queue" (SQS) appears; a small job card drops in. Website box dims ("moves on"). | Job drop. | Queue. |
| 3d | "It's asynchronous…" | Line between Website and Queue turns dashed; the two halves gently separate a few px. Caption: "asynchronous". | Small separation. | The gap. |
| 4 | "Before a GPU…" | "Check" (Lambda) appears after the queue. Job passes in; readout "length 0:21 ✓ price ✓". A second, bad file is stopped with ✗ and a closed gate before the GPU box. | Two dots, one stopped. | The stop. |
| 5a | "And the GPU only runs…" | "GPU" (SageMaker) appears grey, "0 running". Job arrives: it powers up, counter 0 → 1. | Power-up glow. | Counter. |
| 5b | "Things go quiet…" | Glow fades, counter 1 → 0, box greys. Caption: "scale to zero". | Fade. | "0". |
| 5c | "The website and the database are serverless…" | Website and "Database" (Aurora Serverless) boxes pulse on a little traffic, then grey when idle. Caption: "serverless". | Two pulses. | Those two boxes. |
| 5d | "The trade-off…" | Small clock on the GPU box: "~few min to start". Then a tick beside "not needed in a second". | Clock hand turns. | Clock. |
| 6 | "On the GPU, Meta's TRIBE…" | Push into the GPU box; inside, two copies of the clip side by side, one with a speaker icon, one with a muted icon. Label "TRIBE (Meta)". | One slow push. | The two passes. |
| 7a | "…you don't pay for it." | Pull back to the diagram. Credit bar from 2c reappears small: job fails (red ✗ on GPU), hatched slice returns to the balance. | Slice slides back. | Returned credit. |
| 7b | "Billing is idempotent…" | Same job card arrives twice; the second bounces off the "Charged once" stamp. Caption: "idempotent". | Bounce. | Stamp. |
| 7c | "…a watchdog…" | "Watchdog" (Lambda guard + reaper) appears beside the GPU with an eye icon; it switches off a lingering GPU. Dead-letter queue shown as a small side tray. | Switch-off. | Watchdog. |
| 7d | "All of this is infrastructure as code…" | A thin outline labelled "Terraform" draws around the whole diagram. Caption: "infrastructure as code". | Outline draws (1 s). | Outline. |
| 7e | "…reviewed, rebuilt, or torn down…" | Diagram fades out and back in from the outline. | Fade out/in. | The rebuild. |
| 7f | "Every part gets least privilege…" | Each box gets a small key; one arrow that a box shouldn't use is shown blocked. Caption: "least privilege". | Keys appear in turn. | Blocked arrow. |
| 8a | "About ten minutes later…" | Diagram fades to the result: clip left, brain right, chart full width underneath (rebuilt large). | Fade. | Whole result. |
| 8b | "The line… The brain…" | Line draws left to right; then the brain lights. | Line draw, then brain. | Each in turn. |
| 8c | "Anything above the dashed line…" | Dashed "typical" line brightens; area above it tints. | Tint. | Dashed line. |
| 8d | "It peaks at eight seconds…" | Peak marker pops at 0:08; cursor clicks it; clip jumps to 0:08. | Click, jump. | Peak. |
| 8e | "She's telling the story…" | Hold on the clip at the old photos. | Nothing. | Clip. |
| 8f | "But a lot of feeds autoplay on mute…" | Mute toggle appears large; cursor clicks it. | Click. | Toggle. |
| 8g | "…the whole line drops…" | Muted line draws under the sound line; gap between them tints. | Draw. | The gap. |
| 9a | "Then the report reads…" | Report tab rebuilt as a clean card; headline and first moment. | Fade. | Card. |
| 9b | "It goes moment by moment…" | Three rows stack: "On screen", "Brain", "Do". | Rows in turn. | Each row. |
| 9c | "It's written by Claude…" | Small "Claude on Amazon Bedrock" label at the card's foot. | Fade. | Label. |
| 9d | "For the old photos…" | The 0:08-0:13 moment's "Do" line, large, alone. | Push in slightly. | "Do" line. |
| 10 | "None of this predicts your views…" | Plain dark frame, the two sentences as type, nothing else. | Fade. | Words. |
| 11 | "The samples are live…" | Site home page rebuilt large; URL below. Fade to black. | Fade. | URL. |

## Build order
1. One shared diagram component (boxes, lines, travelling dot, grey/lit states) and a page-card
   component in the site's look. Most shots are these two.
2. Shots 3-7 (the diagram), then 8-9 (result and report), then 1-2 and 10-11.
3. Scratch-voice timing, one full render, review, fix.
4. Final voice recorded last; retime cuts to it.

## Reuse vs replace
- Reuse: brain mesh loader (`src/scenes/brain`, `src/walkthrough/brainData.ts`), `Graph.tsx` for
  the chart, the Swee Choon clip and `result.json`, fonts, `sfx/`.
- Replace: `src/walkthrough_v5/` (cropped-recording approach) and the old v4 scene files once v6
  covers them; screen recordings `s*.mp4` and `captures/` are reference only.

## Open questions for Josh
1. Diagram look: dark canvas with lit boxes (matches the trailer), or light like the site?
2. Captions for the signpost terms (asynchronous, serverless…): on, as small labels? (Planned: yes.)
3. Background music: keep the low chill bed from v4, or voice only?
