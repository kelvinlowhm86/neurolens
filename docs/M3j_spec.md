# NeuroLens — M3j Implementation Spec: checked publish dates, link check first, honest sharing wording

Status: current design. Builds on M3i (curve collection) and M3f §5 (sharing and curves). Where this
spec and an older one disagree, this one wins, and the older section is updated to match (§10).

## Decisions (2 Nov, Josh)
- For YouTube the server looks the video up (YouTube Data API, API key) for its publish date,
  length and view count. Facebook links, and YouTube lookups that fail, supply unknown facts. A
  checked view count must reach 1,000 before the curve pays; unknown facts skip the check.
- One set of reward rules for every source. A source supplies the same facts (publish date, video
  length, view count) or "unknown"; the rules treat "unknown" the same way for all sources.
- A YouTube video whose length differs from the analysed video by more than 2 s earns no reward:
  training pairs the analysed file with its curve. The curve is still saved.
- The user learns whether a curve would earn credit before doing any work: the form checks the
  link first and shows the report steps only after.
- Facts we checked replace questions: for YouTube links the form no longer asks views or when it
  was published.
- Wording: sharing is "to help improve NeuroLens"; the course study is no longer a purpose, and the
  extra copy of the result kept for it is gone. The hook is seeing real viewers beside the
  prediction.
- Easier steps: the guide fills in the video's own values with Copy buttons; the report can be
  pasted instead of saved as a file.
- "Connect YouTube" (OAuth) is future work: reading channel analytics needs Google's app
  verification; until then users see an "unverified app" warning and the app is capped at 100 users.

## 1. Scope
### In scope
§2–§9, their tests (§11), the docs (§10).
### Out of scope
Connect YouTube; Facebook publish dates; a low-views warning; prefilled links into Google's API
Explorer (not confirmed to work).

## 2. Settings
- `config.json` `research.curve_length_tolerance_s`: 2.
- The key: `NEUROLENS_YOUTUBE_API_KEY` in `.env` on a laptop; Parameter Store SecureString
  `/neurolens/web/youtube_api_key` for the web function (its role already reads
  `/neurolens/web/*`). `settings.apply_env` copies the variable to `cfg["youtube_api_key"]`;
  `web/lambda_handler.DEPLOYMENT_PARAMETERS` gains `"/neurolens/web/youtube_api_key": (None,
  "youtube_api_key")`, so a deployed function with no stored key fails to start (loud). The key is
  never logged, never in a reply or the page, never in `config.json`.

## 3. YouTube lookup (`neurolens/youtube.py`)
`lookup(video_id, api_key, *, timeout=5) -> VideoFacts | None`, standard library only (`urllib`).
- `GET https://www.googleapis.com/youtube/v3/videos?part=snippet,contentDetails,statistics&id=<id>&key=<key>`.
- No item → `None` (deleted, private, or a mistyped id).
- One item → `VideoFacts(published_at: datetime UTC, duration_s: int, views: int | None)` from
  `snippet.publishedAt`, `contentDetails.duration` (ISO 8601, e.g. `PT31S`, `PT1M5S`, `PT1H2M3S`,
  `P0D`), `statistics.viewCount` (absent when hidden → `None`).
- No key, a network error, a timeout, a non-200 reply, or a reply not in that shape → raises
  `LookupFailed` (message without the key or the URL).
- Measured 2 Nov on a public video: `publishedAt "2024-02-12T17:28:22Z"`, `duration "PT31S"`,
  `viewCount "25048"`; an unknown id gives 200 with no items. The analysed file of that ad is
  30.14 s, so YouTube's whole-second length can differ by about 1 s.

## 4. Facts for a link (`web/app.py`, one helper used by both routes)
- YouTube: `lookup`. `None` → 400 `video_not_found` "We can't see this video. Check the link, or
  add it once it's public." `LookupFailed` → `logger.error` (job id and reason) and facts unknown.
- Facebook: facts unknown.

## 5. Reward rules (`billing`, one helper for the check and the save)
`_curve_reward` takes `facts` (or `None`), `length_tolerance_s` and `min_views` (default
`DEFAULT_MIN_VIEWS = 1000`, passed by `add_curve`, `curve_offer` and both routes from
`research.curve_min_views`), and returns `(cents, reason)`:
- Conditions, in this order (the first unmet one is the `reason`): `invalid` (quality flags, save
  only); `not_charged`; `already_rewarded` (this job, the same video hash, or the same `video_ref`);
  `not_funded`; `cap_reached`; `length_mismatch` (facts known and either the job has no server-measured
  length or `|duration_s - verified_duration_ms / 1000| > length_tolerance_s`); `too_few_views`
  (facts known, `facts.views` not None, and `facts.views < min_views`). A hidden count
  (`facts.views` None) or unknown facts skip the view condition.
- `reason` is `None` when it pays. `too_few_views` returns `cents` (what the curve would pay once
  it qualifies) and the save pays nothing for it.
- `billing.curve_offer(db, user_id, job_id, video_ref, facts, reward_cap, reward_window_days,
  length_tolerance_s, min_views)`: read-only, the same job checks and the same `VideoTaken` /
  `VideoLinkChanged` refusals as `add_curve` (shared code, not copied), then the helper.
- `add_curve` gains `facts`, `length_tolerance_s` and `min_views` and stores `checked_views`,
  `published_at`, `video_seconds` (null when unknown). Its reply adds `reason`.
- `list_jobs` has no `curve_reward_from`. `curve_reward_cents` is unchanged (ignores the link,
  the length and the views).

## 6. Routes
- `POST /api/jobs/<id>/retention/check`, body `{video_url}`: the same job checks as the save
  (404/409), the link (§3 of M3i), the facts (§4), then `curve_offer`. 200 `{platform, video_id,
  reward_cents, reason, published_on, video_seconds, analysed_seconds, checked_views, min_views}`:
  `reward_cents` is what this curve would give now (0 when it cannot); `published_on` is the
  publish date's UTC day as `YYYY-MM-DD`, `video_seconds` the
  YouTube length and `checked_views` its view count (all with facts only, else null);
  `analysed_seconds` is the server-measured length of the analysed video, for the
  `length_mismatch` line, and `min_views` is `research.curve_min_views`. The length rule uses only
  the server-measured length, never the page's. `VideoTaken`/`VideoLinkChanged` → 409 as on
  save. Nothing is written.
- `POST /api/jobs/<id>/retention`: after the link, the facts (§4) (a second lookup: the server
  never trusts the page's). With facts, `views` and `published` must be absent or null and are
  stored null. Without facts they are optional training weights (validated when given), so a
  lookup that fails after the check hid those questions still saves. Then as now, passing `facts`.
- Both routes need a signed-in owner; each check costs 1 unit of YouTube's 10,000 a day.

## 7. Migration 011 (`infra/migrations/011_checked_facts.sql`)
`retention_snapshots`: `checked_views BIGINT`, `published_at TIMESTAMPTZ`, `video_seconds INTEGER`,
all nullable.

## 8. Page (`static/app.html`)
- **Consent box:** "Share to help improve NeuroLens ([details](/static/privacy.html#sharing)). I own
  the rights to it." Quiet line: "Add its retention curve once it's reached 1,000 views: see real
  viewers beside the prediction, and get its credit back." `billing.CONSENT_VERSION` becomes
  `"2026-11-03"`.
- **Entry points:** History row "Get X back", menu "Add audience retention (get X back)". Results
  card: "Thanks for sharing. Once it reaches 1,000 views, add its retention curve: see real viewers
  beside the prediction and get X back."
- **Form, step 1:** the video name and the "Video link" field only. On a valid-looking link
  (debounced ~400 ms, and on paste) the page calls `/retention/check` and shows one line:
  - pays now: "Add its curve now and this analysis is free (X back)."
  - `length_mismatch`: "This video is 45 s; the one you analysed is 30 s. Its curve is welcome,
    but only the analysed video's curve gets credit back."
  - `too_few_views`: "This video has 640 views. Add its curve once it has 1,000 views and get X
    back."
  - `cap_reached`: "You've had 5 rewards in the last 30 days. Its curve is still welcome."
  - other reasons: "Its curve is welcome; this one won't get credit back."
  - refusals (not found, taken, link changed): the server's message, and step 2 stays hidden.
- **Form, step 2** (shown after a non-refused check): the guide link (opens the guide for that
  link, §9), one report box "Paste the reply here" with a small "or choose a file" link that loads
  a file into it, the rows promoted and "Published video" (Same / Lightly edited / Re-cut), and,
  only when the facts are unknown, views and published. Save.
- **Replies after save:** rewarded as now (with the celebration); otherwise the line for its
  `reason` as above ("Thanks, saved." first); quality failures as now.
- Copy sweep: "research copy" → "shared copy" ("Stop sharing this video? Its shared copy is deleted
  now.", the 90-day message, `VIDEO_DELETED`); "This video was not shared for research." → "This
  video was not shared."; the stage label `saving_dataset` → "Saving the shared copy".

## 9. Other pages
- **Guide (`static/retention_guide.html`):** reads `?platform=&v=&start=` from its address. With
  YouTube values it shows the six values filled in (startDate = the day before `start`, as an
  earlier start is harmless and YouTube's days are Pacific time; endDate = today in the viewer's
  time zone; filters `video==<v>`), each with a Copy button; with Facebook, the query with the id
  filled in and a Copy button. Without values, as now. The last steps say "paste the reply into
  NeuroLens" (no saving a file). The YouTube section ends "Add it once the video has 1,000+ views
  to get its credit back."; the Facebook section ends "Add it once the video has 1,000 views to get
  its credit back."
- **Privacy (`static/privacy.html`):** the section is "Sharing a video" (anchor `#sharing`, with
  the old `#research` anchor kept so old links land). Purpose: help improve NeuroLens, by building
  and testing its own engagement models, which may later be a paid product (notice first). The
  course study and the copy of NeuroLens's result kept for it are removed. What is kept adds the
  checked publish date, length and view count, and an access request's channel link and note
  (emailed to the team through AWS, kept until the account is deleted). Reward rules: "the same
  length as the analysed video, give or take 2 seconds", and "when we can check it, has at least
  1,000 views". The test keeping these numbers equal to `config.json` covers 2 and 1,000.
- **Landing (`static/index.html`):** the label "Shared"; "Add a video's retention curve and earn
  its credit back." The "Your videos" column says "Free to try", "Creators and marketers get 5
  free minutes.*" with a "Get free minutes" button to `/login`, and the columns end with
  "*For channels with 1,000+ followers."

## 9b. Removing the course-study copy (`academic/`)
- The worker writes no `academic/<job_id>/` copy: `storage.put_dataset` copies only the video to
  `dataset/<job_id>/`, with no result or brain. NeuroLens's own result and brain stay under
  `results/`.
- The bucket holds no `academic/` objects.
- Nothing else refers to the prefix: `storage.delete_job_prefixes` deletes only
  `dataset/<job_id>/`, there is no S3 lifecycle rule for it, and the worker, Lambda and web IAM
  statements name no `academic/*` resource.

## 10. Older specs updated with this build
M3i §6 (reward conditions, `list_jobs`), §7 (entry points, form, copy), §8, §12;
M3f §5a (box, quiet line, privacy summary, consent version), §5b (shared copy only), §5d (form,
body, replies), M3a, M3c and M3d where they describe the shared copy or its deletion; M3d §8b
landing copy.

## 11. Tests (written first by a separate agent; PostgreSQL for billing)
Full treatment (money). Behaviour, not call details. The lookup is faked at the HTTP boundary.
- `youtube.lookup`: the measured reply → facts (31 s, 25048, the date); `PT1M5S` → 65, `PT1H2M3S` →
  3723, `P0D` → 0; no items → `None`; hidden views → `None` views; HTTP 403/500, a timeout, bad
  JSON, a missing field → `LookupFailed`, and its message never contains the key.
- Reward rules (PostgreSQL, move dates, never sleep):
  - YouTube published 30 days ago, analysed today → rewarded now;
  - published 2 days ago, analysed 10 days ago → rewarded now;
  - unknown facts → rewarded now;
  - length 33 s vs analysed 30.1 s → `length_mismatch`, stored, no reward; 31 s → rewarded;
  - unknown facts skip the length check;
  - the check and the save agree for every case above (same inputs, same reason);
  - the existing M3f/M3i reward tests still pass.
- Routes: check returns each reason; not found → 400 and nothing written; a failed lookup falls
  back and logs an error; the check writes nothing; the save stores `checked_views`,
  `published_at`, `video_seconds`; with facts, `views`/`published` are stored null; without
  facts they are required.
- Settings: the deployed launcher reads the key from Parameter Store and fails to start without
  it; the key never appears in any reply.
- Copy: consent version on the page equals `billing.CONSENT_VERSION`; privacy page 7 and 2 equal
  `config.json`; no page says "for research".
- Worker: a consented job writes the video under `dataset/` and nothing under `academic/`.
- **Break-it checks** (on committed code, restore with `git checkout <file>`): use the analysis
  date even when facts are known; drop the length condition; let the save skip its own lookup and
  accept a fact from the page. Each must turn a test red.

## 12. Known limitations (accepted)
- When the report was downloaded is unknown (the YouTube report holds no date).
- A YouTube link is the user's word that the report is theirs (no video id in the report; Connect
  YouTube is future work). The length check and the one-account-per-video rule limit misuse.
- Facebook: no publish date, length or view count; built from Meta's documentation only
  (M3i §12).
- The check route spends YouTube quota for a signed-in user; 10,000 a day is far above real use.
  Abuse needs a hostile signed-in account (stopping rule).
- No `academic/` copies remain: the worker writes none and nothing else refers to the prefix.
