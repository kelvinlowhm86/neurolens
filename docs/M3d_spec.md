# NeuroLens — M3d Implementation Spec: brain response

**Builds on:** M1–M3c. The TRIBE v2 model, the GPU run and the architecture stay as they are. What changes is how its output is read and shown. NeuroLens stops selling an "engagement score" and shows a **predicted brain response**: second by second, how strongly an average viewer's brain would respond to the video, relative to typical film and TV viewing. It never claims to predict views, retention or sales.

**Light treatment** (normal review, one real run), except deletion and the worker's write order (a missed key leaves user data behind; a half-written job could publish a result without its brain file): those get tests first by a separate agent and the break-its in §10.

## Decisions (21 Oct)
- **Why the change.** The five old regions detect content (faces, bodies, scenes, social cues, sound), not interest. The old score took the absolute value of each region's mean (so a dip counted as a rise) and stretched every video to 0-1 on its own, so two videos could not be compared. Two outside attempts to predict engagement from TRIBE output failed (r = 0.25 on 24 videos; a null result on 48 in arXiv 2607.01400).
- **Built-in scale.** TRIBE was trained on fMRI responses z-scored per vertex, so its raw output already has a reference: 0 means typical film/TV viewing. Values are shown signed and unscaled.
- **No lag fix.** tribev2 already shifts its predictions 5 s back to cancel the blood-flow delay (its README and demo), so second *t* of the result is second *t* of the video.
- **7 signals in 3 groups**, read from the Glasser areas the TRIBE v2 paper tested (§2), an overall line, a muted (no-audio) copy, summary cards, a 3D brain map, and A-vs-B comparison against the user's own videos.
- **The raw brain output is stored** (with-audio pass), so the map and any later change to the regions need no GPU rerun.
- **No value or persuasion signal**: the model gives no evidence for one.
- **Samples** become open-licensed Blender clips; the 14 third-party samples are removed.
- **One AWS day** deploys M3c and M3d together (§9).

## 1. Scope
### In scope
The new maths (`neurolens/response.py`, replacing `neurolens/engagement.py`); result format 2; the raw brain file; the summary; the brain route; the 410 for old results; S3 CORS; the CSV; config `response`; the page (one-screen layout, player, moments, charts, muted overlay, 3D map with head, compare, wording); the landing page and the app's move to `/app` (§8b); `infra/make_brain_mesh.py`; `infra/make_sample.py`; `deploy_web.sh`; new samples; docs.

### Out of scope
- Linking the response to real audience behaviour (future work in the report, using the consented retention curves M3c collects).
- An ad benchmark library (future work).
- Changes to the model, the GPU passes, billing or the job lifecycle.
- The two notebooks: they carry their own copy of the old maths and never import it, so they keep working as they are.
- Converting old stored results: they expire within 30 days (§4).

## 2. Signals (`neurolens/response.py`)
Areas from the Glasser HCP-MMP1 map (360 areas), the map the TRIBE v2 paper and tribev2's own `get_hcp_labels` use, so each signal reads the area the paper tested (§5.9 and figures 4-5 of arXiv 2605.04326). Vertex order is the left hemisphere (indices 0-10,241) then the right (10,242-20,483), as the model outputs them.

| Signal | Areas | Hemisphere | Group | Why these areas |
|---|---|---|---|---|
| faces | FFC | both | see | paper: face area (FFA) |
| bodies | V4t | both | see | paper: body area (EBA) |
| scenes | PHA1, PHA2, PHA3 | both | see | place area (PPA): the paper's label list omits it; its figure 4 shows it on the medial underside, which is PHA1-3. Our reading, not the paper's words |
| visual | V1, V2, V3 | both | see | early visual cortex (the paper's best-predicted areas for video) |
| sound | A1, LBelt, MBelt, PBelt | both | hear | early auditory cortex (the paper's "Auditory (Early)") |
| speech | A5, STSva, STSvp, 45 | left only | understand | paper's language localiser: A5, STS, Broca's area 45 |
| social | PGi, TE1a | both | understand | paper's emotional-vs-physical-pain contrast: TPJ, MTG |

- **Map files.** `lh.HCPMMP1.annot` and `rh.HCPMMP1.annot` (fsaverage, figshare files 5528816 and 5528819, the copies MNE's `fetch_hcp_mmp_parcellation` downloads). `load_model` downloads them once into `data/hcp_mmp/` (git-ignored, like `data/nilearn/`) and checks their SHA-256 (lh `d4da6346…2b44`, rh `744eff4e…e9a2`, full values as constants in `response.py`); a mismatch or failed download raises, so the worker never runs on a changed map. On AWS they sit with the model weights in `s3://<bucket>/models/data/hcp_mmp/` (copied there once; a `--refresh-weights` image build would upload them too), so every worker gets them in its start-up weight sync and never depends on figshare; the download is the fallback. They are never committed and never in a Docker image. They are read with `nibabel.freesurfer.read_annot` (installed with nilearn); mne is not needed.
- **fsaverage5.** fsaverage5 is the first 10,242 vertices of each fsaverage hemisphere, so each hemisphere's label array is cut to its first 10,242 entries (as tribev2 does).
- **Licence.** The map is HCP data under the WU-Minn HCP Open Access Data Use Terms: no attempt to identify subjects, redistribution only under the same terms, and the HCP acknowledgement in the report (§11).
- **Exact match.** A name is normalised by dropping the `L_`/`R_` prefix and the `_ROI` suffix (bytes names decoded first); an area matches only when the normalised name equals the target. Every target must exist in both hemispheres' maps: `build_masks` raises `ValueError` naming any that does not. `require_nonempty_masks` stays. Sizes on fsaverage5 (left + right) range from 29 vertices (bodies) upward.
- **Disjoint.** No vertex belongs to two signals.
- **Values.** For each second, the signed mean of the prediction over the signal's vertices: no absolute value, no rescaling.
- **Groups.** see = mean(faces, bodies, scenes, visual); hear = sound; understand = mean(speech, social). **overall** = mean(see, hear, understand). Groups are means of the signal series, not of pooled vertices, so a large region does not outweigh a small one.
- **Series order** (used by the result, the CSV and the page): faces, bodies, scenes, visual, sound, speech, social, see, hear, understand, overall. `SERIES` holds it.
- **Muted.** The same 11 series computed from the no-audio pass, cut to the shared length of the two passes (normally equal).
- **Rounding.** Every value to 3 decimals, computed from the unrounded series.
- `extract_response(preds_full, preds_noaudio, masks)` returns `{format, duration_seconds, signals, muted}` (§3). It is pure numpy.

## 3. Result format 2 (`results/<job_id>.json`)
```
{"format": 2, "duration_seconds": T,
 "signals": {"faces": [T numbers], ..., "overall": [T numbers]},
 "muted":   {"faces": [M numbers], ..., "overall": [M numbers]},
 "job_id": ..., "processing_time_seconds": ..., "gpu"?: ..., "fake_inference"?: true}
```
- T = rows of the with-audio pass; M = min(T, rows of the no-audio pass).
- The `timesteps`/`regions` structure is retired. NaN or infinity still fails the job before anything is published (`put_result`, unchanged).

## 4. Old results
- `owned_result` answers **410 `old_result`** ("This result was made by an older version of NeuroLens. Upload the video again.") for a stored result without `"format": 2`. This covers the JSON, CSV and brain routes.
- The page shows that message in place of the chart. History still lists the job, and per-video delete still works on it.

## 5. Raw brain file (`results/<job_id>.brain`)
- **Content:** the with-audio predictions, float16 little-endian, row-major, shape (T, 20484), no header. A reader gets T from the byte length ÷ (20484 × 2) and rejects a length that does not divide.
- **Size:** about 41 KB per second of video (4.9 MB for the 120 s maximum).
- **Writer:** `storage.put_brain(s3, bucket, job_id, preds)` casts to float16 and raises `ValueError` before writing if any value is not finite after the cast (float16 overflows above 65,504). `storage.brain_bytes(preds)` does the cast and check, so the dataset copy uses the same bytes.
- **Write order in the worker** (after the `saving_result` claim check, M3c §3b): `put_brain`, then `put_result`. A failed brain write goes down the normal failure path (retry, then refund), and no result is published. A result therefore always has its brain file.
- **Redelivery:** the brain write replaces an earlier copy; `put_result` still never replaces a result. Known limitation: after a redelivery the brain file can come from a later run than the published result. The two differ only by GPU rounding noise.
- **Lifecycle and access:** it sits under `results/`, so the existing 30-day rule and the worker/web IAM already cover it. No Terraform change for this.
- **Dataset:** `storage.put_dataset` gains a `brain` argument (the bytes) and also writes `dataset/<job_id>/brain`.
- **Deletion:** `_job_keys` in `neurolens/web/app.py` adds `results/<job_id>.brain` and `dataset/<job_id>/brain`, so per-video and account delete remove both.

## 6. Summary (`results.summarise(result)`)
A pure function, computed by `/api/jobs/<id>/result` at serve time and added to the response as `summary`. It is never stored, so a rule change needs no rerun.

There is no reference set of videos to say how big a "normal" rise or dip is, so the summary makes no claims against typical viewing. It compares each video with itself, which is always true and is what an editor acts on. The chart's zero line still shows where typical is. The model's first second (t = 0) has no context, so everything below skips it unless T = 1.
- **Opening** (`opening`): the mean overall over seconds 1-3 (fewer if the video is shorter; t = 0 alone when T = 1) against the mean over seconds 4 to T-1: `{value, rest, word}`. `word` is "stronger than the rest" when value − rest > 0.25 × the standard deviation (population, numpy's default) of overall over seconds 1 to T-1, "weaker than the rest" when it is below −0.25 × that, otherwise "about the same as the rest". `rest` and `word` are `null` when there are no seconds after 3 (T ≤ 4) or the standard deviation is below 1e-6; the page then says "too short to compare".
- **Moments:** candidates are the 3-second windows starting at t ≥ 1 (a window, not a single second, because one second is noisy and a marketer thinks in shots). A video shorter than 4 seconds has one candidate, seconds 1 to T-1 (t = 0 alone when T = 1). Each moment is `{start, end, value}`, end exclusive, value = the window's mean overall.
  - **Strongest moments** (`strong_moments`): up to 2, picked greedily by highest mean. A later pick leaves a gap of at least 2 seconds to every earlier (grown) pick (its start ≥ the earlier end + 2, or its end + 2 ≤ the earlier start), so two picks are not the same scene. Ties go to the earliest.
  - **Growing:** each pick then grows one second at a time, taking the stronger neighbour first (the earlier one on a tie), while that second is more than two-thirds of the way from the video's mean overall (seconds 1 to T-1) to the pick's window mean, up to 10 seconds. It never grows into the 2-second gap of an earlier pick, nor below t = 1 or past T. The value is then the grown moment's mean. So moment lengths follow the video; the cap keeps a moment to one scene an editor can find. Two-thirds, not halfway: the predicted response is slow (neighbouring seconds are about 0.9 alike), and halfway grew pure smoothed noise to 5-9 seconds, two-thirds to 4-6. Videos shorter than 4 seconds do not grow.
  - **Weakest moments** (`weak_moments`): up to 2, picked and grown the same way by lowest mean (a second grows in when it is more than two-thirds of the way down), also skipping any window, or growth, that overlaps a strongest moment. Empty when nothing is left (very short videos).
  - Both lists are returned in time order. There are never more than 4 moments, so the page never needs a "more" line or a scrolling list.
- **Audio dependence:** over the shared length (from t = 1, like the rest), `1 − Σ max(muted overall, 0) / Σ max(overall, 0)`, clipped to 0-1 and rounded to 2 decimals. It is `null` when the denominator is below 1e-6; the page then says "no strong response to measure".
- The window length (3), the gap (2), the growth fraction (2/3), the length cap (10) and the opening band (0.25) are constants in `neurolens/results.py`, not config: nothing would tune them without a reference set.

### Config block `response` (in `config.json`)
`{"colour_range": 0.5}`: the response at which the 3D map's heat colour is full.
- 0.5 is a starting value; the AWS day (§9) checks it against the real sample runs and adjusts it so the map neither stays grey nor saturates.
- `results.validate_response_config` checks that `colour_range` is a number > 0. `create_app` calls it, so a bad block fails at start-up, not on a user's request.
- `/api/limits` adds `colour_range`, so the page can draw the map, signed in or not.

## 7. Web
- **`GET /api/jobs/<id>/result`:** the stored result plus `summary`.
- **`GET /api/jobs/<id>/result.csv`:** columns `t`, the 11 series, then `muted_` plus each of the 11 (`results.COLUMNS`). Muted cells beyond the shared length are empty.
- **`GET /api/jobs/<id>/brain`:** `owned_result` first (ownership, refunded, expired, 410). Then, if `results/<id>.brain` does not exist, 404 `brain_missing`. Otherwise a 302 to a presigned GET for it, valid for 5 minutes.
- **S3 CORS:** `cors_rule` in `infra/terraform/main.tf` adds `GET`, so the browser can follow the redirect to the bucket.

## 8. App page (`static/app.html`, served at `/app`)
- **Wording:** "engagement" becomes "brain response" everywhere, on the privacy page and the retention guide too. The zero line and every comparison say just "typical" (not "typical TV"). Near the chart: "Predicted response of an average viewer. The dashed line is typical." The consent text drops "engagement model" for "a future model linking brain response to real audience behaviour".
- **Design goal:** a marketer understands the result at a glance, and the page looks good enough that people walking past a demo stand come over. Meta's TRIBE v2 demo is the inspiration (video and brain side by side, the brain changing as the video plays), not a template. No chart carries more than 2 lines. The prototype that settled this layout is on branch `prototype-m3d-page` (layout D).
- **One screen:** on a laptop (from about 1280 × 720 of browser space; checked at 1470 × 830, a MacBook Air) the whole result fits without scrolling. The page is locked to the window height: the left column holds the player, the right column holds the report, and the brain and the main chart take whatever height is left. Below 860 px wide or 620 px tall it falls back to one scrolling column.
- **Look:** a dark "stage" for the player column and the top bar, and a light, calm report. The heat orange is the data colour (brain, main line, strongest parts) and the play button; it is not used as decoration (the logo is plain text, the upload button white). Headlines and numbers in Bricolage Grotesque, text in Public Sans (Google Fonts, like the current page's fonts).
- **Player (left column):** the clip title (and a sample's attribution line), the video, the 3D brain under it, and one shared timeline: play/pause, one second back/forward, sound on/off, and a scrubber drawn over a small overall curve with the moments shaded. The scrubber, the video, the brain and every chart's playhead move together (the existing `syncPlayhead`). Samples start playing muted on their own, so the stand's screen moves; uploads wait for Play. A result opened from History has no local video (uploads are not replayed: they are deleted within 3 days, or after 12 months when consented): the video area says so truthfully ("This video isn't replayed here. Uploads are deleted within 3 days (12 months if you agreed to keep it for research)."), and play moves the brain through the seconds on its own, at real-time speed. Brain colours blend smoothly between whole seconds (linear between the two neighbouring seconds), so the map flows instead of jumping.
- **Top bar:** the logo (to `/`) and the header line, then, signed in: the credit, the email as a button that opens a small menu ("Sign out", "Delete account"), and the white "Upload a video" (opens the file picker and switches the chooser to "Your analyses", where the upload panel shows). Signed out: only a white "Sign in" (to `/login`). No other navigation buttons. During start-up, until `GET /api/me` answers, none of these show.
- **Delete account:** a dialog opened from the account menu, with the existing text, the type-DELETE field and the Delete button (same `POST /api/account/delete` and messages). Cancel, Escape or a click outside closes it. It replaces the box in the left column.
- **Chooser (left column while no video is open):** signed in, two tabs at the top, "Your analyses" (the default) and "Samples", and only the chosen tab's content shows. "Your analyses" holds the upload box, the upload panel and the no-credit note, then the list of past jobs ("No analyses yet." when there are none). "Samples" holds the sample cards ("Sample videos coming soon." while the list is empty). Back from a video returns to the tab it was opened from. Signed out there are no tabs: the Sign in box, then the samples. The privacy link stays at the column's foot.
- **Report (right column), top to bottom:**
  - **Moments, top row:** two small groups side by side, "Strongest parts" (headed "Good spots for the key message") and "Weakest parts" ("Worth a look in the edit"), each listing its moments in time order as a small frame from the video at the moment (when the local video is there; none from History) and the time range. The hint is written once per group, never per row. Clicking a row seeks. A group with no moment says "Too short to pick one".
  - **Beside them:** Opening (its word, and "the first 3 seconds against the rest") and Audio dependence (the percentage, a plain bar filled to it, and "of the response comes from the sound").
  - The M3c "Add real audience retention" box (consented uploads) sits at the bottom of the report, not in the player column, so nothing below the brain competes with it. The top bar does not repeat the clip name (the player shows it).
  - No summary sentence on top: it would repeat the moments in a templated voice.
  - **Main chart:** the overall line, with a dashed zero line, named "typical" in the chart's legend (not inside the plot, where lines would cross the label). Each moment is shaded (orange for strongest, grey for weakest) with a small time pin above it; clicking a pin or the chart seeks. Straight segments between seconds (no curve smoothing, which overshoots). Its only optional second line is the muted overall ("Show without sound") or the compared video, not both.
  - **Sizing:** the main chart and the strips share the leftover height so the strips stay readable (roughly main chart 65%, strips 35%); the main chart never takes most of the column.
  - **Group strips:** three small charts (Seeing, Hearing, Understanding), one line each, on a shared y range. "Show parts" on Seeing or Understanding swaps the three strips for that group's signals (faces, bodies, scenes, visual; speech, social), one small chart each, side by side across the full width of the same space (never bunched to one side), on one y range shared by the parts shown (both videos when comparing) so no line is cut off, with a button back to all three. Clicking any chart seeks.
- **3D brain:**
  - three.js plus OrbitControls, vendored at one pinned release (like `static/chart.min.js`).
  - The mesh is `static/brain/fsaverage6_pial.bin` (committed), made once by `infra/make_brain_mesh.py`: the folded (pial) surface at fsaverage6 detail, so it looks like a real brain. The model's values exist only at the 20484 fsaverage5 points; fsaverage6 is fsaverage5 with each triangle split once, so every fsaverage6 vertex is either an fsaverage5 point or the midpoint of an edge between two of them. The script finds each vertex's two fsaverage5 parents (equal for an fsaverage5 point) from the sphere surfaces and checks the nesting (fsaverage5 points match exactly; each midpoint is equidistant from its parents); the page colours a vertex by the mean of its parents' values. Layout, little-endian: uint32 vertex count V, uint32 face count F, float32 xyz × V, uint32 × 3 × F, uint8 × V (1 = no cortex: Destrieux `Medial_wall` or `Unknown`, mapped from the parents), float32 × V (sulcal depth, scaled to -1..1, for the fold shading), uint32 × 2 × V (parents). The file is about 4 MB, downloaded once and cached. The script fetches fsaverage6 through nilearn once (a public download) and stops if it cannot.
  - Look: white brain (folds shaded: ridges white, grooves light grey) on a near-black panel, like Meta's demo, a three-quarter front view, slowly orbitable (no zoom; the view fits the panel). A hot scale like Meta's (dark red, red, orange, yellow, pale yellow-white at the top, so a strong spot glows from red edges to a bright centre instead of flattening into one colour) shows only above-typical response, so hotter always means stronger; it fades in from the white near 0 and is full at `colour_range`. Below-typical stays white (the charts show it). No-cortex vertices are a soft grey.
  - A faint see-through head around the brain (edges glow, the face is clear): `static/brain/head.bin` (same header and float32 xyz and face layout as the mesh, nothing after the faces), made by the same script as the outer surface of the MNI152 2009 template that ships inside nilearn (no download). Its licence (permissive, keep the copyright notice) is checked before use and the notice goes in `static/brain/LICENSE.txt`. Meta's assets are not reused (no licence).
  - The brain file is decoded with a small float16 decoder. If it is missing or malformed, the panel says the 3D view is not available for this video; the rest of the page works.
- **Compare:** "Compare with…" picks another done job from History. Its overall and groups become the dashed second line in the main chart and each strip. Both videos are named by their file names everywhere (legend and table header; never "This video"/"Other video" alone). The time axis spans the longer video; where the shorter one ends, its dashed line stops at a dot, joined by a dotted line to a label at the bottom of the plot ("<name> ends"), so the label never covers a line. Only this video's moments are shaded, and the legend says so ("shaded: moments of <name>"). The moments list and its neighbours are replaced, in the same space, by a two-column table (this video | other video: opening, strongest moments, weakest moments, audio dependence). Choosing it turns off "Show without sound". Signed-out visitors see no Compare.
- **Samples** render like a job result, and show their attribution line (CC BY requires it).

## 8b. Landing page (`static/index.html`) and the app at `/app` (`static/app.html`)
- **Why:** visitors who are not signed in (interviewers, people at the demo stand) need to see what NeuroLens does before signing in. The prototype that settled this is on branch `prototype-landing` (layout A).
- **Routes:** the landing page is the front page: `static/index.html`, served at `/` (Flask locally; on AWS it is CloudFront's `index.html` as today). The app page moves unchanged from `static/index.html` to `static/app.html`, served at `/app` (Flask route; on AWS `deploy_web.sh` uploads it to the key `site/app` with `Content-Type: text/html; charset=utf-8` and `Cache-Control: no-cache`, like the front page). `/app` is public, like `/`. After sign-in, `/auth/callback` redirects to `/app`. Sign-out still returns to `/`, which is now the landing page. The app's logo links to `/`.
- **Signed-out visitors at `/app`** (from "Explore samples") see only what they can use (§8 Top bar and Chooser).
- **Signed-in visitors at `/`:** the landing page is a static file, so the server cannot check who is signed in before sending it. The page shows at once for everyone. In the background it asks `GET /api/me`; on a 200 the top bar's Sign in button becomes "Open NeuroLens" (to `/app`) and the hero's and the closing band's sign-in buttons become "Upload a video" (to `/app`, the hero's turning white), and every "Explore samples" button is hidden, since it would open the same page. No automatic redirect: a page that jumps away after a second looks broken. Signed-out visitors never touch the database (`/api/me` answers 401 before any query).
- **Layout (A, one column of sections):**
  - **Top bar:** the plain-text logo; "Explore samples" (outline, to `/app`) and "Sign in" (white, to `/login`).
  - **Hero:** the dark stage at least the window's height minus the top bar. Left: the headline "Watch a brain watch your video.", the line "NeuroLens predicts how an average viewer's brain responds to a video, second by second: which areas light up for faces, places, speech and sound.", the buttons "Explore samples" (white, to `/app`) and "Sign in to upload" (outline, to `/login`), and the sample's attribution line. Right: the sample's video above its 3D brain (brain taller than the video, about 45/55), the brain with the app's legend ("typical" to "higher"), the second counter and "drag to turn".
  - **How it works** (light background, heading left, three numbered steps right): "Upload a clip" (any video up to the upload limit, from `GET /api/limits`, in minutes; sample videos are free to explore without an account); "A brain model predicts the response" (TRIBE v2, a research model from Meta trained on brain scans of people watching films, estimates activity at 20,484 points across the brain for every second); "Read the report" (curves for seven brain areas, the strongest and weakest moments, and a 3D brain that replays with your video).
  - **Closing band:** a dark rounded block, "See it on your own video." with "Sign in to upload" and "Explore samples".
  - **Footer:** Privacy policy link; "Brain model: TRIBE v2 by Meta, CC BY-NC 4.0" (the licence's credit); the sample's attribution.
  - No limits or warnings section: the hero line already says it predicts an average viewer, and the app's report explains the scale.
  - Same theme, fonts and colours as the app (§8 Look). Below 860 px wide everything stacks in one column (copy, video, brain at about 300 px tall, steps one under another).
- **Hero sample:** the first entry of `data/samples.json`, with its video, its brain file and `colour_range` from `GET /api/limits` (public). The video plays muted, on loop, on its own; clicking it pauses or resumes. The brain follows the video's time, blending between seconds as in the app, and turns slowly (not with reduced motion). If there is no sample (the list is empty until the AWS day) or `samples.json` fails to load, the player column shows one quiet panel: "A sample video is coming soon." If only the brain fails (file, mesh or limits), the brain panel says the 3D view is not available and the video still plays.
- **One brain viewer:** the 3D brain code (mesh and head loading, the hot scale, painting, the camera and orbit) moves from the app page into one shared ES module, `static/brain_view.js`, used by both pages, so a look change is made once. Behaviour in the app is unchanged.

## 9. Samples, deploy and calibration
- **Remove:** the 14 current entries in `data/samples.json` and their thumbnails under `data/output/`. The git-ignored third-party videos stay on Josh's laptop, unused.
- **New samples:** the Sintel trailer, and Big Buck Bunny re-encoded to about 720p (a SOURCES.md row). Tears of Steel is optional (CC BY 3.0, live action with faces and speech); if used, it gets a SOURCES.md row too.
- **`infra/make_sample.py <job_id> <video> <title> <attribution>`** (operator script, `AWS_PROFILE=neurolens`):
  - Downloads that job's result and brain file into `data/output/<id>/` (`<id>` = the video's file stem), grabs a thumbnail at 1 s with ffmpeg, and adds or replaces the entry in `data/samples.json`.
  - The entry is `{id, title, description, attribution, video_file, thumbnail, brain_file, result, summary}`, with the summary computed from the current `config.json`. Re-run it after a threshold change.
  - `.gitignore` adds `/data/output/**/brain`.
- **`deploy_web.sh`:** publishes only samples whose clip is listed in SOURCES.md (unchanged) and also copies each one's brain file. It uploads `static/app.html` as `site/app` (§8b).
- **AWS day** (cost estimate first: about $3-5, GPU about 1.5 h at $1.861/h plus NAT and Aurora; Josh confirms remaining credits):
  - One batch of Josh's manual steps: Terraform apply (M3c plus CORS, `nat_gateway=true`), migration 003, `deploy_code.sh`, `deploy_web.sh`.
  - Real GPU jobs on each sample clip, one of them with consent.
  - **Stop check:** the sample values must be finite and centred near 0 (median overall within ±0.3). If not, the built-in-scale assumption is wrong: stop and rethink the wording before going further.
  - **Colour range:** read the value ranges from the brain files and set `colour_range` so the samples' maps neither stay grey nor saturate. Commit, run `make_sample.py` for each sample, and redeploy the web.
  - Finish: `stop_work.sh` and the NAT-off apply.
- No new worker image is needed: `deploy_code.sh` delivers the new code. The image-build smoke script (`infra/build_ami.sh`) calls `extract_response`.

## 10. Tests
Existing tests that pin the old format (`timesteps`, `regions`, `engagement_overall`, the 0-1 rescale) are **rewritten** to format 2. This is the intended change this spec approves, not editing tests to make them pass. The old golden file `tests/fixtures/extract_engagement_golden.json` is **replaced** by `extract_response_golden.json`, regenerated from the new code.
- **Masks:** name normalisation (`L_FFC_ROI` matches `FFC`); exact names (a near-miss does not match; an area missing from either hemisphere raises); the cut to 10,242 vertices per hemisphere; speech only on left vertices; no overlap; none empty. Tests build a small synthetic annot file and make no network call; a wrong SHA-256 raises.
- **Values:** signed means with no rescaling (a region with negative predictions gives negative values); groups and overall from the signal series; the muted length cut; 3-decimal rounding; series order.
- **`summarise`:** the opening's three words and its band edge; `rest`/`word` null for T ≤ 4 and for a flat line; strongest and weakest picks with the 2-second gap (a second-best window next to the best is skipped), the earliest winning a tie, weak picks skipping windows that overlap a strong one, time order; growing (a plateau grows to its edges, a lone spike stays 3 seconds, growth stops at the 10-second cap, at the gap to an earlier pick and at a strong moment); T = 1, 2, 3, 4 and 7; the null audio dependence; clipping to 0-1.
- **Config:** `validate_response_config` refuses a missing, non-numeric or non-positive `colour_range`; `create_app` refuses a bad block.
- **CSV:** the column list; empty muted cells beyond the shared length.
- **Web:** 410 for an old result on the JSON, CSV and brain routes; the brain route's ownership (another user gets 404, a refunded job gets 404), `brain_missing`, and the 302 to a presigned URL for the right key.
- **Deletion (full):** `_job_keys` includes both brain keys; per-video and account delete remove them (moto).
- **Worker (full):** the brain is written before the result; a failed brain write publishes no result and goes down the failure path; a non-finite value (or a float16 overflow) is refused before any write; a consented job writes `dataset/<job_id>/brain` with the same bytes.
- **Samples:** `test_samples_schema` checks the new entry shape and that every sample's brain file length divides by 20484 × 2.
- **Mesh:** the committed mesh file parses: header counts match the length, face indices in range, both parent indices of every vertex are < 20484, and exactly 20484 vertices are their own parent (the fsaverage5 points).
- **Landing (light):** `/app` serves `static/app.html` and is public (signed out, Cognito mode); `/` still serves `static/index.html`; `/auth/callback` redirects to `/app`. The public-routes test adds `/app` to its list (Josh approved, 22 Oct). The page itself is checked by a walk, not unit tests.
- **Break-its** (on committed code): drop a brain key from `_job_keys`, and remove the ownership check from the brain route. A test must fail each time.

## 11. Docs (current-state only)
- `AGENTS.md`: the layout names `response.py`; the normalisation rule becomes "signed, no rescaling; muted cut to the shared length"; the golden-file rule names `extract_response` and its new fixture.
- `README.md`: the result-format section, and the brain-area map (Glasser HCP-MMP1, downloaded on first worker start).
- Report acknowledgement (HCP terms): "Data were provided [in part] by the Human Connectome Project, WU-Minn Consortium (Principal Investigators: David Van Essen and Kamil Ugurbil; 1U54MH091657) funded by the 16 NIH Institutes and Centers that support the NIH Blueprint for Neuroscience Research; and by the McDonnell Center for Systems Neuroscience at Washington University." plus a citation of Glasser et al. 2016.
- `static/privacy.html`: the brain output is part of the result and of the dataset copy, and delete removes it.
- M1/M2a/M3b sections that describe the old format get a one-line pointer to this spec.
- Close-out: report wording (the pitch "virtual brain-response pre-test", honest limits, future work), architecture page, learning log, ai_log.
