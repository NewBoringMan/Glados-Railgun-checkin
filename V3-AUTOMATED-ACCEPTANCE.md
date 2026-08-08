# V3 automated acceptance

This document distinguishes machine-verifiable completion from the final real-Mac / real-account release gate.

## Stage A — automated implementation complete

- privacy-minimized standalone Actions runtime
- GitHub auth/network diagnostics separated in the desktop app
- 05:07 primary and 17:07 recovery schedule (Asia/Taipei)
- independent `productionScheduleEnabled` arm; Release Candidate is disarmed even if merged
- recovery suppresses a second account task only when the matching morning primary slot succeeded
- monthly scheduler heartbeat
- official GitHub Actions pinned to immutable commit SHAs
- complete V3 Python dependency closure pinned
- manual account workflow input uses privacy-safe lock id rather than persistent account key

## Stage B — automated implementation complete

The V3 desktop release-candidate package contains a formal Xcode/XcodeGen project with application, embedded Safari Web Extension, Native Messaging companion host and XCTest targets; macOS Keychain credential storage; durable local history/state; direct GLaDOS read-only refresh independent of GitHub; fixed bundle identifier for in-place upgrade; automatic RC→master branch following after production promotion; and backup/test/rollback-aware installation.

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
- immutable Action-pin and dependency-pin policy checks
- Swift source parse checks
- desktop model/store type checks
- browser/Safari/Native Messaging JS tests
- package structure/privacy/installer/rollback invariants
- GET-only GitHub Canary

### Current real-account result

The managed account set was synchronized after the old `332A23567057FBF5` account was replaced by `69B9338D952FEE8D`. The subsequent five-slot GET-only Canary completed successfully for slots 1–5. All five current independent GitHub account Secrets are therefore readable by the V3 RC workflow. The Canary sent no check-in or exchange POSTs.

## Remaining human-only release gates

1. full Xcode Release build + XCTest + codesign
2. V2 → V3 in-place install and legacy-copy cleanup
3. Safari extension enablement / optional companion extension loading
4. five-account Keychain health 5/5
5. local GLaDOS direct read-only refresh 5/5 and visible data sanity check
6. one controlled RC primary + recovery live Canary with no duplicate side effect
7. merge Draft PR while V3 production schedule remains disarmed
8. verify one manual V3 primary on `master`
9. retire V2 schedule and arm V3 schedule as the explicit final cutover

Until those pass, PR #1 remains Draft and production `master` remains V2. A PR merge by itself cannot activate V3 scheduled sign-in while the production arm is false.
