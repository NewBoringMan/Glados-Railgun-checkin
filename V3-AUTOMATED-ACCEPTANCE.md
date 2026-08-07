# V3 automated acceptance

This document distinguishes machine-verifiable completion from the final real-Mac / real-account release gate.

## Stage A — automated implementation complete

- privacy-minimized standalone Actions runtime
- GitHub auth/network diagnostics separated in the desktop app
- 05:07 primary and 17:07 recovery schedule (Asia/Taipei)
- recovery suppresses a second account task only when the matching morning primary slot succeeded
- monthly scheduler heartbeat
- official GitHub Actions pinned to immutable commit SHAs
- exact V3 Python runtime dependency pin

## Stage B — automated implementation complete

The V3 desktop release-candidate package contains a formal Xcode/XcodeGen project with application, embedded Safari Web Extension, Native Messaging companion host and XCTest targets; macOS Keychain credential storage; durable local history/state; direct GLaDOS read-only refresh independent of GitHub; fixed bundle identifier for in-place upgrade; and backup/test/rollback-aware installation.

## Stage C — automated implementation complete

- Daily Punch evidence model with up to 730 days of local history
- GLaDOS/local/GitHub cloud-result evidence merging
- points and membership-days trends, sign-in success rate, exchange prediction/history
- live exchange plan discovery with explicit user verification
- exact `live ∩ verified` automatic spending policy
- post-exchange points + membership-days verification
- persistent privacy-safe exchange safety lock after ambiguous verification
- dynamic sequential account matrix

## Stage D — automated implementation complete

The desktop RC contains Health Center, notifications, Menu Bar controls, global pause, archive/restore, search/filter/bulk management, local configuration rollback, redacted diagnostics, embedded Safari capture, optional Chromium-family/Firefox companion capture, and unified installation/update/rollback tooling.

## Stage E — machine gates

Implemented machine gates include:

- Python V3 unit tests
- end-to-end exchange flow tests
- mock GLaDOS HTTP integration test
- mock GitHub recovery-gate tests
- Secret/PII leak scans
- immutable Action-pin policy checks
- Swift source parse checks
- desktop model/store type checks
- browser/Safari/Native Messaging JS tests
- package structure/privacy/installer/rollback invariants
- GET-only GitHub Canary

### Known real-account result

The current five-slot GET-only Canary proved four independent existing account Secrets were readable. Slot 3 failed because the matching dedicated account Secret was empty/missing. No check-in or exchange POST was sent by that Canary. The account remains in configuration and is deliberately not silently skipped or deleted.

## Remaining human-only release gates

The following are intentionally not represented as completed until executed on the user's Mac and live accounts:

1. full Xcode Release build + XCTest + codesign
2. V2 → V3 in-place install and legacy-copy cleanup
3. Safari extension enablement / optional companion extension loading
4. five-account Keychain re-capture and independent GitHub Secret health 5/5
5. local GLaDOS direct read-only refresh 5/5
6. GET-only cloud Canary 5/5
7. one controlled primary + recovery live Canary with no duplicate side effect
8. Draft PR promotion/merge; first production V3 primary success; only then retire V2 scheduled workflow

Until those pass, PR #1 remains Draft and production `master` remains V2.
