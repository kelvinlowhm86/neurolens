# NeuroLens — M3d Implementation Spec: brain response

**Builds on:** M1–M3c. The TRIBE v2 model, the GPU run and the architecture stay as they are. What changes is how its output is read and shown. NeuroLens stops selling an "engagement score" and shows a **predicted brain response**: second by second, how strongly an average viewer's brain would respond to the video, relative to typical film and TV viewing. It never claims to predict views, retention or sales.

**Light treatment** (normal review, one real run), except deletion and the worker's write order (a missed key leaves user data behind; a half-written job could publish a result without its brain file): those get tests first by a separate agent and the break-its in §10.

## Decisions (21 Oct)
- **Why the change.** The five old regions detect content (faces, bodies, scenes, social cues, sound), not interest. The old score took the absolute value of each region's mean (so a dip counted as a rise) and stretched every video to 0-1 on its own, so two videos could not be compared. Two outside attempts to predict engagement from TRIBE output failed (r = 0.25 on 24 videos; a null result on 48 in arXiv 2607.01400).
- **Built-in scale.** TRIBE was trained on fMRI responses z-scored per vertex, so its raw output already has a reference: 0 means typical film/TV viewing. Values are shown signed and unscaled.
- **No lag fix.** tribev2 already shifts its predictions 5 s back to cancel the blood-flow delay (its README and demo), so second *t* of the result is second *t* of the video.
- **7 signals in 3 groups**, an overall line, a muted (no-audio) copy, summary cards, a 3D brain map, and A-vs-B comparison against the user's own videos.
- **The raw brain output is stored** (with-audio pass), so the map and any later change to the regions need no GPU rerun.
- **No value or persuasion signal**: the model gives no evidence for one.
- **Samples** become open-licensed Blender clips; the 14 third-party samples are removed.
- **One AWS day** deploys M3c and M3d together (§9).

## 1. Scope
### In scope
The new maths (`neurolens/response.py`, replacing `neurolens/engagement.py`); result format 2; the raw brain file; the summary; the brain route; the 410 for old results; S3 CORS; the CSV; config `response`; the page (cards, chart, muted overlay, 3D map, compare, wording); `infra/make_brain_mesh.py`; `infra/make_sample.py`; `deploy_web.sh`; new samples; docs.

### Out of scope
- Linking the response to real audience behaviour (future work in the report, using the consented retention curves M3c collects).
- An ad benchmark library (future work).
- Changes to the model, the GPU passes, billing or the job lifecycle.
- The two notebooks: they carry their own copy of the old maths and never import it, so they keep working as they are.
- Converting old stored results: they expire within 30 days (§4).

## 2. Signals (`neurolens/response.py`)
Destrieux atlas labels (fsaverage5, as now). Vertex order is the left hemisphere (indices 0-10,241) then the right (10,242-20,483), as the model outputs them.

| Signal | Labels | Hemisphere | Group |
|---|---|---|---|
| faces | G_oc-temp_lat-fusifor | both | see |
| bodies | S_oc-temp_lat, G_temporal_inf | both | see |
| scenes | G_oc-temp_med-Parahip | both | see |
| visual | S_calcarine, G_cuneus, Pole_occipital, G_oc-temp_med-Lingual | both | see |
| sound | G_temp_sup-G_T_transv, G_temp_sup-Plan_tempo | both | hear |
| speech | G_front_inf-Opercular, G_front_inf-Triangul, G_temporal_middle, G_temp_sup-Lateral | left only | understand |
| social | S_temporal_sup | both | understand |

- **Exact match.** A label matches only when its name equals the target (bytes names are decoded first). Every target label must exist in the atlas: `build_masks` raises `ValueError` naming any that does not. `require_nonempty_masks` stays.
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

## 6. Summary (`results.summarise(result, response_cfg)`)
A pure function, computed by `/api/jobs/<id>/result` at serve time and added to the response as `summary`. It is never stored, so a threshold change needs no rerun. Cards:
- **Hook:** over seconds 0-2 (fewer if the video is shorter), the group (see, hear, understand) with the highest mean: `{group, value, level}`.
- **Peak:** the second with the highest overall, skipping t = 0 (the model's first second has no context) unless T = 1; the earliest wins a tie: `{t, value, level}`.
- **Dead stretches:** every run of at least `dead_min_seconds` consecutive seconds where overall is below `-dead_threshold`: a list of `{start, end}`, end exclusive. Empty when there are none.
- **Audio dependence:** over the shared length, `1 − Σ max(muted overall, 0) / Σ max(overall, 0)`, clipped to 0-1 and rounded to 2 decimals. It is `null` when the denominator is below 1e-6; the page then says "no strong response to measure".
- **Level words**, from the value v: v ≥ `well_above` → "well above typical"; v ≥ `above` → "above typical"; v ≤ `below` → "below typical"; otherwise "typical".

### Config block `response` (in `config.json`)
`{"well_above": 0.5, "above": 0.2, "below": -0.2, "dead_threshold": 0.2, "dead_min_seconds": 3, "colour_range": 0.5}`
- These numbers are **placeholders** until the calibration run (§9). M3d is not done, and nothing is demoed, until calibration has replaced them.
- `results.validate_response_config` checks them: numbers, `well_above > above > below`, `dead_threshold > 0`, `dead_min_seconds` an integer ≥ 1, `colour_range > 0`. `create_app` calls it, so a bad block fails at start-up, not on a user's request.
- `/api/limits` adds `colour_range`, so the page can draw the map, signed in or not.

## 7. Web
- **`GET /api/jobs/<id>/result`:** the stored result plus `summary`.
- **`GET /api/jobs/<id>/result.csv`:** columns `t`, the 11 series, then `muted_` plus each of the 11 (`results.COLUMNS`). Muted cells beyond the shared length are empty.
- **`GET /api/jobs/<id>/brain`:** `owned_result` first (ownership, refunded, expired, 410). Then, if `results/<id>.brain` does not exist, 404 `brain_missing`. Otherwise a 302 to a presigned GET for it, valid for 5 minutes.
- **S3 CORS:** `cors_rule` in `infra/terraform/main.tf` adds `GET`, so the browser can follow the redirect to the bucket.

## 8. Page (`static/index.html`)
- **Wording:** "engagement" becomes "brain response" everywhere, on the privacy page and the retention guide too. Near the chart: "Predicted response of an average viewer, relative to typical film and TV viewing." The consent text drops "engagement model" for "a future model linking brain response to real audience behaviour".
- **Cards on top:** hook, peak, dead stretches, audio dependence.
- **Main chart:** overall and the 3 groups. A dashed zero line is labelled "typical viewing", and the peak and dead stretches are shaded. Clicking a group shows its signals.
- **Muted toggle:** overlays the muted overall.
- **3D brain:**
  - three.js plus OrbitControls, vendored at a pinned release (like `static/chart.min.js`).
  - The mesh is `static/brain/fsaverage5_inflated.bin` (committed), made once by `infra/make_brain_mesh.py` from nilearn's fsaverage5 inflated surfaces (they ship inside the nilearn package; no download). Layout, little-endian: uint32 vertex count (20484), uint32 face count, float32 xyz per vertex, uint32 three indices per face, uint8 per vertex (1 = no cortex: Destrieux `Medial_wall` or `Unknown`). The hemispheres are offset side by side. The script checks that the vertex count is 20484 and that every face index is in range.
  - Colours: a diverging scale, blue below typical and red above, fixed at ±`colour_range`. No-cortex vertices are grey. Colours follow the video's playhead (the existing `syncPlayhead`).
  - The brain file is decoded with a small float16 decoder. If it is missing or malformed, the panel says the 3D view is not available for this video; the rest of the page works.
- **Compare:** "Compare with…" picks another done job from History. It overlays that job's overall and groups (dashed) and shows both sets of cards side by side. Signed-out visitors see no Compare.
- **Samples** render like a job result, and show their attribution line (CC BY requires it).

## 9. Samples, deploy and calibration
- **Remove:** the 14 current entries in `data/samples.json` and their thumbnails under `data/output/`. The git-ignored third-party videos stay on Josh's laptop, unused.
- **New samples:** the Sintel trailer, and Big Buck Bunny re-encoded to about 720p (a SOURCES.md row). Tears of Steel is optional (CC BY 3.0, live action with faces and speech); if used, it gets a SOURCES.md row too.
- **`infra/make_sample.py <job_id> <video> <title> <attribution>`** (operator script, `AWS_PROFILE=neurolens`):
  - Downloads that job's result and brain file into `data/output/<id>/` (`<id>` = the video's file stem), grabs a thumbnail at 1 s with ffmpeg, and adds or replaces the entry in `data/samples.json`.
  - The entry is `{id, title, description, attribution, video_file, thumbnail, brain_file, result, summary}`, with the summary computed from the current `config.json`. Re-run it after a threshold change.
  - `.gitignore` adds `/data/output/**/brain`.
- **`deploy_web.sh`:** publishes only samples whose clip is listed in SOURCES.md (unchanged) and also copies each one's brain file.
- **AWS day** (cost estimate first: about $3-5, GPU about 1.5 h at $1.861/h plus NAT and Aurora; Josh confirms remaining credits):
  - One batch of Josh's manual steps: Terraform apply (M3c plus CORS, `nat_gateway=true`), migration 003, `deploy_code.sh`, `deploy_web.sh`.
  - Real GPU jobs on each sample clip, one of them with consent.
  - **Stop check:** the sample values must be finite and centred near 0 (median overall within ±0.3). If not, the built-in-scale assumption is wrong: stop and rethink the wording before calibrating.
  - **Calibration:** read the value ranges from the brain files and set the `response` thresholds and `colour_range` so the samples' cards read sensibly. Commit, run `make_sample.py` for each sample, and redeploy the web.
  - Finish: `stop_work.sh` and the NAT-off apply.
- No new worker image is needed: `deploy_code.sh` delivers the new code. The image-build smoke script (`infra/build_ami.sh`) calls `extract_response`.

## 10. Tests
Existing tests that pin the old format (`timesteps`, `regions`, `engagement_overall`, the 0-1 rescale) are **rewritten** to format 2. This is the intended change this spec approves, not editing tests to make them pass. The old golden file `tests/fixtures/extract_engagement_golden.json` is **replaced** by `extract_response_golden.json`, regenerated from the new code.
- **Masks:** exact labels (a near-miss name does not match; a missing label raises); speech only on left vertices; no overlap; none empty.
- **Values:** signed means with no rescaling (a region with negative predictions gives negative values); groups and overall from the signal series; the muted length cut; 3-decimal rounding; series order.
- **`summarise`:** each card; ties; T = 1, 2 and 3 for the hook and peak; a dead stretch at the very start and at the very end; one exactly `dead_min_seconds` long and one a second shorter; the null audio dependence; clipping to 0-1.
- **Config:** each `validate_response_config` rule refuses a bad value; `create_app` refuses a bad block.
- **CSV:** the column list; empty muted cells beyond the shared length.
- **Web:** 410 for an old result on the JSON, CSV and brain routes; the brain route's ownership (another user gets 404, a refunded job gets 404), `brain_missing`, and the 302 to a presigned URL for the right key.
- **Deletion (full):** `_job_keys` includes both brain keys; per-video and account delete remove them (moto).
- **Worker (full):** the brain is written before the result; a failed brain write publishes no result and goes down the failure path; a non-finite value (or a float16 overflow) is refused before any write; a consented job writes `dataset/<job_id>/brain` with the same bytes.
- **Samples:** `test_samples_schema` checks the new entry shape and that every sample's brain file length divides by 20484 × 2.
- **Mesh:** the committed mesh file parses: 20484 vertices, face indices in range, medial flags present.
- **Break-its** (on committed code): drop a brain key from `_job_keys`, and remove the ownership check from the brain route. A test must fail each time.

## 11. Docs (current-state only)
- `AGENTS.md`: the layout names `response.py`; the normalisation rule becomes "signed, no rescaling; muted cut to the shared length"; the golden-file rule names `extract_response` and its new fixture.
- `README.md`: the result-format section.
- `static/privacy.html`: the brain output is part of the result and of the dataset copy, and delete removes it.
- M1/M2a/M3b sections that describe the old format get a one-line pointer to this spec.
- Close-out: report wording (the pitch "virtual brain-response pre-test", honest limits, future work), architecture page, learning log, ai_log.
