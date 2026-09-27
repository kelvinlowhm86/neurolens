# NeuroLens — M4 Implementation Spec
**Milestone:** Usability Evaluation & Analysis (31 Oct – 13 Nov)
**Builds on:** M1–M3 (a complete, authenticated, credit-accounting-enabled, autoscaled evaluation product; it does not collect real payments). M4 is different in character from M1–M3: most of the work is running a study and consolidating results, not building system components. The spec below separates the small amount of real engineering (lightweight event logging, a consolidation/analysis script) from the study design itself (which an AI coding agent should treat as fixed input, not something to redesign).

## 1. Scope
### In scope (things to build)
- A lightweight, server-enforced in-app consent step and pseudonymous participant-code linkage for study participants (not anonymous — see §8a)
- Minimal event logging for task-performance measurement, reusing M3's existing Aurora tables wherever possible rather than building new infrastructure
- A results-consolidation script that pulls together Experiment 1 (latency), Experiment 2 (scaling/Locust), Experiment 3 (TCO), SUS scores, and task-performance data into the tables/figures needed for the Final Report
### In scope (things to run, not build)
- The actual 5-participant SUS study session, following the protocol in §3 — this is a human-facilitated activity, not something the coding agent executes
### Out of scope
- Any new product features. If the study surfaces usability problems, fixing them is explicitly scoped as post-submission future work per the report — a follow-up task, not part of this spec
- Redesigning the study methodology (cohort size, instrument choice, task script) — those are fixed by the report's Section 5.2 and shouldn't be second-guessed by whoever implements this spec

## 2. Lightweight event logging
Reuse what M3 already gives you rather than building a new analytics system. The moderator assigns each of the five invited accounts a random, pseudonymous participant code before the study; it is recorded in events and entered in the external SUS form, avoiding an email-based join. Add the study fields and one small event table:
```sql
ALTER TABLE users
  ADD COLUMN study_participant BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN study_participant_code TEXT UNIQUE,
  ADD COLUMN study_consent_at TIMESTAMPTZ;

CREATE TABLE study_events (
  id            BIGSERIAL PRIMARY KEY,
  user_id       TEXT NOT NULL REFERENCES users(user_id),
  event_type    TEXT NOT NULL,   -- consent_given|task_started|chart_interaction|task_completed
  job_id        UUID REFERENCES jobs(job_id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
- `study_participant` and `study_consent_at` are checked server-side before permitting the study flow. A query parameter may select study-only presentation, but must never grant study access or bypass consent.
- `consent_given`: logged when the participant accepts the consent screen (§4)
- `task_started`: logged when the moderator (or participant, if self-guided) marks the start of the end-to-end scenario
- `chart_interaction`: logged on the existing chart `onClick` handler already in the frontend (it currently seeks the video — just add one line to also POST this event; no new UI needed)
- `task_completed`: logged when the participant indicates they've identified a peak/drop-off moment — this can be as simple as a "Done — I found it" button added near the charts for the study build only. Study-mode presentation may use a query parameter, but server-side participant/consent state remains authoritative.
Don't build anything more elaborate than this — a 5-participant qualitative study doesn't need a full analytics pipeline, and over-instrumenting risks changing the UI enough to affect the very usability being measured.

## 3. SUS instrument
Use the standard, unmodified 10-item System Usability Scale (Brooke, 1996) — don't build a custom in-app version of this. Two reasonable options, pick one:
- **External form (recommended):** a Google Form or equivalent with the 10 standard SUS items, the structured qualitative questions from §3.2, and the assigned pseudonymous participant code. Do not collect email solely to join study data. This keeps the validated instrument's exact wording intact and avoids the product's own visual styling influencing responses.
- **In-app form:** only if you want a single unified experience — a simple standalone page (not styled to match the product, deliberately, for the same neutrality reason above), gated behind auth, submitting to a new `sus_responses` table. More engineering for no real benefit at N=5; only worth it if external tools aren't an option for you.

### 3.1 Scoring
Standard SUS scoring: for odd-numbered items, score = response − 1; for even-numbered items, score = 5 − response; sum all 10, multiply by 2.5, giving 0–100. The report's target threshold is ≥80 ("Grade A"). Whatever collection method you pick, the consolidation script (§5) needs raw per-item responses, not just a pre-computed total, so it can recompute and sanity-check scores.

### 3.2 Structured qualitative questions (append to whichever form you use)
Per the report's Section 5.2, capture open-ended responses on:
1. Dashboard clarity and playhead responsiveness
2. Comprehension of the cortical ROI labels (FFA/EBA/PPA/STS/auditory) — this one matters particularly, since these labels are neuroscience jargon and the report's own competitor-differentiation argument rests on users actually understanding them
3. Perceived commercial value of the $0.90/ad pricing model

## 4. Consent flow
A short in-app screen shown once, before a study participant's first upload:
- Plain-language description of what's being tested; that they'll upload a self-selected ad creative; that their Google-authenticated account is linked, server-side, to logged session data for the purpose of measuring task performance (even though the separate SUS form collects no email or other direct identifier); that this linked data is accessible only to the project team, not published or shared externally; that the pseudonymized (participant-code-linked) task-performance metrics and SUS scores may be retained indefinitely as part of the project report and codebase, becoming fully de-identified once the email-to-code link is deleted by 14 November 2026; that the identifiable link between their account (email) and their participant code, and any raw database copy or snapshot containing that link, will be deleted by that same date; and that the session may be observed/logged.
- A single "I consent" action, which logs `study_events(event_type='consent_given')`, sets `users.study_consent_at`, and unlocks the study flow
- This appears only for server-flagged study participants. A query parameter may control study-only presentation but cannot opt a user into the study or bypass consent.

## 5. Task protocol (fixed input — implement the logging hooks, don't redesign the task)
Per Section 5.2's "End-to-End Creative Audit Scenario," each participant:
1. Signs in (Google SSO, already built in M3)
2. Uploads a self-selected ad creative (or picks a sample, if they don't have one on hand — the existing sample carousel already supports this)
3. Observes real processing progress (M2's real polling UI)
4. Explores the synced playhead timeline to identify attention peaks and drop-off moments
5. Marks task completion (§2's `task_completed` event)
Task success/failure is judged by the moderator observing whether the participant can articulate a peak and a drop-off moment they found, not purely by an automated signal — this is a qualitative judgment call appropriate for N=5, not something to over-automate.

## 6. Consolidation & analysis script (`experiments/consolidate_results.py`)
The one substantial piece of new code this milestone needs. It should:
- Read Experiment 1–3 artifacts exclusively from `experiments/<experiment>/<run_id>/` and their `manifest.json` files (the shared contract defined in M2 §10); reject incomplete or schema-mismatched runs rather than guessing paths or discovering logs manually
- Pull `jobs`/`study_events` timestamps to compute time-on-task per participant for the core scenario; use `task_started`/`task_completed` as the primary task interval, with `job_id` optional for sample-based sessions
- Produce scenario-based cost estimates from measured Experiment 3 runtimes and stated assumptions, then compare them to the ~1,475 videos/month analytical breakeven using the report's fully-loaded $0.10/video model. Do not present these small benchmark runs as directly observed fully-loaded cost per video.
- Take the SUS raw responses (CSV export from whichever form you used) and compute per-participant and mean SUS scores
- Output: a small set of charts/tables (latency breakdown bar chart, throughput-vs-concurrency line chart, SUS score summary, TCO comparison table) in a format easy to drop into the Final Report — plain PNGs/CSVs are fine, no need for a polished dashboard here since this output is for the report, not the product

## 7. File layout additions
```
neurolens/
├── infra/
│   ├── study_events_schema.sql     # NEW — study fields plus the event table from §2
│   └── teardown_m4.sh              # NEW — final teardown after consolidation/exports
├── static/                          # MODIFIED — consent screen, task-completion button, chart_interaction logging (study-mode only)
├── experiments/
│   └── consolidate_results.py       # NEW — pulls Experiments 1–3 + SUS + task data into report-ready output
└── docs/
    └── sus_form_questions.md         # NEW — exact SUS/qualitative text and pseudonymous participant-code instructions
```

## 8. Acceptance criteria
1. Only a server-flagged study participant can enter the study flow; declining or not consenting leaves it inaccessible. A query parameter alone cannot grant access or bypass consent.
2. Every study session produces a computable time-on-task figure from `task_started`/`task_completed` events, including a sample-based session with no new job.
3. `consolidate_results.py` joins event data to an external SUS CSV using the pseudonymous participant code, reads only valid versioned Experiment 1–3 artifacts, and produces the charts/tables described in §6 without manual data wrangling.
4. The SUS scoring in the consolidation script matches hand-calculated scores for a known test response set (verify against a worked example from Brooke's original paper or any standard reference before trusting it on real participant data).
5. None of the study-mode-only UI additions (consent screen, task-completion button) appear for non-study users of the product.

## 8a. Data pseudonymization and retention
The 'required Aurora exports' referenced in §9 must be a pseudonymized export (retaining `study_participant_code` but excluding `users.email`): it may include `study_participant_code`, `study_events` rows, task timestamps, and SUS responses, but must exclude `users.email` and any other directly identifying field. Produce this pseudonymized export as part of `consolidate_results.py`'s output, before teardown.

If `infra/teardown_m4.sh` takes an Aurora snapshot as a safety net before deleting the cluster (per §9), that snapshot contains the raw `users` table with real emails linked to participant codes, and is therefore identifiable data, not the pseudonymized export described above. This snapshot, and any other raw copy containing the email-to-participant-code link, must be deleted by 14 November 2026 — the same deadline promised to participants in §4. Once the email-to-code link is deleted by that date, the retained export becomes fully de-identified rather than merely pseudonymized: within retained project datasets and managed exports, no retained record maps the participant code back to identity. This does not extend to backups, logs, or infrastructure outside the project's own datasets and exports (e.g. AWS account-level logging or backups outside the team's control), which are out of scope for this claim.

## 9. Final teardown and what happens after this milestone

After `consolidate_results.py` has completed and its report-ready outputs plus required Aurora exports are retained, run `infra/teardown_m4.sh`. It deletes the Flask ASG, Launch Template, and ALB; snapshots then deletes Aurora if the retained export is sufficient; and confirms no Flask-tier or GPU instances remain. This is the only final teardown for the live M3/M4 product stack.

This is the last milestone in the roadmap. Its output — consolidated benchmark results, SUS scores, and qualitative feedback — feeds directly into the Final Report, and any usability issues surfaced (per the report's own stated plan) are documented as findings and scoped as post-submission future work, not built blindly into this spec.
