# NeuroLens launch trailer

A 43-second, 1920 × 1080, 30 fps Remotion film (1,290 frames). No voice-over. Footage, predicted signals, anatomical mesh, fonts and music are local. The two film clips are muted; the edited track is the only audio.

## Render

From this directory:

```sh
npx remotion render src/index.ts NeuroLensTrailer out/neurolens_trailer_v2.mp4
```

The explicit output path is what `tools/verify_v2.py` inspects. To keep caches and temporary files inside the project:

```sh
XDG_CACHE_HOME="$PWD/.cache" npm_config_cache="$PWD/.npm-cache" TMPDIR="$PWD/out" \
  npx remotion render src/index.ts NeuroLensTrailer out/neurolens_trailer_v2.mp4
```

Dependencies are pinned in `package.json` and `package-lock.json` (Remotion 4.0.538). If they need reinstalling: `npm ci --cache .npm-cache`, then once `npx remotion browser ensure`. Remotion's headless Chrome is the only browser used; `tools/chrome-local.sh` keeps its renderer and GPU in one process on Apple Silicon. No app or browser UI is controlled.

## Prepare the soundtrack and timing

`tools/analyze_music.py` is the single source of the edit and of every cut, beat, downbeat and named moment:

```sh
../.venv/bin/python tools/analyze_music.py
```

It decodes `public/music2.mp3` at 44.1 kHz, measures the material, writes `public/music-edit.wav`, and rewrites `src/beats.ts`. Evidence lands in `out/audio_source_onsets.json`, `out/audio_edited_onsets.json` and `out/audio_timing.json`. `tools/prepare_audio.sh` is only a wrapper around that script.

The track is credited in the film as LNPlusMusic (Pixabay). Source duration is 117.943 s; the edit is 43.000 s at 100 BPM (2.4 s bars). `musicPieces` in `src/beats.ts` records the mapping, with 15 ms crossfades where source material is skipped:

| Piece | Source seconds | Edit seconds |
| --- | --- | --- |
| breakdown | 31.349–36.136 | 0.000–4.787 |
| build-a | 38.416–40.820 | 4.772–7.176 |
| build-b | 43.225–45.620 | 7.161–9.556 |
| stop + drop | 45.620–48.014 | 9.556–11.950 |
| drop body | 48.014–69.146 | 11.950–33.082 |
| ending | 108.010–117.943 | 33.067–43.000 |

The gaps 36.136–38.416, 40.820–43.225 and 69.146–108.010 are intentionally removed. The entrance has a 20 ms de-click, gain is 0.6, and the ending keeps its natural decay; the script asserts the edit does not clip. The one-beat duck is authored in `src/Trailer.tsx` as `duckVolume`, using `beatGap(moments.duck)`, so its length follows the measured beat rather than a hard-coded frame count.

Do not run `tools/prepare_assets.py`: it is a draft-1 helper that rewrites `src/beats.ts` for the old `public/music.mp3` timeline. Current timing comes only from `tools/analyze_music.py`.

## Edit layout

`src/script.ts` is the single source of all caption text and cue frames (including credits). `src/Trailer.tsx` imports those cues; it does not keep a second copy of the words. `src/beats.ts` owns the timing contract; `src/edit/Graph.tsx` draws every chart.

| Frames | Shot |
| --- | --- |
| 0–143 | Procedural `Scanner` push-in. “To see how a brain reacts to a video,” / “you needed this.” |
| 143–337 | Scanner build. “A lab.” / “A scanner.” / “Weeks.” |
| 337–359 | Cut to black at the stop. “What if all you needed” / “was the video?” |
| 359–431 | `BrainHero start={12}` on the drop. “Meet NeuroLens.” |
| 431–502 | Full-bleed Big Buck Bunny (source 50 s) cutting to Sintel (source 13 s); Big Buck Bunny overall graph, source 38–54.8 s, marked at its +0.21 peak at 53 s. “The second they’re hooked.” |
| 502–575 | Full-bleed Big Buck Bunny (source 30 s) cutting to Sintel (source 5 s); overall graph, source 20–42 s, marked at its −0.097 low at 30 s. “The second they drift.” |
| 575–719 | “Seven signals.” Faces, Bodies, Scenes, Visual, Sound, Speech and Social arrive one per beat with their stored signed values at source seconds 25–31. |
| 719–790 | Full-bleed Big Buck Bunny, source 25 s. One-beat audio duck. Overall and muted overall on one graph, source 20–36 s. “Still works on mute?” |
| 790–935 | Vertical Big Buck Bunny / Sintel split, both source 26 s. Both overall lines on one graph, source 14–42 s. “Which cut wins?” |
| 935–992 | `BrainHero start={25}`. “Built on a model trained on real brain scans.” |
| 992–1,218 | Ending build to the final musical hit. |
| 1,218–1,290 | “NeuroLens” lands on the hit, then “See how your video lands.” and the three credit lines; fade to black over the last 16 frames. |

## Data fidelity

- The main chart line is `signals.overall`; the muted line is `muted.overall`. Values keep their sign and the model's own scale. There is no absolute-value conversion and no per-clip rescaling; `0` is typical viewing.
- The hooked and drift charts zoom their vertical domains to the real local range (hooked −0.15 to +0.25; drift −0.15 to +0.08) so the actual movement is readable. The plotted numbers and labels remain the stored signed values; zooming changes only the axis window, not the data.
- The comparison chart computes one domain from both films together (`bothRange`: combined min/max plus 14% padding). Both lines use that same domain, so their heights are directly comparable. The two-line muted chart also uses one graph domain for both lines.
- Straight segments join actual one-second samples; playheads, markers and readouts interpolate between adjacent samples.
- `src/bunny.json` and `src/sintel.json` contain only `duration_seconds`, `signals` and `muted`, copied from the local result files. Job metadata is not bundled or displayed.
- The brain views parse the saved binary per-vertex predictions, interpolate between samples and map them through the stored mesh parents. As in the product map, negative predictions remain neutral and positive values enter the heat palette. The fixed comparison scale is 0.5 from the app config, not a per-shot normalisation. This colouring does not transform the signed chart data.

## Verification

Run the offline checks:

```sh
../.venv/bin/python tools/verify_v2.py
```

It reads `src/beats.ts`, `src/script.ts` and (when present) `out/neurolens_trailer_v2.mp4`. It prints the cut list against the nearest beat and named moment with frame deltas, checks every caption start and end, reports edges outside one frame, compares `src/bunny.json` and `src/sintel.json` with their local source `result.json` files (duration, min/max, indices and exact values), and records source-level evidence for the muted comparison, the shared comparison domain, the duck automation and caption ownership. It writes `out/verification_v2.json` and exits nonzero on timing or data violations. It does not inspect pixels, and it does not substitute for a visual review.

When a render exists, the script also reads stream durations and frame count with `ffprobe` and decodes the audio with `ffmpeg` into numpy to report the true peak. End-of-film, true-stop, film-start and final-decay edges are reported as allowed exceptions rather than timing errors.

Extract frames and make readable contact sheets:

```sh
mkdir -p out/frames_v2
ffmpeg -hide_banner -loglevel error -i out/neurolens_trailer_v2.mp4 \
  -vf "select='not(mod(n,1))'" -fps_mode vfr -q:v 2 out/frames_v2/frame_%04d.jpg
../.venv/bin/python tools/contact_sheets.py --input out/frames_v2 --step 1 \
  --prefix contact_v2 --per-sheet 6 --width 640
```

`contact_sheets.py` writes nothing when the frame directory is empty or missing. It defaults to six 640 px thumbnails per sheet, with frame and time labels. For a shorter review, extract every Nth frame and pass the same `--step N`; old `out/frames` sheets can still be made with `--step 60`.

Type check:

```sh
npm run typecheck
```

## Attribution

Predicted brain response from TRIBE v2 (Meta, CC BY-NC).
Big Buck Bunny and Sintel © Blender Foundation, CC BY 3.0.
Music: LNPlusMusic (Pixabay).

## Scope

All created/modified files are under `trailer/`. No git commands, AWS, deploys, paid calls or website access are used.
