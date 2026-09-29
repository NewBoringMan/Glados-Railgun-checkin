# Account Center login refresh — candidate integration contract

Status: the offline safety core, a bounded native HTTP login adapter, and the Gmail
read-only REST adapter are implemented. Only the code-request step has been tried
live; a fresh forwarded message and complete login have NOT been accepted. The native
application, Gmail OAuth consent/token storage, Keychain credential storage, cloud
publisher and monthly scheduler are NOT connected. No production automation is enabled.
This is not an installable Account Center release.

## 2026-09-29 continuation — measured boundary

- DevSpace recovered. The installed app was independently read as Build 20011.
- The current 26-row status cache has zero nonempty email fields. A matching historical
  cache backup contains five successful email records; the experiment identity was
  not present in that backup. No complete 26-account identity recovery is claimed.
- The installed `core.js` already derives account keys from the stable user ID with
  SHA-256 of `glados-user:<userId>`, truncated to 16 uppercase hex characters. The new
  adapter matches that algorithm and refuses keys outside the existing account set.
- Inspected the current public first-party client at
  `https://glados.cloud/app.bundle.js`: email-code requests go to `/api/authorization`,
  login goes to `/api/login`, and the site field is `glados.network`. The page also
  supplies its own optional Authorization context and handles a captcha-required
  response. We do not generate a fingerprint or bypass a human challenge.
- At 2026-09-29T08:12:24Z, exactly one authorized experiment code request from the Mac
  to the pinned cloud origin returned HTTP 200, code 0, captcha_required false, with
  fields `code` and `method`. This means accepted request, NOT confirmed mail delivery.
  Cookie values, codes and customer emails were not written into the repository.
- Repeated scoped Gmail inspection still found only the two pre-existing old forwarded
  sample messages. The search was widened within the authorized receiving mailbox to
  include redirected mail retaining its original To header, including spam/trash for
  diagnosis. No new message was found at the latest check. No code was resubmitted.
- No live login, Secret replacement, exchange, app installation or scheduled refresh
  was performed in this increment.

`login_refresh_http.py` pins HTTPS origins, blocks redirects, has zero automatic POST
retries, distinguishes ambiguous write timeouts, stops on captcha/permission errors,
requires authenticated identity matching and an existing stable key, and exposes no
check-in/exchange or Gmail mutation operation. Gmail profile verification uses the
same access token as each message request. RAW mail is size bounded and transport
authentication is checked before passing messages to the OTP parser.

The remaining live prerequisite is a fresh code arriving through the intended forward
route. After that, run the full single-account login acceptance before cloud writes.
Independent Gmail consent/token storage and native UI integration still remain; do
not describe this prerequisite as the only remaining implementation work.

## Scope and compatibility

Keep the existing Account Center bundle identifier, account keys, Secret names,
check-in jobs, exchange policies and schedules. Add the feature as an internal module
of the one existing app, not another `.app`. `login_refresh_core.py` is standard-library
Python code; it contains no browser/HTTP/Secret mutation and does not request a code.
The Python runtime path on the target Mac must be verified before any app integration.
Do not add a second interpreter solely for this component.

The current repository's native core is a prebuilt `GLaDOSAccountCenter.real` plus an
in-process policy plugin. The repository is not a complete native-app source checkout.
Recover and identify the authoritative core source before changing the account cards.
Do not guess an account from mouse position, accessibility geometry or binary offsets.
Do not announce that the native UI is upgraded based on this Python test suite.

## Responsibilities

Account Center owns identity, the serial refresh queue, Gmail authorization, login,
credential publication, recovery and local notifications. GitHub keeps the existing
check-in/exchange execution. ChatGPT may help diagnose failures, but is not part of
an OTP's ten-minute critical path. There must not be two independent refresh schedulers.

The eventual local scheduler should enter the same internal runner both from a user
button and a bundled background task. Store completed cycle and attempt state on disk;
resume after sleep instead of starting a duplicate cycle. Do not promise operation
while the Mac is powered off, the data volume is unavailable or a required login is
locked. Pause safely and resume when prerequisites recover. Only enable scheduling
after the one-account live acceptance gate passes.

## Private identity, not disposable status

Store the verified mapping `existing Account Key -> email` locally with owner-only
permissions, independently from status-cache.json. Never save a blank failed status
as a new identity. Do not derive a new account key or Secret name from the changing raw
cookie value. The existing stable user-ID hashing convention must be preserved. Existing per-account policies remain attached to their original keys.

`remember_verified_identity` accepts identity already verified by a trusted caller.
`import_successful_cache` is for an app-owned, previously successful status snapshot;
it is not authority to trust arbitrary imported JSON. A historical email is useful
for display and selecting the login email, but before publishing credentials the
new authenticated server-side identity must still match. An email typed into the
login form or found in a mail body is not sufficient proof of the resulting session.
A missing or conflicting mapping goes to manual resolution; never guess.

The source repository may be public. Do not commit real customer emails, email bodies,
OTP values, browser profiles, OAuth tokens, cookies, identity SQLite files or logs.
The source and tests contain fictitious examples only.

## Mail adapter contract

Use Gmail's official read-only API for the dedicated receiving mailbox; Apple Mail can
remain its normal viewer. Do not scrape Mail's UI or private message database. The
standalone app needs its own OAuth authorization; a ChatGPT Gmail connection cannot
be exported as the app's credential. Request `gmail.readonly`, not send/delete/modify.
This scope can read the mailbox; filtering to GLaDOS messages is application logic,
NOT a Gmail-enforced per-label permission boundary. Keep OAuth tokens in macOS Keychain.
Check the consent application's production/testing status and token lifetime before
claiming unattended monthly operation.

Before requesting an OTP, verify mailbox access and capture current message IDs as the
attempt baseline. Retain the baseline, request timestamp, mailbox and target email as
non-secret attempt state. Persist send intent before sending once. If the send response
is ambiguous, wait for that attempt rather than immediately sending another request.
Use individual Gmail message IDs, not concatenated conversation-thread bodies.

`MailEnvelope.authenticated_sender` must be supplied by an adapter that validates
Gmail's trusted outer transport authentication. Do not set it equal to the unverified
From header and do not trust a forged Authentication-Results inside forwarded content.
For inline forwarded mail, authenticity of the original GLaDOS header is not preserved
cryptographically: the authenticated, explicitly permitted forwarding mailbox is the
trust boundary. Original recipient and date still must match the current attempt.

The parser accepts one direct message, a single RFC822 forwarded attachment, or one
unambiguous plain/HTML inline forward with original From, To, Subject and Date. It
requires the original GLaDOS sender, exact intended original recipient, known forwarder,
a new message ID, an original issue time in this request window, and expiry margin.
The observed template has a six-digit code and ten-minute TTL; changes fail closed.
It preserves leading zeros, rejects arbitrary six-digit numbers and multiple codes,
and deduplicates copies of the same code. New forwarding arrival cannot revive an old
original. HTML is parsed locally without scripts or remote resource loads.

No request ID is present in the observed email template. Time, recipient and baseline
checks correlate a candidate; they do not cryptographically prove which concurrent
request issued it. Only one outstanding login is allowed, conflicting candidates stop,
and authenticated account identity must be verified after login. A selected candidate
is single-use within the attempt. Never try every six-digit value or replay an old
attempt's code. Never log `raw`, `CodeCandidate.code` or a serialization of those objects.

## Login and credential publication contract

Use the normal GLaDOS login flow through the user's background-only DCF entry point
when GUI is needed. Do not use direct automation of existing Safari/Brave profiles,
osascript, or foreground takeover. A native HTTP login adapter can replace GUI only
after the actual supported contract, security checks and session behavior have been
verified on the target account. The inspected endpoint contract is implemented in
`login_refresh_http.py`, but only requesting the email code has passed a live test;
full authentication and credential publication remain gated.

A human challenge, permission block or identity mismatch requires manual intervention;
do not change domains, proxies, fingerprints or providers to evade it. Pin each account
to its verified login origin; never spray OTPs or credentials across candidate domains.

After normal login, verify the authenticated account email/immutable user ID and a
read-only account response. Merely receiving a numeric points field or seeing a
loggedIn flag does not prove identity. Candidate cookies stay in process memory and,
only when needed for crash recovery, the app's protected Keychain item. They never go
into queue SQLite, console output, command-line arguments or plaintext backup files.

State order:

`queued -> preflight -> awaiting_code -> candidate_verified -> publish_pending`
`-> verify_pending -> done`

Record a publication intent before updating only the existing account Secret. Record
credential generation/expected target in private recovery state. Do not mark done just
because GitHub accepted the upload. Verify a new read-only cloud job created after the
upload, at the expected code revision, for the expected account. Do not use an old run
or a cached status. Do not trigger an exchange as a credential acceptance test.

GitHub Secret writes are not a multi-step transaction and old secret values cannot be
read back. A timeout after upload is ambiguous: verify and reconcile the same candidate,
not another email login. An old credential can be restored only if the app actually has
a previously validated protected copy. Never promise rollback from a Secret listing.
Coordinate the short publication window with current account jobs; existing runs may
still hold their previous credential generation. A failed old run must not overwrite
a newer successful health result.

## Three-pass retry and shared outages

Process one account at a time. Complete the first pass before the retry pass; successful
accounts are not retried. Suggested cool-down is 15 minutes then 120 minutes, respecting
any longer server Retry-After. At most three actual code requests per account per cycle;
restart must not reset this counter. Shared mailbox/network/service failures pause the
whole queue without spending the remaining accounts' attempt budgets. Preflight faults
are not failed login attempts. A challenge/mismatch goes to manual immediately.

Queue recovery after an interrupted send waits for the outstanding request's expiry;
after a candidate is verified or published it resumes that candidate/verification,
not another login. Human-required unresolved accounts must remain excluded by the app
until explicitly resolved; a new calendar month must not override a manual hold.
The core's cycle builder alone is not the app's eligibility policy.

A monthly pass is maintenance cadence, not a claim that cookies last one month. Use
existing check-in health results to request earlier repair when authentication truly
fails. Do not declare all accounts expired from one failed account or from HTTP403
alone. Distinguish service outage, challenge, permission failure and expired session.

## Acceptance gates before installing or enabling

1. Verify all existing keys/policies and recover available private identity mappings.
2. Authenticate the dedicated Gmail mailbox once; prove automatic forwarding delivers
   a FRESH code for the authorized experiment account (old forwarded examples do not
   prove the forwarding rule is installed).
3. Complete one background-safe fresh login; prove the resulting identity is correct.
4. Update only that existing Secret; run a fresh read-only cloud check, with no exchange.
5. Check the account list, policies and schedules are unchanged; failed cache refresh
   still leaves the email visible after an app restart.
6. Exercise mailbox outage, challenge, repeated notification, late forwarded mail,
   crash after upload and exact three-pass retry with isolated test fixtures.
7. Build/sign the same app; require zero nested or standalone helper `.app` bundles.
8. Enable the single local schedule only after live acceptance; then expand serially.
9. Clean disposable build/test files, but retain maintainable source, private account
   identities, protected credentials and necessary recovery state.

## Offline verification

From the repository root, using Python 3.10+ on macOS or Linux:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p test_login_refresh_core.py -v
```

The initial candidate passes 67 offline tests. This result proves parser/queue behavior
under those fixtures, not real email forwarding, OAuth consent, Safari behavior,
credential validity, native UI integration or unattended monthly execution.

## Primary references

- Google Gmail scopes: https://developers.google.com/workspace/gmail/api/auth/scopes
- Gmail message resource: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages
- Native OAuth: https://developers.google.com/identity/protocols/oauth2/native-app
- OAuth lifecycle: https://developers.google.com/identity/protocols/oauth2
- Apple timed jobs: https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html
