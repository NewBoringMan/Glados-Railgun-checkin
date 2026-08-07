# GLaDOS Account Center V3 migration

V3 is developed on `v3/account-center`. Production remains on `master` until the real Mac / five-account release-candidate gate is accepted.

## Safety boundary

- Backup branch: `backup/pre-v3-account-center-20260807`.
- Existing `GLADOS_ACCOUNT_<ACCOUNT_KEY>` secrets are reused; V3 never reads their plaintext back from GitHub.
- V3 account configuration lives in `.github/glados/accounts.v3.json`, separate from the V2 production configuration.
- V3 runtime is standalone and emits coarse operational state only. It does not intentionally log email addresses, Cookie values, exact point balances, point history, or persistent account keys.
- Dynamic matrix rows expose an ephemeral slot, Secret name, and a non-reversible exchange-lock identifier; job metadata does not expose the stable account key.
- One matrix job handles one account and `max-parallel` is `1`.
- GET operations have bounded retry. Check-in and exchange POST operations are sent at most once per runtime invocation and are never automatically retried.
- 401/403 and challenge/CAPTCHA-like responses fail closed.
- Manual `read_only` mode performs GET-only status/points capability validation and cannot check in or exchange.

## Production schedule

- Primary: `05:07` Asia/Taipei.
- Recovery: `17:07` Asia/Taipei.
- Scheduled recovery checks GitHub's corresponding morning **primary slot** first. A successful morning slot suppresses the afternoon account task entirely.
- If GitHub history cannot prove the morning slot succeeded, Recovery remains available rather than suppressing a potentially needed fallback.
- Runtime recovery also treats only explicit GLaDOS check-in-labelled history as reliable evidence; positive reward points alone never suppress recovery.

Scheduled V3 workflows become production-active only after the V3 workflow is merged to the default branch.

## Exchange safety

V3 keeps `.github/glados/exchange_plans.json` as the trust anchor and also reads the live plan list returned by GLaDOS. Automatic spending uses only the exact `(plan id, points, days)` intersection of `live ∩ verified`.

Selection order is exact rational cost (`points / days`), then shorter duration, lower point requirement, stable plan id. If live plan metadata is absent, changed, or mismatched, check-in remains allowed but automatic exchange is held.

After a successful exchange POST V3 re-reads points and membership days. The exchange is accepted only when points decreased by at least the plan cost **and** membership days increased by approximately the promised duration. Ambiguous verification fails the job and creates a privacy-safe GitHub `GLaDOS exchange lock <hash>` issue. Future automatic exchange for that account stays frozen until explicitly cleared in Account Center after review.

## Configuration model

`accounts.v3.json` contains automation metadata only:

- enabled / archived
- autoExchange
- generic label
- global pause state
- schedule metadata

Local display names and notes are not encoded into the public repository. Cookie values remain in independent GitHub Actions Secrets and, after user-approved migration, the macOS Keychain used by Account Center V3.

## Desktop release-candidate architecture

The macOS V3 RC uses bundle identifier `com.enoch.glados-account-center`, so V2 → V3 is an in-place application upgrade. The RC includes:

- macOS Keychain credential store behind a `CredentialStore` abstraction
- durable local history/state with restrictive filesystem permissions
- direct read-only GLaDOS refresh independent of GitHub network availability
- Daily Punch evidence merging from GLaDOS, local operations and privacy-safe GitHub primary/recovery job results
- analytics, exchange prediction/history and explicit verification of newly discovered plans
- Health Center with separate GitHub auth/network/Secret/workflow, GLaDOS, Keychain and Safari diagnostics
- account search/filter/bulk management, archive/restore, global pause, local config rollback and redacted diagnostics
- macOS notifications and Menu Bar controls
- embedded Safari Web Extension inside the main app
- optional Chromium-family and Firefox Companion Extension with a local Native Messaging Host
- verified legacy/duplicate-app cleanup, pre-install backup, test-before-overwrite and rollback path

## Automated acceptance completed before human gate

Machine-verifiable acceptance includes V3 Python unit/flow tests, mock GLaDOS HTTP integration, mock GitHub recovery-gate tests, Swift source parsing, app model/store type checks, browser/Native Messaging JS tests, package/static privacy checks, Secret/PII scan, Action SHA-pin policy, installer/rollback invariants, and a GET-only GitHub Canary.

A previous five-slot GET-only Canary confirmed 4/5 existing independent Secrets. Slot 3 failed because its dedicated `GLADOS_ACCOUNT_...` Secret was empty/missing; the other four were readable. This is intentionally left as a visible migration blocker rather than silently dropping the account. Re-reading that account from the authenticated browser will repair the dedicated Secret and simultaneously establish its local Keychain copy.

## Final human promotion gate

Before merging to `master`:

1. Full Xcode Release build, XCTest, embedded Safari extension and codesign checks pass on the user's Mac.
2. V2 → V3 installs in place and old Assistant/Bridge/duplicate copies are gone.
3. Embedded Safari extension is enabled; optional Companion Extension is loaded where desired.
4. Each of the five managed accounts is re-read once so macOS Keychain and independent GitHub Secret health are 5/5.
5. Direct local read-only refresh is 5/5 and the visible plan/points/days/punch data matches GLaDOS.
6. GET-only V3 Canary is 5/5.
7. One controlled Primary Canary and the corresponding Recovery Canary are verified without duplicate side effects.
8. The Draft PR is then promoted/merged. V2 schedule remains enabled until the first successful production V3 primary run; only then is the V2 scheduled workflow retired.

No human-gate failure causes automatic production promotion.
