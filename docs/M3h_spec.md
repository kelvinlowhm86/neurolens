# NeuroLens — M3h Implementation Spec: the comparison report and sample reports

Status: current design. Builds on M3g (the AI report), M3d §9 (samples) and M3f §5c (deletion).
Where this spec and an older one disagree, this one wins, and the older section is updated to
match (§17).

## Decisions (29 Oct, Josh)
- A comparison report reads exactly two videos. A signed-in user can ask for one on two of their
  own done, non-deleted videos that both have frames. Free, written only on a click, at most
  `report.max_tries` (3) tries per pair (only failures use one up), the same stale rule and the
  same writing / ready / failed states and polling as the single report.
- A-vs-B and B-vs-A are the same pair: one report, never two. The pair is stored in one fixed
  order (§2) and the page shows it either way round.
- The same Lambda (`neurolens-report`) writes both kinds; the event says which. Same model and
  config block `report`; nothing is added to it.
- Deleting either video removes every comparison that involves it, in the database and in S3.
- The request sends each video's numbers as one row per second, never as one long list per
  signal: the laptop trial (29 Oct, CeraVe 30 s and 76 s cuts) showed the model misreading long
  lists by position and inventing values, and the per-second table fixed it. The single report
  uses the same table builder (M3g §5b).
- Samples (public, for signed-out visitors) may carry a stored single report, and a pair of
  samples may carry a stored comparison. Both are made once by the operator through the real
  live path in Josh's account and copied into `data/samples.json` by `infra/make_sample.py`.
  Visitors can never start a Bedrock call. All four samples carry a report, and two pairs carry a
  stored comparison (Big Buck Bunny / Sintel, CeraVe 30 s / 76 s).
- On the page the comparison shows in the Report tab while comparing, in the single report's
  style, with the videos named by their names.

## 1. Scope
### In scope
The pair rule (§2), migration 009 (§3), two web routes (§4), the comparison path of the report
Lambda and the shared table builder (§5), deletion (§6), sample reports and sample comparisons
(§7), the page (§8), the privacy page (§9), Terraform and IAM (§10), laptop fake (§11), tests
(§13).
### Out of scope
Comparing more than two videos. Comparing a sample with one of the user's own videos. A sample
visitor asking for a report. Regenerating a finished comparison. A page redesign (later).

## 2. The pair
`storage.comparison_pair(x, y)` returns `(job_a, job_b)`: both ids as canonical lowercase UUID
text, sorted, so `job_a < job_b` (PostgreSQL orders `uuid` the same way as this text). Raises
`ValueError` for a non-UUID or for the same id twice. Every caller (routes, Lambda, laptop fake)
goes through it, so the order is decided in one place.

"A" is always `job_a` and "B" is `job_b`, in the database, the event, the request to the model,
the stored answer and the GET answer. The page maps A and B to the two names (§8); nothing is
ever relabelled after the model has written it.

`storage.comparison_key(job_a, job_b)` = `results/<job_a>_<job_b>.report.json`. It sits under
`results/`, so it expires with the results after 30 days, and it matches the existing
`results/*.report.json` IAM grant (§10). `storage.get_comparison_report(s3, bucket, job_a, job_b)`
reads it like `get_report` (None when missing).

## 3. Migration 009 (`infra/migrations/009_comparison.sql`)
```
CREATE TABLE comparisons (
  job_a      UUID NOT NULL REFERENCES jobs(job_id),
  job_b      UUID NOT NULL REFERENCES jobs(job_id),
  state      TEXT NOT NULL CHECK (state IN ('writing', 'ready', 'failed')),
  tries      INTEGER NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  error      TEXT,               -- a short code, never shown verbatim to the user
  PRIMARY KEY (job_a, job_b),
  CHECK (job_a < job_b)
);
CREATE INDEX comparisons_job_b ON comparisons (job_b);
```
A row exists only once a comparison has been claimed: no row means never asked. No `user_id`:
both jobs are the user's, checked at the claim. The foreign keys have no `ON DELETE CASCADE` on
purpose: rows are removed by the purge (§6), and a row left behind makes
`remove_deleted_account` fail loudly instead of hiding a missed S3 delete.

Billing functions (`neurolens/billing.py`), the Data API rules of M3g apply (`CAST(:x AS uuid)`,
`make_interval(mins => CAST(:stale AS integer))`):
- `claim_comparison(db, user_id, job_a, job_b, max_tries, stale_minutes)`: one transaction.
  First `SELECT job_id FROM jobs WHERE job_id IN (a, b) ORDER BY job_id FOR SHARE` (a racing
  job delete then waits, or the claim sees the delete; §6). Then one
  `INSERT … SELECT … ON CONFLICT (job_a, job_b) DO UPDATE … RETURNING`: the insert row
  (`writing`, `tries = 1`, `started_at = now()`) is proposed only when exactly two jobs match
  `job_id IN (a, b)`, `user_id = :user_id`, `status = 'done'`, `deleted_at IS NULL`, and the user
  is not deleted. On a conflict it sets `writing`, `tries + 1`, `started_at = now()`,
  `error = NULL` only where `tries < max_tries` and the state is `failed` or `writing` started
  more than `stale_minutes` ago. Returns True/False.
- `finish_comparison(db, job_a, job_b, state, error=None)`: sets `ready` or `failed` only where
  the state is `writing` and neither job is deleted; returns whether a row changed.
- `release_comparison_try(db, job_a, job_b)`: sets `failed` and `tries - 1` where `writing`, used
  only when the Lambda could not be started (§4).
- `get_comparison(db, job_a, job_b)`: the row (`state`, `tries`, `started_at`) or None.

## 4. Web routes (`neurolens/web/app.py`)
- `POST /api/compare/<x>/<y>/report`: `owned_job` for each (either fails → its error, 404 when not
  the caller's); `comparison_pair` (same id twice → 400 `same_video`); 404 unless both are done;
  409 `no_frames` unless both results have `frames`. Then, from `get_comparison`: 409
  `report_ready` when ready; 409 `report_writing` when writing and not stale; 429 `no_tries_left`
  when tries are used up. Otherwise `claim_comparison` (False → 409 `report_writing`), then
  `report_starter({"pair": [job_a, job_b]})`. If that raises: `release_comparison_try`, log, 503
  `report_failed`. Answers 202 `{"report_state": "writing"}`.
- `GET /api/compare/<x>/<y>/report`: `owned_job` for each, `comparison_pair`; answers
  `{"a": job_a, "b": job_b, "report_state", "tries_left", "report"}`. No row: state null and all
  tries left. A stale `writing` is answered as `failed`, like M3g §4. `report` is the stored JSON
  when ready, else null.
- `report_starter` now takes the Lambda event, not a job id: `{"job_id": id}` for a single
  report (the existing route), `{"pair": [job_a, job_b]}` for a comparison. The boto3 starter
  in Cognito mode sends it as the payload unchanged.
- Signed-out callers get the usual 401 (every `/api/*` route needs sign-in); there is no
  sample route, so a visitor has no way to reach Bedrock.

## 5. The report Lambda (`neurolens/lambdas/report.py`)
### 5a. Flow
`handler`: `{"pair": [x, y]}` → `write_comparison(job_a, job_b, db, s3, bucket, client, cfg)`
(the pair goes through `comparison_pair` again); `{"job_id": …}` → `write_report` as today.
`write_comparison` is `write_report`'s flow on the pair:
1. `get_comparison`; stop (log, no writes) unless the state is `writing`.
2. Read both results and both frames files. Any missing: `finish_comparison(failed,
   "missing_input")`.
3. Build the request (§5b) and call Bedrock (§5c, same call as M3g).
4. Check the answer (§5d). Bad: `finish_comparison(failed, <code>)`.
5. Put the comparison key, then `finish_comparison(ready)`. No row changed (a job was deleted
   meanwhile): delete the object just written.
Any unexpected exception: log both ids, `finish_comparison(failed, "error")`, return normally.

The Bedrock call, the stop-reason codes (`refused`, `truncated`, `bedrock_error`), the JSON parse
and the stored extras (`model`, `created_at`, `usage`) are one helper shared by both paths, so
the two differ only in inputs, schema, answer check, key and finish function.

### 5b. Request (`neurolens/report_prompt.py`)
Shared builders, used by both requests:
- `READING_RULES`: the single report's reading rules, a module constant: the text of
  `SYSTEM_PROMPT` from "The table has three groups" up to the line before "Writing:". `SYSTEM_PROMPT` is
  rebuilt from intro + `READING_RULES` + its writing rules, with unchanged text.
- `series_table(result)`: one text block, sent after the pictures. First line
  `duration_seconds <n>, last_second <n-1>. One row per second.`, then a header
  row `t` see hear understand overall `muted_overall` (the seven sub-signals and the duplicate
  `sound` column are left out: with them the writer drifted to a big sub-signal and called it
  seeing), then one row per second `t = 0 .. n-1`: `t`
  right-aligned to 3, then each value formatted `+0.00`, space-separated. A missing value is left
  blank (only `muted_overall` can be shorter, as the muted pass is cut to the shared length, and
  it is the last column).
- `words_block(words, label=None)`: `[Video <label> ]spoken words:` then `<start 2 dp> s: <word>`
  lines; omitted when there are no words.
- `frame_blocks(frames, seconds, label=None)`: for `t < seconds`, a text block `[<label> ]t = <t>
  s` then its base64 JPEG image block.

- `comparison_table(a, b)`: one block, one row per second up to the longer video:
  `t` then `A_x B_x` pairs for see, hear, understand, overall, muted_overall; cells past the
  shorter video are blank, so a second sits beside the same second of the other video.

`build_request(result, frames, words)` (single, M3g §5b) = words + frames + table, no label.
`build_comparison_request(a, b)`, each a `(result, frames, words)`, returns
`(COMPARISON_PROMPT, content)`: A's words and frames, then B's, labelled `A` / `B`, then the one
`comparison_table`. The table comes last, right before the answer.

`COMPARISON_PROMPT` (module constant; the laptop trial's accepted text):
```
You compare two videos, A and B, for the person who made them. For each you are given the
predicted brain response for an average viewer, second by second, and one picture from each
second. They may be two cuts of one video or two different videos: work out which from the
pictures and words, and say so.

<READING_RULES>
Comparing:
- Compare levels against 0 and against each other. When the two are two cuts of one video, match
  the shared material by what is on screen, not by the clock: the same shot can sit at different
  times in A and B. Say what one cut adds, removes or reorders and how the brain response differs
  there.
- A longer video is not better or worse for being longer: compare how each holds up, stretch by
  stretch. Never compare whole-video averages alone; say which stretches cause the difference.
- The same reading rules apply to each video on its own: a warm-up at the start and a fall over
  a final logo or product card are expected in both.

Writing:
- The headline and summary are the verdict, shown beside the chart: which video draws the
  stronger response overall, the main reason, and where the other one does better, in two or
  three sentences. Leave the stretch-by-stretch story to the differences. Be accurate rather
  than kind. If they are close, say so.
- A stronger response may be called stronger brain engagement, or said to suggest more
  attention. Never claim anything about what real viewers do: views, watch time, retention,
  drop-off, clicks or sharing, and never give a score.
- Write for a video maker, in plain words. Cite times in whole seconds, always naming the video
  (A 12 s, B 20-24 s). Name what happens on screen from the pictures.
- Differences are the few stretches that explain the verdict most, no two covering the same
  stretch: each one names its time in A, in B or in both (null for the video where it has no
  match), what differs on screen and how the response differs, one sentence each. Leave out
  stretches where the two behave alike, and any difference no scene plausibly explains. Its times
  start at the shot that causes it, at most about 3 seconds before the response differs, and
  end where the difference fades.
- Each list item is one short sentence that adds to the differences, not a repeat of one.
- Every claim about level must hold in the numbers you were given.

Limits: the headline at most {MAX_HEADLINE} characters, 1 to {MAX_DIFFERENCES} differences, at most
{MAX_COMPARISON_LISTS} items in each list. Times are whole seconds from 0 to that video's
last_second, both included, start <= end.
```
The text never quotes a table value (levels are words against typical viewing or the other
video). `MAX_DIFFERENCES = 3` and `MAX_COMPARISON_LISTS = 2` join the existing length constants; like
them they are guidance only. The verdict's length is set in sentences, not characters (the model
cannot count characters and stopped a summary mid-sentence to fit one). They keep the comparison
short: the verdict sits beside the chart, the differences under it.
Size: at most 240 images (two 120 s videos), within the 600-image limit of Sonnet 4.6's 1M
window; the request size against Bedrock's 20 MB limit is measured on the real run (§14).

### 5c. Call
As M3g §5c: same model, `max_tokens`, `anthropic_version`, timeout and retries, with
`COMPARISON_SCHEMA` as the JSON-schema format.

### 5d. Answer
`COMPARISON_SCHEMA` (in `report.py`, strict: every object `additionalProperties: false`, every
field required):
```
SPAN = { "start": int, "end": int } | null
{ "headline": str, "summary": str,
  "differences": [ { "a": SPAN, "b": SPAN, "what_differs": str, "brain_response": str } ],
  "a_does_better": [str], "b_does_better": [str], "suggestions": [str] }
```
After parsing, `_comparison_problem(answer, duration_a, duration_b)` checks only what the page
relies on, like M3g §5d: every field present with its type; each difference's `a` and `b` is
null or a span with `0 <= start <= end < duration` of that video; `a` and `b` are not both null;
the three lists hold only text; the summary ends a sentence as M3g §5d. Never lengths or counts.
Failure codes as M3g §5d.

## 6. Deletion
Comparisons exist only between done jobs. A done job's own files leave in exactly two ways, and
both run through `billing.purge_job_files` on a job with `deleted_at` set:
- the user deletes a video (`DELETE /api/jobs/<id>`), and
- the user deletes the account (`POST /api/account/delete`);
a failed purge of either is retried by the reaper's `unpurged_dataset_jobs` loop.

`purge_job_files`, when the job is deleted: reads the comparison rows where `job_a` or `job_b` is
the job, adds their `comparison_key`s to the same `delete_keys` call as `job_keys` (S3 failure
raises, rows stay for the retry), then deletes those rows in the same transaction that deletes
the job's `retention_snapshots`, before `mark_dataset_purged`. The partner video is untouched
apart from losing the comparison.

Not changed, with the reason:
- Refund and cancel paths (worker refund, the SageMaker error handler, the reaper's `reap` and
  failed-job sweep, `POST /cancel`): they act only on queued or processing jobs, which can have
  no comparison.
- Stop sharing and the reaper's no-curve rule: they purge only the research copy; the user's
  own result and its comparisons stay.
- The 30-day `results/` expiry removes the comparison object on its own schedule; the row stays,
  as the single report's columns do (§15).

Races: a delete marks `deleted_at` before purging, and `claim_comparison` locks both job rows,
so a claim either finishes before the mark (the purge then sees its row) or sees the mark and
claims nothing. A Lambda still writing when a job is deleted fails `finish_comparison` and
deletes its object (§5a).

## 7. Samples
### 7a. `data/samples.json`
```
{ "samples": [ { …as M3d §9…, "report": <stored single report minus usage> (optional) } ],
  "comparisons": [ { "a": <sample id>, "b": <sample id>, "report": <stored comparison minus usage> } ] }
```
In a comparison entry, `a` is the sample made from the report's `job_a` and `b` the one from
`job_b`, so the report's A and B need no relabelling. A pair appears at most once, in either
order; the page matches it either way round. `comparisons` may be missing or empty.

### 7b. `infra/make_sample.py` (operator script, `AWS_PROFILE=neurolens`)
- The existing form also downloads `results/<job_id>.report.json` when present and stores it as
  `report` without `usage`, after checking it with `report._report_problem` against the result's
  duration (fails loudly if it does not pass). No report object: the entry has no `report`.
- New form: `make_sample.py compare <sample_id_1> <job_id_1> <sample_id_2> <job_id_2>`. Both
  samples must already be in `samples.json`. It reads `comparison_key(comparison_pair(job_1,
  job_2))`, checks it with `_comparison_problem` against the two samples' durations, maps
  `job_a`/`job_b` to their sample ids, and adds or replaces the entry for that pair. Missing
  object: stops with a message, writes nothing.

### 7c. `infra/deploy_web.sh`
Unchanged rule: only samples whose clip is listed in `data/videos/SOURCES.md` are published. The
two CeraVe cuts are listed there by Josh's decision (third-party ad, public sample for this
non-commercial course project); the script's comment no longer says third-party ads never are.
Comparisons are published only when both of their samples are; the staged file is
`{"samples": public, "comparisons": public_pairs}`.

## 8. Page (`static/app.html`)
Modest changes; the redesign comes later.
- Single report, sample: the Report tab shows the sample's stored `report` with the same
  rendering as a job's report, no button; a sample without one shows "No report for this
  sample." A job keeps M3g §6.
- Compare for samples: while a sample is open and there are at least two samples, the "Compare
  with…" row lists the other samples, for every visitor, signed in or not. Picking one uses its
  result from `samples.json` (no fetch): the same curve overlay and comparison view as a job
  comparison. Swap stays jobs-only. Jobs are compared only with jobs, samples only with samples.
- The comparison and all its states live only in the Compare tab; the Report tab always shows
  the open video's own report (or its states). The comparison stays selected while visiting
  Analysis or Report, and clearing it (None) returns to Analysis. There is no "Full report" link.
  - Jobs: `GET /api/compare/<main>/<other>/report`, then the same states and wording pattern as
    M3g §6: "Write comparison" with "Free"; "Writing your comparison…" (checks every 5 s until
    `ready` or `failed`; the verdict shows the same shimmering placeholder as a report); "Couldn't
    write the comparison." with "Try again" while tries are left. Either result without
    `frames`: no report and no write offer. The POST uses `postJSON`.
  - Samples: the stored comparison for the pair; without one, no report and no write offer.
  - Layout, one column at every width: the head row (Compare with, time, Play both, Swap, Close),
    then the two videos side by side, then the verdict card (kicker, key, headline, summary,
    placeholder, Write button), then the chart card (title row and chart), then the differences,
    then one `<details>` "More: what each does better, suggestions", collapsed by default,
    holding "Where <name> does better" (twice), "Suggestions", each hidden when empty, and the
    M3g note "Written by Claude from predicted brain responses." When there is no report and none
    can be written, the verdict card is hidden and the chart comes first. On a phone the two
    videos stack; on a desktop each video is capped at about 190 px tall, 16:9 and centred.
  - A is always the left video and orange; B is always the right video and blue. The key line
    "A = <name> · B = <name>" uses the stored names (from the GET's `a`/`b`, or the sample entry).
    When the open video is stored B, its section moves to the right in blue and the other
    (stored A) moves left in orange, using CSS order while each video stays in its own host.
    The name colours, progress bars, difference dots and chart lines follow stored A/B. Ready:
    the headline and summary, then one row per difference, with time chips first (a coloured dot
    and "0:00–0:06", plus "only" when just one side has it), then what differs, then the brain
    response in muted text clamped to two lines. Clicking a row moves both videos to that span's
    start.
  - Swapping the main video keeps the same pair; the videos, rows and chart are redrawn so the
    times, colours and A/B sides still follow the stored pair.
- All model text is inserted with `textContent`, never as HTML. Compare titles show the full name
  and wrap, each in its line's colour with its average overall in words (M3d §8).

## 9. Privacy page (`static/privacy.html`)
The "AI reports" section adds one sentence: when you ask for a comparison, the same things
(frames, words and brain numbers) for both videos are sent the same way, and the comparison is
deleted when either video is deleted, and expires after 30 days.

## 10. Terraform and IAM
No change. The report Lambda's role already reads `results/*` and puts and deletes
`results/*.report.json`, which covers the comparison key; its Data API access covers the new
table. The web role and the reaper already delete `results/*`. Reserved concurrency 5 and the
900 s timeout stay (one comparison is one Bedrock call, like a report).

## 11. Laptop (`infra/local_stack.py`)
`_report_starter` takes the event. For `{"pair": …}` it runs `write_comparison` in a thread with
`FakeReportClient` and a canned valid comparison built from the two results alone (one
difference spanning `0 .. min(2, duration - 1)` in both, one item per list). Migration 009 runs
on the local database like the others.

## 12. Cost (estimate)
Measured on the laptop trial: a 30 s + 76 s pair took about 42k input and 1.7k output tokens,
about $0.17 at Sonnet 4.6's US-profile price ($3.30 / $16.50 per million). Input grows with the
total seconds (about 300 tokens per frame plus about 40 per table row). Two 120 s videos: an
estimated 80k in, 2k out, about $0.30. Tries are used only by failures, so a pair normally costs
one call, at most 3. Free to the user, like the single report.

## 13. Tests (written first by a separate agent; PostgreSQL for billing)
Full treatment: it triggers paid Bedrock calls. Fake SDK client and fake S3; no network.
- Pair: `comparison_pair(x, y) == comparison_pair(y, x)`, lowercase, `job_a < job_b`; the same id
  twice and a non-UUID raise.
- Claim: first claim makes a `writing` row with one try; B-vs-A after A-vs-B claims nothing while
  writing and counts against the same tries; tries cap; stale `writing` reclaimable, fresh one
  not; either job not the user's, not done or deleted, or the user deleted → no claim and no row;
  release gives the try back.
- Routes: each 4xx/202 case in §4, for both orders of the ids; the GET answers the same `a`/`b`
  either way round; start failure → 503 and the try returned; a stale `writing` read as
  `failed`; the starter receives `{"pair": [job_a, job_b]}`; signed out → 401.
- Lambda: success stores the comparison at its key then sets ready; not `writing` → no Bedrock
  call; either input missing → `failed missing_input` and no call; refusal, truncation, bad JSON,
  a span outside its own video's range (A's span checked against A, B's against B), both spans
  null → `failed` with the code and nothing stored; a span valid for the longer video but past the
  shorter one's end, given to the shorter → `bad_answer`; SDK error → `failed bedrock_error`;
  either job deleted during the call → object removed.
- Deletion: deleting either video, through the route and through account delete, removes the
  comparison row and object and leaves the partner's own files; a failed S3 delete keeps the rows
  for the reaper; `remove_deleted_account` succeeds after the purge.
- Request: `series_table` has one row per second whose cells are the result's values at that
  second in header order; the comparison request has A's blocks, then B's, each image after its
  labelled `A t = <t> s` / `B t = <t> s`; image count equals the two frame counts.
- Samples: `make_sample` writes `report` without `usage` and refuses one that fails the check;
  the `compare` form writes `a`/`b` matching the report's jobs and replaces an existing entry for
  the pair in either order (S3 faked). `deploy_web.sh`'s filter drops a comparison whose sample
  is not public.
- Page: every POST goes through `postJSON` (the existing check in `tests/test_m3b_auth.py` covers
  the new one).
- No test pins the prompt wording or exact SDK call arguments.

Break-it checks (3): use `x, y` as given instead of `comparison_pair` in the POST route (a second
report for B-vs-A must go red); drop the comparison cascade from `purge_job_files`; check only
one of the two jobs' ownership in `claim_comparison`. Each must turn a test red.

## 14. Real run (ask Josh before; estimate ≤ $2: GPU start and two short jobs, two reports, one comparison)
Upload the two CeraVe cuts (30 s, 76 s) as normal videos in Josh's account; click Report on each
and Compare on the pair. Check: both reports and the comparison on the page, swapped both ways;
the stored `usage`; the frames files' sizes (to confirm two 120 s videos fit Bedrock's 20 MB
request). Then run `make_sample.py` for both cuts and `make_sample.py compare`, commit
`data/samples.json`, and deploy the web. Then delete one of the two jobs and confirm the
comparison row and object are gone. Bedrock line in Cost Explorer the next day.

## 15. Known limitations (accepted)
- Comparisons are free and one user can compare every pair of their videos (n videos, n(n-1)/2
  pairs, about $0.17-0.30 each). Reaching a real cost needs a hostile user with many paid videos;
  reserved concurrency 5 caps the rate, not the total.
- After either result's 30-day expiry the comparison cannot be opened (the page needs both
  results); its row stays and its object expires on its own clock.
- The model's prose says "A" and "B"; the page's key line names them.
- The answer can be confidently wrong about why one cut works better; the page says it is an AI
  reading.

## 16. Acceptance criteria
- Compare on two of the user's videos writes one comparison within ~2 min; opening the pair the
  other way round shows the same report and a POST answers `report_ready`.
- Deleting either video removes the comparison row and object.
- A sample with a stored report shows it (a sample without one shows "No report for this
  sample."); two samples with a stored comparison show it when compared, signed out.
- `pytest` and `ruff check .` pass; the three break-it checks go red.

## 17. Older specs updated with this build
- M3g §4: `report_starter` takes the Lambda event (§4 above).
- M3g §5a: the handler also takes `{"pair": …}` (§5a above).
- M3g §9: the laptop starter takes the event (§11 above).
- M3d §9: a sample entry may carry `report`, and `samples.json` may carry `comparisons` (§7).
- The test that pins the starter call, `tests/test_m3g_report.py::test_post_report_starts_a_report`
  (`starter.calls == [job_id]`), changes deliberately to `[{"job_id": job_id}]`.

## 18. PDF download (report and comparison)
Decided 31 Oct (Josh): a server-made PDF of a written report and of a written comparison; light
paper, the page's fonts and orange, A first and orange, B blue; no 3D brain. Generated on click
in the web function, never stored for jobs. Design approved from real CeraVe previews, then
reviewed by an independent Opus pass; its fixes are part of this section.

### 18a. Module `neurolens/report_pdf.py` (no AWS, no database)
- `report_pdf(name, result, report, frames) -> bytes` and
  `comparison_pdf(a, b, report) -> bytes`, where `a` and `b` are `(name, result, frames)`.
  `frames` is the list `storage.get_frames` returns (JPEG bytes, index = second), or `[]`.
- `fpdf2` is imported inside the functions (cold start unchanged). Fonts live in
  `neurolens/fonts/` with their OFL licence texts: Space Grotesk Regular/Medium/Bold and
  JetBrains Mono Regular/Medium (as on the page), plus Noto Sans SC and Noto Sans KR as fallbacks
  for Chinese, Japanese and Korean, trimmed by `infra/make_cjk_fonts.py` to the regular weight
  and the national standard sets (GB 2312, Big5, JIS, KS X 1001; ~8 MB instead of 28 MB). The
  fallbacks are registered only when the text holds a character the main fonts lack (they are
  large and slow to load). A character no font has (emoji, rare characters) is drawn as "?",
  never dropped silently. A test renders one name mixing simplified and traditional Chinese,
  Japanese and Korean and fails if any character comes back as "?".
- A4 portrait, 18 mm side margins, text left-aligned (never justified). Header on every page:
  "NeuroLens" left, the kind ("Brain response report" / "Comparison report") right. Footer:
  "Written by Claude from predicted brain responses (TRIBE v2), not viewer data. 0 = typical
  viewing." and "n / N". The date (the report's `created_at`, e.g. "31 Oct 2026") appears once,
  in the meta line under the name.
- Single report, in order: name; meta line (length m:ss · date); headline; summary; the overall
  chart; a one-line key ("With sound", and "Sound off: the same video with the sound removed"
  only when `result["muted"]["overall"]` exists); the Seeing / Hearing / Understanding strips;
  "What works" and "What to improve" side by side; "Moments".
- Comparison, in order: "A <name>" in orange and "B <name>" in blue; meta line (A m:ss · B m:ss
  · date); headline; summary; one chart with both overall lines, no fill, no difference marks;
  key; "A does better" (orange marks) and "B does better" (blue marks) side by side; "Where they
  differ"; "Suggestions".
- Chart: level words on the left instead of numbers ("Well above" at +0.2, "Typical" at 0,
  dashed, "Well below" at -0.2), drawn only when inside the range; range at least ±0.3, widened
  to fit the data. Time labels m:ss, chosen by width so there are at least 3 (one sample: a dot,
  not a line). Single report only: each moment is a shaded band from `start` to `end + 1`, cut
  at the last second, with a small gap between bands that touch, numbered 1..n above the chart
  (numbers nudged apart when they would overlap); the numbers match the Moments rows.
- Strips: one shared scale for all three groups (the main chart's), a faint ±0.2 line, orange
  above typical and grey below, and a one-line key saying so.
- Moments row: the picture for second `(start + end) // 2` (clamped to the frames list), fitted
  inside a box of the frame's own shape so a tall phone frame keeps its shape (black bars in the
  frame itself are left as they are); then the number, the time `m:ss–m:ss` (en dash, `start` to
  `end`, as the page's `fmtRange`), what happens, the brain response, and "Try" + suggestion when
  there is one. No frame for that second (or no frames at all): the row has no picture and its
  text takes the full width. Differences rows: A's picture and time, then B's ("Not in A" / "Not
  in B" box when a side is null), then the text.
- Page breaks: a row (moment or difference) never splits across pages; a heading never ends a
  page alone; the two side-by-side lists move to the next page as one block when they do not fit;
  rows are tight enough that six moments fit on one page after the first.
- Names: a name wider than its space is shortened in the middle with "…", keeping the extension.
- Size: frames are 640 px JPEGs (~50 KB); at most 8 moments or 3 differences (6 pictures), so a
  PDF stays well under 1 MB, far below the web function's 6 MB response cap (about 4.5 MB of
  binary after base64).

### 18b. Routes (`neurolens/web/app.py`)
- `GET /api/jobs/<id>/report.pdf`: `owned_result` (its errors unchanged: 404 not yours, 404 not
  ready, expired, refunded); then the report must be `ready` and stored, else 409 `no_report`.
  Frames from `storage.get_frames`; a missing frames object gives a PDF without pictures, any
  other S3 error is raised (500). Name = the upload's file name.
- `GET /api/compare/<x>/<y>/report.pdf`: `comparison_ids` (400 `same_video`, 404 not yours), both
  results through `owned_result`, the comparison row `ready` and its report stored, else 409
  `no_report`. A and B as stored (canonical pair, §2).
- Both answer `application/pdf` with an attachment name built like the CSV's
  (`_csv_disposition` generalised to take the suffix): "<stem> – NeuroLens report.pdf" and
  "<stem A> vs <stem B> – NeuroLens comparison.pdf" (ASCII fallback in `filename`, full name in
  `filename*`). Signed out: 401, like every `/api/*` route. `binary_support=True` already makes
  the Lambda return the bytes base64-encoded.

### 18c. Page (`static/app.html`)
- The Download menu (step 3d) offers "Data (CSV)", gains "Report (PDF)" when the open job's
  report is ready, and "Comparison (PDF)" in the Compare tab when the pair's comparison is ready.
  Samples: "Report (PDF)" from the sample's `pdf_file`, "Comparison (PDF)" from the comparison
  entry's `pdf_file`, with the download name
  built like the sample CSV's. No item when there is no written report.

### 18d. Samples (`infra/make_sample.py`, `infra/deploy_web.sh`)
- Both forms write the PDF with the same module: `data/output/<id>/report.pdf` (name = the
  sample title) when the sample has a report, and `data/output/compare/<a>__<b>.pdf` for a
  comparison; they record `pdf_file` in the entry. Frames come from `results/<job_id>.frames`
  (the job id is in the sample's result). A third form, `make_sample.py pdfs`, re-renders every
  sample's and comparison's PDF from `samples.json` (run it after a layout change).
- `deploy_web.sh` copies each public sample's and public comparison's `pdf_file` with the rest.
  `data/output/` stays git-ignored, as for the CSV.

### 18e. Shipping the web code through S3
- With fpdf2 (and its Pillow and fontTools) and the fonts, the web zip is about 48 MB, at the
  50 MB limit for a direct upload. So the zip goes through S3: Terraform uploads it as
  `aws_s3_object` `deploy/web_lambda.zip` in the main bucket and the function reads `s3_bucket` /
  `s3_key` (code changes stay `deploy_web.sh`'s job: `ignore_changes` on the code arguments);
  `deploy_web.sh` copies the new zip to the same key, then `update-function-code --s3-bucket
  --s3-key`. The limit becomes 250 MB unzipped. `deploy/` is private (CloudFront serves only
  `site/`), has no expiry rule, and the deploy user already has S3 rights on the bucket: no IAM
  change.

### 18f. Tests (light treatment + a security check of the two routes)
- `pypdf` in `requirements/dev.txt` to read the text back. A PDF starts with `%PDF`, and its text
  holds the headline and the name, for a single report and a comparison, on the fake app.
- Hard cases render without error: no frames; a 1- and 2-second video; no muted series; a
  comparison side that is null; a Chinese/Japanese/Korean name (the characters come back in the
  text); a name of 200 characters; eight moments with long text (no row split: checked by the
  module, not pixel tests).
- Routes: signed out 401; another user's job or pair 404; no report or not ready 409
  `no_report`; the attachment name.

### 18g. Known limitations
- Thai, Tamil, Arabic and other scripts outside the fonts print as "?" in the PDF (the page is
  unaffected).
- The PDF is a second layout to keep in step with the page; a layout change means re-running
  `make_sample.py pdfs` for the samples.
