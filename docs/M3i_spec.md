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
- `POST /api/jobs/<id>/retention` body adds `video_url` (text, required) and `same_cut` (`same`,
  `light_edit`, `recut`, required). Bad values → 400 (`invalid_video_link` with the message of §3,
  or the generic bad-field 400).
- Order: parse the link (§3) → parse the report with that platform's parser (YouTube: unchanged;
  Facebook: §4, given the link's id) → validate the form choices → the existing S3 writes → quality
  flags → `billing.add_curve`. A link or report refusal stores nothing.
- `billing.add_curve` gains `video_ref`, `same_cut`, `source` and `reward_min_days`, and in its
  transaction, after the existing job checks and before the duplicate-curve check:
  - another snapshot with the same `video_ref` on a job of a different user → `VideoTaken`
    (409 `video_link_taken`, "This video is linked to another account.");
  - the job already has a snapshot with a different non-null `video_ref` → `VideoLinkChanged`
    (409 `video_link_changed`, "This analysis is already linked to another video.").
  - The reward gets two more conditions: `now() >= jobs.created_at + reward_min_days days`, and no
    snapshot with the same `video_ref` has `rewarded_cents > 0`.
  - It returns also `reward_from` (UTC text, `created_at + reward_min_days`) when the only unmet
    reward condition was the date.
- `list_jobs` adds `curve_reward_from` (UTC text) for shared jobs whose `curve_reward_cents > 0`.
  `curve_reward_cents` keeps meaning "what a valid curve would give", ignoring the date and the
  link (unknown until the form). The date and the per-user conditions come from the same helper
  `add_curve` uses, so the preview and the payment cannot drift apart.

## 7. Page (`static/app.html`)
Dates are the reward date in the viewer's local time, "9 Nov" (with the year only if not this year).
- **Upload dialog:** the per-row "Add audience retention" link, the curve held by the page and its
  send-when-done path are removed (M3f §2a, §5d). The quiet line under the consent box: "Add its
  YouTube or Facebook retention curve a week later and get its credit back."
- **Entry points** (History row link, "⋯" menu, results card): when a reward is possible and the
  date is still ahead: row link "Get X back from 9 Nov", menu "Add audience retention (get X back
  from 9 Nov)", card "Thanks for sharing. Add its YouTube or Facebook retention curve from 9 Nov to
  get your X of credit back.". After the date, as now ("Get X back", …), with "YouTube or Facebook"
  in the card.
- **Form:** after the video name, a "Video link" text field (placeholder "YouTube or Facebook
  link"), then the file, then the choice rows: views, published, promoted (as now) and "Published
  video" ("Same as analysed", "Lightly edited", "Re-cut"). The reward line: before the date "Add a
  valid curve from 9 Nov and this analysis is free (X back). Curves added earlier are saved without
  credit."; after it, as now. The guide link goes to the guide's section for the link's platform
  once a valid link is typed, else to the top.
- **Look:** the form stays light: the link field first; the four choice rows are compact segmented
  one-tap rows (as the existing ones), no extra headings or help text beyond the reward line.
- **Reward moment** (only when `rewarded_cents > 0`): a small confetti burst from the reply, about
  30 pieces in the page's own colours (heat orange `--heat` and the brain map's heat shades), over in
  about 1 s, drawn on a temporary canvas that is removed after; the reply shows a tick that draws in;
  the top bar's credit counts up from the old to the new amount over about 0.8 s with a brief
  `--heat` glow. With the system's "reduce motion" setting (`prefers-reduced-motion`), no confetti
  and no count-up: the new amount and the tick just appear. No library: a few lines of canvas code.
- **Reply:** saved, valid, and only the date unmet: "Thanks, saved. Add a newer curve from 9 Nov
  to get X back." The other replies are unchanged; link and report refusals show the server's
  message.

## 8. Other pages
- **Retention guide (`static/retention_guide.html`):** two sections, "YouTube" (the current steps)
  and "Facebook" (Graph API Explorer: pick the Page under "User or Page", allow
  `pages_read_engagement` and `read_insights`, paste `<video id>/video_insights?metric=total_video_retention_graph`,
  Submit, save the reply as a `.json` file; the video id is the number in the video's address). Each
  section ends with "Add it from 7 days after your analysis to get its credit back."
- **Privacy (`static/privacy.html#research`):** what is kept adds "the video's link and whether it
  was re-cut"; the reward rules add "added at least 7 days after the analysis" and "once per
  published video"; curves come "from YouTube or Facebook". The test that keeps the privacy page's
  numbers equal to `config.json` covers the 7.
- **Landing (`static/index.html`), Early access:** Invited: "5 minutes of free analysis for invited
  creators, marketers, teachers and researchers."; Free: "Share a video for research, add its
  YouTube or Facebook retention curve a week later, and get its credit back." A test keeps "5
  minutes" equal to `billing.invite_cents` at the current price.
- **Consent:** the wording shown changes, so `billing.CONSENT_VERSION` becomes `"2026-11-02"` with the
  page's text (M3f §5a rule).

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
- The 7 days run from the analysis, not from publishing; a video published late gets a younger
  curve. The "published" answer records this.
