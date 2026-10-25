# NeuroLens launch trailer

A 43-second, 1920 × 1080, 30 fps Remotion film. No voice-over. All footage, predictions, anatomy and music are local; no website or screen recording is used.

## Render

From this repository's root:

```sh
cd trailer
npx remotion render
```

The default composition and output are configured in `remotion.config.ts`. The result is **`out/neurolens_trailer.mp4`** (H.264, yuv420p, AAC audio).

The exact command used for the delivered render keeps caches and temporary render files inside this project:

```sh
cd trailer
XDG_CACHE_HOME="$PWD/.cache" npm_config_cache="$PWD/.npm-cache" TMPDIR="$PWD/out" npx remotion render
```

Dependencies are pinned to exact versions in `package.json` and `package-lock.json`, including Remotion **4.0.538**. If dependencies need reinstalling, use `npm ci --cache .npm-cache`, followed once by `npx remotion browser ensure` to restore the locally downloaded headless browser. Remotion's headless Chrome is the only browser used. On Apple Silicon, `tools/chrome-local.sh` keeps its renderer and GPU in a single process to avoid macOS process-start restrictions; the config selects it automatically. No app/browser UI is controlled.

## Edit and verify

- `src/Trailer.tsx`: components, typography, animation and scenes.
- `src/beats.ts`: measured onsets, source times, 30 fps cut points.
- `src/bunny.json`, `src/sintel.json`: only the duration and signal fields from the copied results. Job metadata never enters the film.
- `tools/analyze_music.py`: reproducible offline spectral-flux analysis of 22,050 Hz mono PCM, with pulse autocorrelation. `out/onsets.json` retains source onset measurements.
- `tools/prepare_audio.sh`: recreates the local music cut, fades and unclipped gain using ffmpeg.
- `tools/prepare_assets.py`: extracts the signed time series, writes measured onsets, copies local Geist font files.
- `tools/render_brain.py`: creates the rotating brain frames from the actual local anatomical mesh and Big Buck Bunny vertex predictions, following the binary format, parent interpolation, sulcal shading and heat stops in `static/brain_view.js`.

Geist 400/500/600 is loaded through `@remotion/google-fonts` with its font URLs replaced by local WOFF2 files installed from `@fontsource/geist`. No Google Fonts download occurs during rendering.

Type check:

```sh
npm run typecheck
```

Extract the final rendered frame every two seconds:

```sh
mkdir -p out/frames
ffmpeg -hide_banner -loglevel error -i out/neurolens_trailer.mp4 -vf "select='not(mod(n,60))'" -fps_mode vfr -q:v 2 out/frames/frame_%02d.jpg
```

## Music and timing

Music: AudioEra, `audioera-stylish-trailer-percussion-622881.mp3`.
**Source segment: 34.500–77.500 seconds.** The measured pulse is approximately 120 BPM. The opening uses the source's short break/build, phrase cuts land on the detected accents to within one video frame, and the last hit decays into a fade. The local WAV edit has an 80 ms entrance fade, 1.3 s tail fade starting at trailer 41.7 s, and 0.6 gain (headroom for the original MP3’s above-full-scale decoded peaks). The film contains only this music; both video soundtracks are muted.

| Trailer time | Scene |
| --- | --- |
| 0:00–0:04.5 | “Every second of a video” / “lands somewhere in the brain.” |
| 0:04.5–0:10.5 | Floating Big Buck Bunny clip and real rotating brain response map |
| 0:10.5–0:17.5 | “See it. Second by second.” Overall chart, playhead and signed summary |
| 0:17.5–0:25.5 | Seven signals appear on beats and gather into See, Hear, Understand |
| 0:25.5–0:30.5 | “What if it's muted?” Actual cool dashed comparison separates |
| 0:30.5–0:36.5 | “Compare.” Big Buck Bunny and Sintel, on one signed chart |
| 0:36.5–0:40.5 | NeuroLens / “See how your video lands.” |
| 0:40.5–0:43 | Small muted attribution, final music hit, fade to black |

## Data fidelity

The main line is `signals.overall`; the no-audio line is `muted.overall`. Values retain their sign and model scale. No absolute-value conversion or per-clip rescaling is used. The charts use the same fixed signed vertical domain and a visible zero baseline. Straight segments join actual one-second samples; the playhead and displayed numbers interpolate between adjacent samples.

The brain and first video use source seconds 12–18; the following overall scene continues with source seconds 18–25. The muted shot uses Big Buck Bunny seconds 25–30. The comparison plays source seconds 26–32 of both films with their corresponding overall samples. The signal chips use Big Buck Bunny seconds 25–33.

Group membership follows the requested trailer brief: See = Faces, Bodies, Scenes, Visual; Hear = Sound, Speech; Understand = Social. Each small group line is the arithmetic mean of its member signal arrays. The supplied JSON's stored group fields have different membership, so those fields are not substituted for these requested groupings.

The brain uses the actual binary predictions, interpolated in time and mapped to the real mesh through its stored parents. As in the product's map, negative vertex predictions remain neutral while positive responses enter the heat palette. The fixed heat range is the 99th percentile of the complete saved Big Buck Bunny brain array. This color mapping does not transform the signed chart data.

## Attribution

Predicted brain response from TRIBE v2 (Meta, CC BY-NC).
Big Buck Bunny and Sintel © Blender Foundation, CC BY 3.0.
Music: AudioEra.

## Final verification

Rendered with Remotion 4.0.538. Video: 43.000 s, 1,290 frames, 1920 × 1080 at 30 fps; MP4 container: 43.008 s including AAC padding. Full-file ffmpeg decode and TypeScript checks pass. All 22 exported frames (0–42 s, every 2 s) were visually reviewed. Final decoded audio peak: −1.6 dBFS. `out/verification.json`, `out/media-info.json`, `out/data-verification.json` and the contact sheets retain the checks.

## Scope

All created/modified files are under `trailer/`. No git commands, cloud model workers, AWS, deploys, paid calls or website access are used. The prescribed external model-worker commands were incompatible with this task's npm-only network limit. Implementation and checks were performed locally with Remotion/Chromium, ffmpeg and the repository's Python/numpy environment.
