# V3 automated acceptance — RC5

This document distinguishes machine-verifiable completion from the final user-Mac / live-account release gate.

## Stage A — automated implementation complete

- privacy-minimized standalone Actions runtime
- GitHub auth/network diagnostics separated in the desktop app
- 05:07 primary and 17:07 recovery schedule (Asia/Taipei)
- independent `productionScheduleEnabled` arm; the Release Candidate is disarmed even if merged
- recovery suppresses a second account task only when the matching primary already succeeded
- monthly scheduler heartbeat
- official GitHub Actions pinned to immutable commit SHAs
- complete V3 Python dependency closure pinned
- manual account workflow input uses privacy-safe lock id rather than persistent account key
- guarded atomic V2→V3 cutover and rollback workflows

## Stage B — automated implementation complete

The desktop RC contains a formal Xcode/XcodeGen project with the application, embedded Safari Web Extension, Native Messaging companion host and XCTest targets; macOS Keychain credential storage; durable local history/state; direct GLaDOS read-only refresh independent of GitHub; fixed bundle identifier for in-place upgrade; and backup/test/rollback-aware installation.

RC5 additionally fixes the real crash diagnosed from three user `.ips` reports. SafariServices replies arrived on its private NSXPC reply queue and Swift 6 trapped in `_swift_task_checkIsolatedSwift` / `_dispatch_assert_queue_fail` before the Swift closure body could run. RC5 receives those SafariServices reply blocks in Objective-C, dispatches to the main queue, and only then invokes the `@MainActor` Swift facade. Status lookup and opening Safari extension preferences use the same bridge.

RC5 also makes local-history merge duplicate-safe and preserves corrupt local state as a diagnostic copy rather than trapping on startup.

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

RC5 cleanup additionally covers `/Volumes/*/UserData/Downloads`, scoped GLaDOS Xcode DerivedData, and stale LaunchServices registrations. Candidate apps are revalidated by known bundle identifier before removal; current V3 data, Keychain and ordinary ZIP files are not part of cleanup.

## Stage E — machine gates passed

Machine gates include Python V3/unit/integration/flow tests, V2 regression tests, mock GLaDOS/GitHub tests, Secret/PII leak scans, immutable Action/dependency policy checks, Swift 6 strict-concurrency semantic checks, browser/Safari/Native Messaging JS tests, package structure/privacy/installer/rollback invariants, GET-only account Canary, and a real GitHub-hosted macOS/Xcode gate.

### RC5 authoritative macOS/Xcode gate

Successful workflow run: `31250096151`, job `93084986641`.

Environment: macOS 26.5.2 arm64, Xcode 26.6 (17F113), Swift 6.3.3, XcodeGen 2.46.0.

The gate verified:

- 25 final Xcode input files byte-match the RC5 delivery source by SHA-256
- XcodeGen does not rewrite the shipping `Info.plist` files
- `xcodebuild test`: 11 tests, 0 failures, `TEST SUCCEEDED`
- SafariServices XPC/Objective-C bridge regression test passes without SIGTRAP
- Release build succeeds with ad-hoc signing
- embedded Safari extension exists
- `codesign --verify --deep --strict` passes
- main bundle ID is `com.enoch.glados-account-center`
- built `CFBundleVersion` is `30005`
- built `CFBundleShortVersionString` is `3.0.0`
- Release application stays alive for 15 seconds with no new DiagnosticReports (`RC5_STARTUP_SMOKE_OK`)

The final packaged RC5 additionally passed 122/122 local static release checks, Swift 6 semantic acceptance, 25/25 browser/Safari/Native Messaging tests, SHA256SUMS verification after a fresh ZIP extraction, and executable-bit checks for all `.command` scripts.

### Current real-account result

The managed account set matches production after replacing the retired `332A23567057FBF5` account with `69B9338D952FEE8D`. The five-slot GET-only Canary passes 5/5 and sends no check-in or exchange POSTs.

## Remaining human-only release gates

1. install RC5 on the user's actual Mac and confirm V2→V3 in-place upgrade with no startup crash
2. run the scoped old-version cleanup and confirm the real external Downloads / DerivedData / stale LaunchServices results
3. enable/confirm the embedded Safari extension where needed
4. five-account Keychain health 5/5
5. local GLaDOS direct read-only refresh 5/5 and visible-data sanity check
6. one controlled RC primary + recovery live Canary with no duplicate side effect
7. merge Draft PR while V3 production schedule remains disarmed
8. verify one manual V3 primary on `master`
9. retire V2 schedule and arm V3 schedule through the guarded atomic cutover

Until those pass, PR #1 remains Draft and production `master` remains V2. A PR merge by itself cannot activate V3 scheduled sign-in while the production arm is false.
