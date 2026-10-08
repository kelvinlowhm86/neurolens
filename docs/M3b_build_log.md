# M3b build log

Measurements, choices and evidence for `docs/M3b_spec.md`. Newest decisions are folded into the
sections, not appended as a diary.

## Acceptance criteria (§12)

| # | Result | Evidence |
|---|---|---|
| 1 | Pass (2026-10-08) | Day one (71982f8): Google and email sign-in through Cognito with the CloudFront callback; `email_verified` mapped for Google; the account's Lambda concurrency limit is 1,000 (`aws lambda get-account-settings`) |
| 2 | Pass (2026-10-08) | A Google account not on the test-user list signed in after the app was published: one new user, 0 available, 0 reserved, no ledger rows, one identity. Signed-out API calls answer 401 (tests) |
| 3 | Pass (2026-10-08) | Google and email/password sign-ins with the same verified email: 2 identities, 1 user, one balance |
| 4 | Pass (2026-10-08) | Tests; break-it (dropping the owner filter in `owned_job` failed 4 tests); Josh checked History and the CSV against the result on the live site |
| 5 | Pass (2026-10-08) | `/healthz` straight to the web function URL: 403; through CloudFront: 200 |
| 6 | Pass (2026-10-08) | Sign-in, `/api/me` and `/api/jobs` answered 200 at 08:28 UTC, after the NAT Gateway was destroyed |
| 7 | Pass (2026-10-08) | Fake-job rehearsal below |
| 8 | Pass (2026-10-08) | `stop_work.sh`: NOT CONFIRMED while the NAT Gateway existed; ALL STOPPED after `nat_gateway = false` was applied |
| 9 | **Open** | Re-check under the 3-hour schedule (below): Aurora pauses after each run, the database alarm stays OK through 6 idle hours, and the cost of one run |
| 10 | Pass (2026-10-08) | Stripe test top-up of $5: one `test_topup` ledger row, one `stripe_test_events` row of 500 cents, webhook "credited"; replays credit once (tests; break-it ignoring the "already recorded" answer failed 5 tests) |
| 11 | Pass (2026-10-08) | `grant_credit.py` on Aurora: unknown email refused, unconfirmed grant refused, mixed-case email granted 100 cents; balance 600 = ledger sum, rows `grant` 1 + `test_topup` 1 |
| 12 | Pass (2026-10-08) | Bucket CORS allows POST only from the site and `localhost:5003` / `127.0.0.1:5003` (the laptop app); the web app sends no CORS headers |
| 13 | Pass (2026-10-08) | `/security-review` of `main...aws-josh`: no HIGH or MEDIUM finding at confidence 0.8 or above |
| 14 | **Open** | Needs the GPU batch |
| 15 | Pass (2026-10-08) | Tests committed before the code (9471160, d11d615); CI green on the last pushes |

## Fake-job rehearsal (2026-10-08, `t3.large`, fake model)

| Step | Result |
|---|---|
| `start_work.sh` with `nat_gateway = false` | Refused ("No NAT Gateway is available"), group stayed 0/0/0 |
| `nat_gateway = true` with the rehearsal settings | NAT Gateway created in 1 min 38 s; `start_work.sh` found it routed and set max 1 |
| One 27.4 s clip through the live site | Uploaded 06:44:13 UTC, `done` 06:48:26 on attempt 1; client and measured duration both 27,400 ms; `reserve` -90/+90 then `capture` 0/-90; balance 510 = ledger sum; result in S3; queue and dead-letter queue empty |
| `stop_work.sh`, then an upload | Worker ended; NOT CONFIRMED (NAT Gateway still there); upload refused with "Processing is paused"; balance unchanged |
| `nat_gateway = false` with max 1 | Plan refused by the precondition |
| `stop_work.sh`, then `nat_gateway = false` | NAT Gateway destroyed in 1 min 2 s; the Elastic IP release failed (below) and a second apply released it; `stop_work.sh` ALL STOPPED; fresh plan: no changes |

Cost: about an hour of NAT Gateway and 10 minutes of `t3.large`, about $0.10.

## Measurements

- **Reaper, hourly (2026-10-08, 02:00-06:00 UTC, 4 runs):** each wake resumed Aurora at its maximum
  (2 ACU) and kept it awake about 11 minutes (the 300 s pause timer starts when the last connection
  closes): 0.18 ACU-hours a run, about $15 a month at $0.12 per ACU-hour. The spec's $4 a month had
  assumed 0.5 ACU for 5 minutes. Changed to every 3 hours with a 1 ACU maximum (580b408); the new
  cost per run is measured with criterion 9.
- **Aurora wake-up:** 15-21 s for the reaper; 19.7 s for a sign-in callback.
- **Web function cold start:** at 512 MB, app setup 4.57-5.45 s (settings 2.25-2.65 s, `create_app`
  2.33-2.79 s) plus 0.49-0.72 s container start; at 1,769 MB (one vCPU), 1.41 s (0.60 s and 0.82 s)
  plus 0.37 s. Batched Parameter Store reads were not added: below the 1.5 s threshold.
- **Duration from packets:** 0.1 s for an 11 MB clip, 0.4 s for a 251 MB 4K clip, equal to the
  header's length for both.

## Problems found live, and fixes

- **Reaper error alarm (02:06 UTC):** while resuming, the Data API answered `ThrottlingException`
  ("insufficient resources on the database") after two `DatabaseResumingException`s; AWS's own
  retry succeeded a minute later. `BeginTransaction` now waits on both codes (0723cfe). AWS does not
  document the throttle during a resume; it was seen once.
- **The scripts undid the database cap:** `start_work.sh` and `stop_work.sh` passed `MaxCapacity=2`;
  they now change only the minimum, which Terraform ignores (861c4d4, regression test).
- **NAT Gateway off:** Terraform unlinks the Elastic IP after the gateway is gone, and AWS checks
  `ec2:DisassociateAddress` against no resource (`*/*`, no tags; decoded with
  `sts:DecodeAuthorizationMessage`, 5f2218a), so the tag-scoped permission refused it. Unlinking is now
  allowed on any us-east-1 address while releasing stays tag-scoped (f47ae38). **Not yet proven:** the
  next NAT-off apply should finish in one run.
- **Independent review (sign-in and money):** no HIGH finding. Fixed test-first: a returning sign-in
  now keeps `users.email` current, so an address the user gave up can't link a stranger to the account;
  the worker measures a video's length from its packets, not its header (ea5ccfe; break-its caught).
- **Sign-out:** repeated clicks and stale tabs; buttons now disable while busy, `index.html` is served
  `no-cache`, and every request logs one line without the query string (d633c27, 9ed3a2b).

## Known limitations (accepted)

- A sign-in whose new email already belongs to another user keeps the old email and logs a warning.
- Logout can't cancel a copied session cookie before its 12-hour limit.
- The first request after Aurora pauses waits about 20 s; demos and study sessions hold it awake.
- A signed-in stranger calling the API every few minutes could keep Aurora awake (about $1.40 a day);
  the database alarm emails.
- After the circuit breaker fires, GPU work stays off until `start_work.sh`.
