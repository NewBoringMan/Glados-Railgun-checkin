# GLaDOS Account Center V3 migration

V3 is developed on `v3/account-center`. Production remains on `master` until the V3 beta and cloud workflow are accepted.

## Safety boundary

- Backup branch: `backup/pre-v3-account-center-20260807`.
- Existing `GLADOS_ACCOUNT_<ACCOUNT_KEY>` secrets are reused; V3 does not read their plaintext from GitHub.
- V3 account configuration lives in `.github/glados/accounts.v3.json`, separate from the V2 production configuration.
- The V3 workflow emits operational state only. It does not intentionally log email addresses, Cookie values, exact points balances, or points history.
- One matrix job handles one account and `max-parallel` is `1`.
- POST check-in and exchange operations are not automatically retried by the V2 engine used underneath V3.

## Planned production schedule

- Primary: `05:07` Asia/Taipei.
- Recovery: `17:07` Asia/Taipei.
- Recovery first checks whether the current day already has a reliable check-in record. If so, it exits without another check-in POST.

Scheduled workflows only become production-active after the V3 workflow is merged to the default branch.

## Configuration model

`accounts.v3.json` contains only non-secret automation metadata:

- enabled / archived
- autoExchange
- label / optional local note when managed by the app
- global pause state
- schedule metadata

Cookie values remain in GitHub Actions Secrets and, after user-approved migration, the macOS Keychain used by Account Center V3.

## V3 desktop architecture

The macOS beta uses the existing bundle identifier `com.enoch.glados-account-center` so V2 -> V3 is an in-place application upgrade. It adds:

- macOS Keychain credential store
- local durable state/history store
- direct read-only GLaDOS status refresh independent of GitHub network availability
- system health page with separate GitHub auth/network and GLaDOS diagnostics
- account archive, global pause, analytics and Daily Punch history
- embedded Safari Web Extension inside the main app; no separate Safari Bridge app

Existing accounts need to be re-read once from an authenticated browser to establish their local Keychain copy. This does not create a duplicate account because the stable account key maps back to the existing GitHub secret.

## Promotion gate

Before merging to `master`:

1. V3 Python regression tests pass.
2. macOS Swift sources parse/build on the user's Mac with full Xcode.
3. Embedded Safari extension is enabled and can capture a user-approved GLaDOS session.
4. Each of the five managed accounts is migrated to Keychain and read-only status refresh succeeds.
5. Manual primary and recovery canary runs are verified without duplicate check-in side effects.
6. Production V2 schedule remains enabled until V3 cloud canary succeeds.
7. Merge is followed by disabling/removing the V2 scheduled workflow only after the first successful V3 primary run.
