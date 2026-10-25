# NeuroLens launch trailer

A 75-second, 1920 × 1080, 30 fps Remotion film (2,250 frames). No voice-over. Footage, predicted signals, anatomical mesh, fonts, music and sound effects are local. The two film clips are muted; the music and three sound effects are the only audio.

## Render

From this directory:

```sh
npx remotion render src/index.ts NeuroLensTrailer out/neurolens_trailer_v4.mp4
```

To keep caches and temporary files inside the project:

```sh
XDG_CACHE_HOME="$PWD/.cache" npm_config_cache="$PWD/.npm-cache" TMPDIR="$PWD/out" \
  npx remotion render src/index.ts NeuroLensTrailer out/neurolens_trailer_v4.mp4
```

Dependencies are pinned in `package.json` and `package-lock.json` (Remotion 4.0.538). If they need reinstalling: `npm ci --cache .npm-cache`, then once `npx remotion browser ensure`. Remotion's headless Chrome is the only browser used; `tools/chrome-local.sh` keeps its renderer and GPU in one process on Apple Silicon. No app or browser UI is controlled.

## Soundtrack and sound effects

`public/music3.wav` is 76.05 s at 120 BPM. The trailer plays it from 4.0 s (frame 120, a bar start) into the uncut file, so the music runs to 72.05 s trailer time. The muffled bed used from 40.0 to 44.25 s is made once with:

```sh
ffmpeg -i public/music3.wav -af "lowpass=f=450,volume=0.8" public/music3-muffled.wav
```

Both `<Audio>` beds use `trimBefore` from `MUSIC_START` in `src/beats.ts`; the normal and muffled beds are crossfaded by volume in `src/Trailer.tsx`, and exactly one is audible at a time. The three effects in `public/sfx/` are the impact at 20.0 s, the mute chime at 40.0 s (made with ffmpeg, a tap and two tones in the song's key) and the scribble at 60.25 s. `HIT_OFFSET` in `src/beats.ts` records each file's offset from its start to the main hit.

Music and sound effects (impact, scribble) made with ElevenLabs; the mute chime is our own.

## Timing

`src/beats.ts` is the hand-written timeline: 30 fps, 2,250 frames, `MUSIC_START = 4`, the 120 BPM beat grid, named moments, scene starts and the sound-effect offsets. The grid, silences, muffled window and moments are expressed in music time and shifted by `MUSIC_START`, so picture and music stay locked. `src/script.ts` owns every caption text and cue frame. `src/Trailer.tsx` places the scenes and audio; it does not keep a second copy of the words. `src/edit/Graph.tsx` draws every chart. `src/edit/Shatter.tsx` builds seeded polygon shards; it breaks the stop line on the drop and assembles the title on the final hit.

## Edit layout

| Frames | Shot |
| --- | --- |
| 0–90 | Big Buck Bunny (source 20 s), full-bleed. “You made a video.” |
| 90–180 | Sintel (source 12 s), full-bleed. “Will it grab them?” |
| 180–548 | One continuous `Scanner` push. 6–14 s slow push; 14–18.25 s accelerated fly-in through the open bore. “The real answer is in the brain.” / “A lab.” / “A scanner.” / “Weeks.” / “What if all you needed” |
| 548–600 | Black at the stop. “was the video?” It shatters on the drop. |
| 600–720 | `BrainHero` on the drop. “Meet NeuroLens.” |
| 720–840 | Hooked: full-bleed clip with the overall chart and peak marker. |
| 840–960 | Drift: full-bleed clip with the overall chart and trough marker. |
| 960–1,080 | Seven signal names, one per beat from 32.0 s; this scene fills 32–36 s until stage 2 adds the groups. |
| 1,080–1,200 | Groups: three columns with their charts. |
| 1,200–1,380 | Mute: full-bleed clip, overall and muted overall on one chart, muffled music. “Still works on mute?” |
| 1,380–1,620 | Compare: Bunny and Sintel side by side with one shared chart. “Which cut wins?” |
| 1,620–1,740 | `BrainHero`. “Built on a model trained on real brain scans.” |
| 1,740–1,830 | Drained `Scanner` placeholder with “Weeks”; stage 2 adds the counter, scribble and fade. |
| 1,830–2,048 | `BrainHero` and “Minutes.” |
| 2,048–2,093 | Gather: the brain holds as the vignette closes; no pulse. |
| 2,093–2,160 | “NeuroLens” assembles from shards on the final hit, then “See how your video lands.” |
| 2,160–2,250 | Three credit lines and a fade to black over the last 16 frames. |

## Data fidelity

- The main chart line is `signals.overall`; the muted line is `muted.overall`. Values keep their sign and the model's own scale. There is no absolute-value conversion and no per-clip rescaling; `0` is typical viewing.
- Zooming a chart changes only the axis window, never the data. Labels keep the stored signed values. A two-line chart uses one shared domain so the lines are directly comparable.
- Straight segments join actual one-second samples; playheads, markers and readouts interpolate between adjacent samples.
- `src/bunny.json` and `src/sintel.json` contain only `duration_seconds`, `signals` and `muted`, copied from the local result files. Job metadata is not bundled or displayed.
- The brain views parse the saved binary per-vertex predictions, interpolate between samples and map them through the stored mesh parents. As in the product map, negative predictions remain neutral and positive values enter the heat palette. The fixed comparison scale is 0.5 from the app config, not a per-shot normalisation. This colouring does not transform the signed chart data.

## Verification

Type check:

```sh
npm run typecheck
```

Extract frames and make readable contact sheets:

```sh
mkdir -p out/frames_v4
ffmpeg -hide_banner -loglevel error -i out/neurolens_trailer_v4.mp4 \
  -vf "select='not(mod(n,1))'" -fps_mode vfr -q:v 2 out/frames_v4/frame_%04d.jpg
../.venv/bin/python tools/contact_sheets.py --input out/frames_v4 --step 1 \
  --prefix contact_v4 --per-sheet 6 --width 640
```

`contact_sheets.py` writes nothing when the frame directory is empty or missing. It defaults to six 640 px thumbnails per sheet, with frame and time labels.

## Attribution

Predicted brain response from TRIBE v2 (Meta, CC BY-NC).
Big Buck Bunny and Sintel © Blender Foundation, CC BY 3.0.
Music and sound effects (impact, scribble) made with ElevenLabs; the mute chime is our own.

## Scope

All created/modified files are under `trailer/`. No git commands, AWS, deploys, paid calls or website access are used.
