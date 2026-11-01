# NeuroLens — M3i Implementation Spec: curve collection (week-old curves, video links, Facebook, invite credit)

Status: current design. Builds on M3f §5 (research sharing and curves). Where this spec and an older
one disagree, this one wins, and the older section is updated to match (§10).

## Decisions (2 Nov, Josh)
- A curve earns its video's credit back only when added at least 7 days after the analysis
  (NeuroLens's own clock). Earlier curves are accepted, stored and shown, with no reward; a later,
  different curve for the same job can still earn it. A next-day curve shows few viewers, mostly
  subscribers; a week-old one is worth more for research and training.
- Rewards never depend on what the user says (views, published, promoted, re-cut): those answers
  are stored as training weights only.
- Every curve names the published video by its link. One published video belongs to one account,
  and earns at most one reward.
- The form asks whether the published video is the one analysed: Same / Lightly edited / Re-cut.
- Facebook Page videos are a second curve source, beside YouTube. Built from Meta's documentation
  only; no real report has been seen yet, so the check is strict and a mismatch is refused loudly.
  Meta ad curves and Facebook/Instagram Reels are left out until a real report is seen.
- Invited users get a standard 5 minutes of credit (enough for a brand team to compare two cuts of
  two or three 30–60 s ads).
- A rewarded curve gets a small celebration (a short confetti burst, the credit counting up); the
  page stays clean, modern and light: the form's new questions are compact one-tap rows.
- The upload dialog's "Add audience retention" link is removed: with the 7-day rule a curve added
  at upload never earns the reward, and the History row offers the same form later.

## 1. Scope
### In scope
§2–§9 below, their tests (§11), the docs (§10).
### Out of scope
Connecting any platform account (unchanged, M3f §1), Meta ad curves, Reels on any platform, TikTok,
Instagram, Vimeo, reminders or email when the 7 days are up.

## 2. Settings (`config.json`)
- `research.curve_reward_min_days`: 7. A starting value, adjusted from data later.
- `billing.invite_cents`: 1000 (5 min at today's price, `pricing.CENTS_PER_BLOCK` 100 per 30 s).
  `billing.starter_cents` stays 0: credit comes only by invitation.

## 3. Video link (`neurolens/results.py`, pure)
`parse_video_link(text) -> (platform, video_id)`, `platform` `"youtube"` or `"facebook"`; raises
`ValueError` with the user-facing message. Trims whitespace, accepts with or without `https://`,
`www.` and `m.`; at most 300 characters.
- YouTube (`youtube.com`, `youtu.be`): `watch?v=<id>`, `youtu.be/<id>`, `/shorts/<id>`,
  `/live/<id>`, `/embed/<id>`; `<id>` is exactly 11 characters of `[A-Za-z0-9_-]`; other query
  parameters ignored.
- Facebook (`facebook.com`): `/<page>/videos/<digits>`, `/<page>/videos/<slug>/<digits>`,
  `/watch/?v=<digits>`, `/watch?v=<digits>`, `/video.php?v=<digits>`.
- `facebook.com/reel/...` or `facebook.com/share/r/...` → "Facebook Reels aren't supported yet."
- `fb.watch/...` or `facebook.com/share/v/...` (short links that need a lookup) → "Open the link and
  paste the full facebook.com address from the browser bar."
- Anything else → "Paste the video's YouTube or Facebook link."
- The stored form is `video_ref = "<platform>:<video_id>"`.

## 4. Facebook report (`results.parse_facebook_retention(text, video_id)`, pure)
The creator runs, in Meta's Graph API Explorer with a Page token for their Page,
`GET /<video_id>/video_insights?metric=total_video_retention_graph` and uploads the JSON reply.
Meta documents the metric as "the number of times your videos played at each interval as a
percentage of all views", lifetime, videos "divided into 40 equal intervals". Accepted only when
all of these hold, else `ValueError` naming the first fault:
- a JSON object whose `data` is a list holding exactly one item with `name`
  `"total_video_retention_graph"` and `period` `"lifetime"`;
- that item's `id` starts with `"<video_id>/"` (else "This report is for a different video than
  the link.");
- `values` is a non-empty list; the last item's `value` is an object whose keys are exactly
  `"0"`…`"39"`, each a finite number in 0–1. Anything else → "This Facebook report isn't in the
  expected shape. Please send it to the NeuroLens team." (the file is not stored).
- Returns 40 pairs `(i / 40, value_i)` in key order, the same `(elapsed_ratio, watch_ratio)` shape
  as `parse_retention_report`, so `curve_quality`, the CSV and the chart overlay work unchanged.
`parse_retention_report` (YouTube) is unchanged. The YouTube report holds no video id, so a YouTube
link cannot be checked against its report.

## 5. Migration 010 (`infra/migrations/010_curve_links.sql`)
- `retention_snapshots`: `video_ref TEXT` (null only on rows saved before this build), `same_cut
  TEXT CHECK (same_cut IN ('same','light_edit','recut'))`; index on `video_ref`.
- `source` takes `'youtube_api'` (as now) or `'facebook_api'`.

## 6. Adding a curve (replaces the matching parts of M3f §5d)
M3j §4-§6 supersedes the reward date and the form's order: the date comes from the checked publish
date when we can check it, and the link is checked before any work. The link rules below remain.
- `POST /api/jobs/<id>/retention` body adds `video_url` (text, required) and `same_cut` (`same`,
  `light_edit`, `recut`, required). Bad values → 400 (`invalid_video_link` with the message of §3,
  or the generic bad-field 400).
- Order: parse the link (§3) → look the video up (M3j §4; the save does its own lookup and never
  trusts the page) → parse the report with that platform's parser (YouTube: unchanged; Facebook:
  §4, given the link's id) → validate the choices (`views` and `published` are required only when
  the facts are unknown; with facts they must be absent or null) → the existing S3 writes →
  quality flags → `billing.add_curve`, passing the facts. A link or report refusal stores nothing.
- `POST /api/jobs/<id>/retention/check` takes `{video_url}` and returns the same job checks, the
  same link facts, and `{platform, video_id, reward_cents, reward_from, reason, published_on}`
  from `billing.curve_offer`, writing nothing (M3j §6). Its `VideoTaken` / `VideoLinkChanged`
  refusals are the save's 409s.
- `billing.add_curve` gains `video_ref`, `same_cut`, `source`, `reward_min_days`, `facts` and
  `length_tolerance_s`, and in its transaction, after the existing job checks and before the
  duplicate-curve check:
  - another snapshot with the same `video_ref` on a job of a different user → `VideoTaken`
    (409 `video_link_taken`, "This video is linked to another account.");
  - the job already has a snapshot with a different non-null `video_ref` → `VideoLinkChanged`
    (409 `video_link_changed`, "This analysis is already linked to another video.").
  - The reward rules are M3j §5: the date is `published_at + reward_min_days` when facts are
    known, else `created_at + reward_min_days`; the length must match the analysed video within
    `length_tolerance_s` when it can be checked; and no snapshot with the same `video_ref` has
    `rewarded_cents > 0`. It returns `reason` (the first unmet condition) and `reward_from` only
    with `too_early`.
- `billing.curve_offer` shares the job checks, the link refusals and `_curve_reward` with
  `add_curve`, so the check and the save cannot disagree.
- `list_jobs` drops `curve_reward_from`: before a link there is no date to promise.
  `curve_reward_cents` keeps meaning "what a valid curve would give", ignoring the date, the link
  and the length.

## 7. Page (`static/app.html`)
No date is promised before the form names a link (M3j §5).
- **Upload dialog:** the per-row "Add audience retention" link, the curve held by the page and its
  send-when-done path are removed (M3f §2a, §5d). The quiet line under the consent box is M3j §8's.
- **Entry points** (History row link, "⋯" menu, results card): row link "Get X back", menu
  "Add audience retention (get X back)", card "Thanks for sharing. Once it's been public a week,
  add its retention curve: see real viewers beside the prediction and get X back."
- **Form, step 1:** the video name and a "Video link" field only. On a valid-looking link
  (debounced about 400 ms, and on paste) the page calls `/retention/check` and shows one line:
  pays now, `too_early` (with the date, or "a week after your analysis" when facts are unknown),
  `length_mismatch`, `cap_reached`, or the other-reason line (M3j §8). A refusal shows the
  server's message and keeps step 2 hidden.
- **Form, step 2** (after a non-refused check): the guide link for that link, one report box
  "Paste the reply here" with a small "or choose a file" link, the promoted row and "Published
  video" ("Same as analysed", "Lightly edited", "Re-cut"), and views and published only when the
  facts are unknown. Save, then the saved or error message.
- **Reward moment** (only when `rewarded_cents > 0`): a small confetti burst from the reply, about
  30 pieces in the page's own colours (heat orange `--heat` and the brain map's heat shades), over in
  about 1 s, drawn on a temporary canvas that is removed after; the reply shows a tick that draws in;
  the top bar's credit counts up from the old to the new amount over about 0.8 s with a brief
  `--heat` glow. With the system's "reduce motion" setting (`prefers-reduced-motion`), no confetti
  and no count-up: the new amount and the tick just appear. No library: a few lines of canvas code.
- **Reply:** rewarded as now; otherwise "Thanks, saved." first, then the line for its `reason`
  (M3j §8). Quality failures keep their existing replies; link and report refusals show the
  server's message.

## 8. Other pages
- **Retention guide (`static/retention_guide.html`):** reads `?platform=&v=&start=` from its
  address. With YouTube values it fills the six values (startDate = the day before `start`, endDate
  = today in the viewer's time zone, filters `video==<v>`), each with a Copy button; with Facebook
  it fills the query with the id and a Copy button. Without values, as now. The steps say to paste
  the reply into NeuroLens (no saving a file), and both sections end "Add it once the video has
  been public a week to get its credit back."
- **Privacy (`static/privacy.html#sharing`, with the old `#research` anchor still landing there):**
  the section is "Sharing a video". Its purpose is to help improve NeuroLens by building and
  testing its own engagement models, which may later be a paid product (notice first); the course
  study and its copy are removed. What is kept adds the checked publish date, length and view
  count. Reward rules: "public at least 7 days (7 days after the analysis when we can't check the
  publish date)" and "the same length as the analysed video, give or take 2 seconds, when we can
  check it". The test that keeps these numbers equal to `config.json` covers 7 and 2.
- **Landing (`static/index.html`), Early access:** Invited: "5 minutes of free analysis for invited
  creators, marketers, teachers and researchers."; Shared: "Share a video, add its retention curve
  once it's been public a week, and get its credit back." A test keeps "5 minutes" equal to
  `billing.invite_cents` at the current price.
- **Consent:** the wording shown changes, so `billing.CONSENT_VERSION` becomes `"2026-11-03"` with
  the page's text (M3f §5a rule).

## 9. Invite credit (`infra/grant_credit.py`)
The amount becomes optional: with none, it grants `billing.invite_cents` from `config.json` and the
confirm line reads "Grant $10.00 (5 min, the invite credit) to <email>?". With an amount, as now.
The grant is a `grant` ledger row as now, so invited users count as funded for curve rewards
(M3f §5d).

## 10. Older specs updated with this build
- M3f §1 out of scope: Meta Ads → "Meta ad curves" (Facebook Page videos are in, M3i).
- M3f §2a: the last-but-one bullet (upload-dialog curve link) removed.
- M3f §5a: the quiet line, the privacy summary (link, re-cut answer, 7 days, once per published
  video, YouTube or Facebook), consent version.
- M3f §5d: entry-point wording with the date, the form fields, the body, the order, `add_curve`'s
  new refusals and reward conditions, `list_jobs` `curve_reward_from`, the reply; the upload-dialog
  send bullet removed. Point to M3i §6 instead of repeating it.
- M3f §5e: note migration 010 adds `video_ref` and `same_cut`.
- M3d §8 (history row) and §8b (Early access copy).
- M3b §7a: grant without an amount gives the invite credit.

## 11. Tests (written first by a separate agent; PostgreSQL for billing)
Full treatment (money). Behaviour, not call details.
- `parse_video_link`: each accepted YouTube and Facebook form gives the right `(platform, id)`;
  Reels, short links, other sites, a 10- or 12-character YouTube id, over 300 characters → the
  right message.
- `parse_facebook_retention`: a valid 40-key reply → 40 pairs from 0 to 0.975; wrong id, 39 or 41
  keys, a missing key, a value above 1 or not a number, two metrics, no `data` → `ValueError`.
- `add_curve` (PostgreSQL):
  - a valid curve on day 6 is stored with no reward and returns `reward_from`; a valid, different
    curve on day 7 for the same job is rewarded once (move the job's `created_at` back, never sleep);
  - the same link on another account's job → `VideoTaken`, nothing stored; the same link on two of
    the same user's jobs → both stored, only the first eligible one rewarded;
  - a second link on a job that already has one → `VideoLinkChanged`;
  - the existing M3f reward tests still pass with curves added after day 7.
- `list_jobs`: `curve_reward_from` is `created_at + 7 days`; absent when no reward is possible.
- Route: missing or bad `video_url`/`same_cut` → 400 and nothing in S3; a Facebook report whose id
  differs from the link → 400; a Facebook curve saves with `source = 'facebook_api'`.
- `grant_credit.py` with no amount grants 1000 cents; with an amount, as now.
- Copy: landing "5 minutes" matches config; the privacy page's 7 matches config; the consent version
  on the page equals `billing.CONSENT_VERSION`.
- **Break-it checks** (on committed code, restore with `git checkout <file>`): remove the date
  condition; remove the other-account link check; remove the once-per-`video_ref` reward check.
  Each must turn a test red.

## 12. Known limitations (accepted)
- The Facebook route is built from documentation only. Meta's Explorer may need the creator to set
  up a developer app first, and the real reply may differ from §4; a mismatch is refused, never
  guessed. First real Facebook report: check it, then adjust §4.
- A YouTube report holds no video id, so a YouTube curve's link is the user's word; the link rules
  limit reuse but cannot prove the report belongs to it.
- Two accounts adding the same link at the same instant could both pass the other-account check
  (no lock across jobs). Needs two colluding accounts; the reward caps bound the cost.
- Facebook has no publish date or length, and a YouTube lookup that fails falls back to the
  analysis date; the 7 days then run from the analysis (M3j §12). A checked YouTube video waits
  from its publish date and must match the analysed length within 2 s.
