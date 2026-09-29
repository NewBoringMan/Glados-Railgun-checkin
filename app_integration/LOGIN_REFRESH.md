# Account Center login refresh — candidate integration contract

## Same-app UI integration increment (not installed)

The current installed 2.0.9 Build 20011 is the update base; an old V2 source archive
is NOT a prerequisite for the independent in-process maintenance/policy modules.
The original compiled main account-card implementation is left untouched.

New source in this increment:
- `RefreshCenter.swift`: an internal, same-process maintenance window with account
  emails, local email entry, Gmail setup, consent and dependency status.
- `login_refresh_app.py`: JSON-lines host adapter. Reads the real account registry,
  retains local email identities, labels new user-entered email targets as unverified,
  and exposes the previously implemented mailbox configuration/consent functions.
  It does not expose live credential publication or enable batch/monthly automation.
- `AccountEmailDirectory.swift`: read-only private SQLite adapter reused by the existing
  policy editor, so a failed/empty status cache no longer erases its stored email name.
- `PolicyMenuPlugin.m`: source for a visible Login Maintenance button/menu alongside
  the original Exchange Policy button, without another application or main-binary edit.

On the target Mac all three UI libraries typechecked and linked; the new window entry
symbol was present. The existing 254 tests passed. A real source-entry snapshot through
isolated Python returned 26 accounts, five confirmed local emails, Mail running and
monthly execution disabled. Private email values were not printed. This is not a GUI
screenshot/interaction acceptance and does not prove the original cards were modified.

The additional runner, new packaging/installation script and new test-file write were
not executed/created because those tool calls were denied. No alternative route was
used to perform the denied installation or credential actions. The new native/UI
sources are retained, but packaging and installation are pending. The existing
`build-v209.sh` predates these extra source inputs; do not present it as a complete
builder for this increment. Its policy editor target must eventually include
`AccountEmailDirectory.swift` and SQLite, and the new module/resources must be added
in an authorized packaging step before replacing the existing app.

Do not mark the draft ready for release from typecheck or the old regression count.
The running/installed app remains Build 20011 and the production branch is unchanged.


Status: candidate core, native HTTP login, Mail prerequisite, Gmail read-only REST,
Desktop OAuth/PKCE and native Keychain components are implemented. The Keychain component
has passed a real noninteractive synthetic-item test on the target Mac. Five historical
email identities have been persisted privately. Real Google consent, successful GLaDOS
login, native UI integration, cloud credential publication and monthly scheduling are
NOT complete. No production automation is enabled. This is not an installable release.

## Latest continuation — protected credentials and durable identity

The current installed app remains Build 20011. A later single-account authorization
request, made with Mail running, returned `captcha_required=true` and was stopped. No
challenge retry, alternative domain, fingerprint spoofing or credential publication
followed. In this increment no login/code request was made at all.

Implemented `login_refresh_oauth.py`:
- User-supplied Desktop OAuth client only; pinned official Google endpoints.
- Short-lived IPv4 loopback receiver, S256 PKCE, unguessable state, exact Host/path,
  duplicate-parameter rejection, single-use code exchange, no query/code logging.
- Only Gmail read-only scope; authenticated mailbox validation before saving/replacing
  a credential. An expired access token refreshes without another consent flow; an
  expired/revoked refresh token requests human authorization. Shared network faults
  do not delete the previous protected record.
- No implicit browser opening. The host must present initial consent through the user's
  approved interaction path. No Google OAuth client was created or imported, and no
  real Google account consent was performed here.

Implemented `app_integration/RefreshSecretStore.swift`, an internal helper, NOT another
`.app`. It addresses only this app's fixed Keychain service and validated record keys.
It communicates over anonymous pipes; secrets are not command arguments or file data.
It never changes system Keychain settings or other applications' items, and disallows
interactive authentication. A locked/denied Keychain pauses instead of opening a prompt.
`tools/test_refresh_keychain.py` compiled and signed it, verified its signature, completed
synthetic put/get/update/delete against the separate self-test service, verified removal,
and rejected out-of-scope requests. Its temporary binary and module cache were removed.
This test does not claim the helper is installed into the signed production bundle.

Implemented `login_refresh_identity.py` and `tools/recover_login_identities.py`:
- Require a matching snapshot SHA and current registered account keys.
- Reject conflicting key/email mappings before applying any of a batch.
- Keep historical provenance and source time separate from current authentication.
- Applied five matching historical identities to the existing app data directory's
  `login-refresh.sqlite`, permission 0600; reopened and verified five rows and zero jobs.
- The existing 26-row status cache is unchanged. The remaining 21 identities were not
  guessed. No email was added to the public repository, and no remote writes occurred.
  This private state is required app data, not disposable work garbage.

Verification: 254 Python tests pass (195 prior + 47 OAuth/pipe tests + 12 identity tests),
plus native signed Keychain self-test. OAuth tests include an actual local callback
socket with synthetic data; they do NOT exercise Google's live service.

Source recovery: metadata for the original `GLaDOS-Account-Center-macOS-v2.0.4.zip`
was located in the user's Library. Its original local download path no longer exists.
The file tool denied raw-byte materialization and returned no readable archive content.
This is a known source retrieval gap, not justification to replace V2 with V3 or binary
patch the main window. A readable authorized copy is still needed for main UI integration.
The previous two uncommitted live-probe drivers are disposable and are removed after
confirming their bounded runtime has exited; keep the reusable Keychain test/migration
scripts as maintained source.

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

## Mail adapter contract — local forwarding dependency

The user confirmed that Apple Mail was closed during the earlier test. After the
user opened it, the outstanding original message (2026-09-29 16:12:26 Asia/Taipei)
was forwarded into Gmail at 16:50:37. The 38-minute delay means that code is expired;
a new arrival does not reset its ten-minute lifetime. This later observation
supersedes the earlier "no forwarded message observed" status, but it does not prove
full login success or that all accounts' forwarding routes work.

For this installation, Apple Mail is a REQUIRED LOCAL FORWARDING COMPONENT, not
merely a viewer. Reading Gmail through its API does not run a rule installed in Mail.
Retain the user's existing forwarding rules; do not require switching 26 providers'
server-side rules merely to fix this missing application prerequisite.

Required order: prepare Mail in the background -> verify source mailbox/check-new-mail
readiness and Gmail access -> collect the Gmail baseline -> record request time/intent
-> request one code -> poll Gmail while monitoring Mail -> validate fresh code/identity.
Perform this preparation before a monthly batch and each retry pass, and recheck
Mail immediately before every account's code request. Keep Mail running throughout
all active waits. Do not close it after the job, whether the user or the job started it.

`login_refresh_mail.py` implements a bounded process-lifecycle guard. The native probe
reads only the exact Mail executable's PID/start time. The HTTP client's
`prepare_delivery()` must be called before baseline collection; `request_code()` now
refuses to send without a prepared, currently running Mail instance. `check_delivery()`
is exposed for every receiver polling iteration. A loss/restart of Mail or a long
sleep gap invalidates readiness. Pausing this shared dependency does not invalidate
26 cookies or justify 26 login retries. Already-requested codes retain their original
expiry; neither restoring Mail nor replaying a forwarded message restarts the clock.

A cold-start hook is available to the native host for ONE DCF-approved background
start, followed by a real process recheck; absent/denied launch support pauses before
sending. This hook is NOT yet wired to a production launcher. The reviewed DCF tool
surface does not provide a dedicated background application-launch action, and the
LocalAnt tool route remains unavailable in this turn. Do not invent such a tool,
use direct osascript/open, enable foreground permission, or alter DCF to work around it.
Mail is already running in the live environment; no restart or focus change was needed.
If the user quits Mail mid-job, pause rather than repeatedly reopening it against them.

A stable process and the 15-second bounded warmup DO NOT prove mailbox connectivity,
sync completion, automatic polling configuration or forwarding delivery. The native
integration must verify those separately; a manual-only fetching setting or offline
source account remains a blocking/diagnostic state, not a green "ready" inferred from
sleeping for a fixed duration. A preflight denial must occur before reserving an
attempt. If a later gate denies after intent reservation but before the HTTP send,
the runner must reconcile that definite non-send instead of consuming a login retry.
Queue-to-host integration is still pending, so this is not claimed as a full runner test.

Use Gmail's official read-only API for reading the dedicated receiving mailbox.
Do not scrape Mail's UI or private message database. The
standalone app needs its own OAuth authorization; a ChatGPT Gmail connection cannot
be exported as the app's credential. Request `gmail.readonly`, not send/delete/modify.
This scope can read the mailbox; filtering to GLaDOS messages is application logic,
NOT a Gmail-enforced per-label permission boundary. Keep OAuth tokens in macOS Keychain.
Check the consent application's production/testing status and token lifetime before
claiming unattended monthly operation.

After the Mail prerequisite and mailbox readiness checks, verify Gmail access and
capture current message IDs as the attempt baseline. Do not take that baseline before
Mail has had a chance to process its pending messages. Retain the baseline, request timestamp, mailbox and target email as
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
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_login_refresh*.py' -v
```

The initial candidate had 67 core tests. The current candidate additionally has
50 HTTP/Gmail tests and 29 Mail-lifecycle tests: 146 refresh-module tests passed on
the target Mac. A real read-only Mail process/preflight check passed without launching
or quitting Mail. These results do not prove automatic DCF startup, mailbox-online
readiness, OAuth consent, complete login, native UI integration or monthly execution.

## Primary references

- Google Gmail scopes: https://developers.google.com/workspace/gmail/api/auth/scopes
- Gmail message resource: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages
- Native OAuth: https://developers.google.com/identity/protocols/oauth2/native-app
- OAuth lifecycle: https://developers.google.com/identity/protocols/oauth2
- Apple timed jobs: https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html
