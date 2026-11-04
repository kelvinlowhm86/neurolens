# NeuroLens — M3k Implementation Spec: early access by request, 1,000-view curve rule

Status: current design. Builds on M3i (curve collection), M3j (checked facts, reward rules) and
M3b §7a (granting credit). Where this spec and an older one disagree, this one wins, and the older
section is updated to match (§10).

## Decisions (4 Nov, Josh)
- Free credit goes to people who bring data: short-form creators and marketers with a channel of
  1,000+ followers on YouTube, Facebook, TikTok or Instagram. Not open to the general public; no
  separate path for teachers or researchers.
- A signed-in user asks inside the app; Josh screens each channel by hand and grants with
  `infra/grant_credit.py` (unchanged amount: `billing.invite_cents`, 5 minutes) or declines.
  Every request has a status the user sees: waiting, granted or declined. Nobody waits forever.
- The request is saved first, then Josh is notified by email through an SNS topic (the service
  that already emails him the alarms; SES would send "from" his Gmail address and risk spam, and we
  have no domain). A failed notification is logged as an error; the request stays saved. The
  database table, not the email, is the list of requests.
- The form asks only for the channel link and an optional note: Josh screens by opening the link,
  so a "creator / marketer" question adds nothing.
- New reward condition: when the server checked the video's view count (a YouTube lookup that
  answered and showed the count), it must be at least `research.curve_min_views` (1,000). Unknown
  views (Facebook, a failed lookup, a hidden count) pay as before: those users were screened, and
  Josh spot-checks by hand. Nothing extra is built for spot checks: `checked_views` is already
  null on exactly those curves.
- TikTok and Instagram curves are not accepted yet (Instagram gives only a picture of the curve;
  TikTok's API is unconfirmed). Revisit when such creators ask.
- A booth demo account is an ordinary account given credit with `grant_credit.py`: nothing built.

## 1. Scope
### In scope
§2–§9, their tests (§11), the docs (§10).
### Out of scope
TikTok/Instagram curve input; re-requesting after a decline; emailing the requester (needs SES and a domain); a way to take credit back or block an account (build only if someone cheats); an
admin web page.

## 2. Settings
- `config.json` `research.curve_min_views`: 1000.
- The topic: Terraform's `aws_sns_topic.access_requests` (`neurolens-access-requests`), with an
  email subscription to the existing `var.alert_email` (Josh's address, in the git-ignored
  `terraform.tfvars`). Its ARN is published as Parameter Store String
  `/neurolens/web/request_topic_arn` (one more entry in `aws_ssm_parameter.web_deployment`).
  `web/lambda_handler.DEPLOYMENT_PARAMETERS` gains `"/neurolens/web/request_topic_arn": (None,
  "request_topic_arn")`, so a deployed function without it fails to start.

## 3. Migration 012 (`infra/migrations/012_access_requests.sql`)
```sql
CREATE TABLE access_requests (
  user_id     TEXT PRIMARY KEY REFERENCES users(user_id),
  channel_url TEXT NOT NULL,
  platform    TEXT NOT NULL CHECK (platform IN ('youtube','facebook','tiktok','instagram')),
  note        TEXT,
  status      TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting','granted','declined')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at  TIMESTAMPTZ
);
```
One row per account (the primary key).

## 4. Requests (`neurolens/access.py`, pure Python + `db`)
- `platform_of(url) -> str`: `https://` (or `http://`) only; the host, lowercased with a leading
  `www.` or `m.` removed, must be `youtube.com`, `facebook.com`, `tiktok.com` or `instagram.com`,
  and the path must not be empty or `/`. Anything else raises `ValueError` with "Paste your
  YouTube, Facebook, TikTok or Instagram channel link." Length at most 300 characters.
- `add_request(db, user_id, channel_url, note)`: validates (note stripped, empty → null, at most 500 characters), then `INSERT ... ON CONFLICT DO NOTHING
  RETURNING`. An existing row raises `AlreadyRequested`. Returns the stored request (no email).
- `request_status(db, user_id) -> 'waiting' | 'granted' | 'declined' | None`.
- `waiting_requests(db)`: rows with the user's email, oldest first, for the operator script.
- `decline(db, email)`: sets a `waiting` request to `declined`, `decided_at = now()`; returns
  False when there is no waiting request for that email.
- `billing.grant_credit` also sets the user's request (if any, and not already `granted`) to
  `granted` with `decided_at = now()`, in the same transaction as the credit. So any grant,
  including one to a declined user, ends the request as granted.
- `billing.delete_account` deletes the user's `access_requests` row in its transaction (it holds
  a channel link and a note; nothing financial is lost). `remove_deleted_account` needs no change.

## 5. Notifying Josh (`neurolens/notify.py`)
- `sns_notifier(region, topic_arn) -> callable(request, email)`: boto3 `sns.publish` to the
  topic, subject "NeuroLens access request (<platform>)", plain text: the requester's email,
  channel link, note, and the lines `python infra/access_requests.py list --backend data_api` and
  `python infra/grant_credit.py --backend data_api` (amount omitted: the invite credit).
- `create_app` gains `notify=None`. In Cognito mode with no `notify` it builds `sns_notifier`
  from `cfg["request_topic_arn"]` (missing → `ValueError` at start). Otherwise (dev mode, tests) the
  default logs "access request from user <user_id> (not emailed: dev mode)" at INFO.
- `infra/local_stack.py` passes a notifier that prints the request to the console.
- boto3 is imported inside the function, as elsewhere in `web/`.

## 6. Routes (`web/app.py`)
- `GET /api/me` adds `"access": {"funded": bool, "request": <request_status>}`. `funded` is the
  same "has had credit beyond the starter" fact the reward rule uses (one shared helper in
  `billing`, not a copy).
- `POST /api/access-request`, body `{channel_url, note?}`, signed in:
  - bad link or note → 400 `{error: "bad_request", message}`;
  - `AlreadyRequested` → 409 `already_requested`;
  - else 201 `{request: "waiting"}`. The row is committed before `notify` runs; a `notify`
    exception is caught, `logger.error("access request email failed for user <user_id>: <type>")`
    (no email address, link or note in the log), and the reply is still 201.

## 7. Reward rule (`billing._curve_reward`, M3j §5)
- New keyword `min_views` (default `DEFAULT_MIN_VIEWS = 1000`), passed by `add_curve`,
  `curve_offer` and both routes from `research.curve_min_views`.
- New condition `too_few_views`, after `length_mismatch` and before `too_early`: facts known,
  `facts.views` not None, and `facts.views < min_views`. It returns `cents` (what the curve would
  pay once it qualifies) and `reward_from` only when the date is also still ahead (else null).
  The save pays nothing for it and stores the curve as now.
- `facts.views` None (hidden count) or facts unknown → the condition is skipped.
- `list_jobs`' preview is unchanged (ignores views, like the date and the length).
- `POST /api/jobs/<id>/retention/check` adds `checked_views` (facts only, else null) and
  `min_views` to its reply.

## 8. Page (`static/app.html`)
The no-credit panel (shown when the credit is below one video's price) shows one of:
- **not funded, no request:** heading "Get 5 free minutes", line "For creators and marketers
  with 1,000+ followers. We look at each channel by hand.", fields "Channel link" (placeholder
  `https://www.youtube.com/@yourchannel`) and "Anything else? (optional)", button "Ask for minutes". Errors show the server's message under the form.
  The 5 comes from `billing.invite_cents` via `/api/limits` (`invite_seconds`), never typed in.
- **waiting:** "Request received. We'll look at your channel and add your minutes here."
- **declined:** "Thanks for asking. Free minutes are for channels with 1,000+ followers for now."
- **funded (any request status):** "You've used your credit. Add a shared video's retention
  curve to get its credit back, or ask the NeuroLens team for more."
- After a successful request the panel switches to "waiting" without a reload.
- Retention form line for `too_few_views`: "This video has 640 views. Add its curve once it has
  1,000 and get X back." With `reward_from` too: "...once it has 1,000 views, from 14 Nov (a week
  after it went public), and get X back." Reply after save: "Thanks, saved." then the same line.

## 9. Other pages and tools
- **Landing (`static/index.html`), Early access:** "Samples" "Open" "Explore every sample without
  an account."; "Your videos" "Free to try" "Creators and marketers get 5 free minutes.*" with a
  button "Get free minutes" to `/login`; "Shared" "Keep going" "Add a video's retention curve and
  earn its credit back."; under the columns, small: "*For channels with 1,000+ followers."
- **Guide (`static/retention_guide.html`):** the YouTube section's line gains "(and has 1,000+
  views)"; the Facebook line is unchanged.
- **Privacy (`static/privacy.html`):** "What we collect" adds "An access request, if you ask for
  free minutes: your channel link and any note. It is emailed to the NeuroLens team (through
  Amazon Web Services) and kept until you delete your account." The reward rules
  add "and, when we can check it, has at least 1,000 views".
- **`infra/access_requests.py`** (`--backend data_api|postgres`, like `grant_credit.py`):
  `list` prints waiting requests (date, email, platform, channel link, note), oldest first;
  `decline` asks for the email at a prompt, shows the request, asks to confirm, then declines.
  Granting stays `grant_credit.py`.
- **Terraform:** the topic and its email subscription (Josh clicks the one confirmation email
  once after apply); the web app function's role gets `sns:Publish` on that topic only; the
  `/neurolens/web/request_topic_arn` parameter (§2). The deploy policy and role boundary in
  `infra/iam/` already allow `neurolens-*` topics: no change there.
  No standing cost; the first 1,000 emails a month are free.

## 10. Older specs updated with this build
M3d §8b (landing Early access copy), M3i (invite wording, reward conditions), M3j §5 (condition
order), §6 (check reply), §8 (form lines), §9 (guide and privacy), M3b §7a (grant marks the
request).

## 11. Tests (written first by a separate agent; PostgreSQL for billing and requests)
Full treatment for the reward rule and the grant; light for the rest. Behaviour, not call details.
- **Reward (PostgreSQL, move dates, never sleep):** checked views 999 → `too_few_views`, no
  credit, curve stored with `checked_views` 999; 1000 → rewarded; hidden views (None) → rewarded;
  unknown facts (Facebook, failed lookup) → rewarded as before; few views and too early →
  `too_few_views` with `reward_from`; few views but length mismatch → `length_mismatch`; the check
  and the save agree on every case; the existing M3f/M3i/M3j reward tests still pass.
- **Requests:** a valid request is stored as waiting; each bad link/long note → 400 and
  nothing stored; a second request → 409; not signed in → refused; `/api/me` reports funded and
  each status; `grant_credit` marks a waiting and a declined request granted and leaves no
  request alone; `decline` changes only a waiting request; deleting the account removes the row.
- **Email:** the notifier is called once with the stored request; a raising notifier still gives
  201, keeps the row and logs an error that holds no email address, link or note; the deployed
  launcher fails to start without `/neurolens/web/request_topic_arn`.
- **Copy:** the landing page's "5 free minutes" equals `invite_cents` at today's price; the
  1,000 *views* on the guide, privacy and app pages equals `research.curve_min_views` (the
  "1,000+ followers" screening bar is plain copy, not tied to that setting); no page
  says "Invited". (`tests/test_m3i_web.py`'s pinned "invited creators..." wording changes on
  purpose; the commit says so.)
- **Script:** `access_requests.py list` shows waiting requests only; `decline` without "y"
  changes nothing.
- **Break-it checks** (on committed code, restore with `git checkout <file>`): drop the views
  condition; make `grant_credit` skip marking the request; let a `notify` exception escape the
  route. Each must turn a test red.

## 12. Known limitations (accepted)
- Views are checked only on YouTube; elsewhere the screening and Josh's spot checks are the
  guard. A user could claim a curve is from another, larger video only on Facebook (no lookup).
- A view count is read at save time; a video that later drops below 1,000 (rare) keeps its credit.
- One request per account; a declined user cannot ask again (Josh can still grant by hand).
- Anyone can make an account and ask; the cost is one email to Josh and one row (stopping rule).
