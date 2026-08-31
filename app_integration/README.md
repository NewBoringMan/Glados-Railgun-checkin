# GLaDOS Account Center V2.0.7 integration

This directory contains the maintainable source for the V2.0.7 per-account exchange-policy integration.

## Architecture

- `GLaDOSAccountCenter.real`: the proven V2.0.6 native SwiftUI binary, kept unchanged.
- `launcher.c`: a minimal launcher that injects `PolicyMenuPlugin.dylib` and then executes the original binary.
- `PolicyMenuPlugin.m`: adds `账号兑换方案…` to the Account Center app menu and opens the embedded editor.
- `PolicyEditor.swift`: SwiftUI editor for per-account exchange policies.
- `PolicyEditor-Info.plist`: embedded helper bundle metadata.
- `build-v207.sh`: reproducibly builds/signs V2.0.7 from a trusted Account Center app base.

## Single source of truth

Per-account exchange policy is stored in one non-sensitive GitHub file:

`.github/glados/account_policies.json`

Shape:

```json
{"version":1,"default":"auto","accounts":{"ACCOUNT_KEY":"plan200"}}
```

`auto` means the existing smart best-plan policy. Fixed values must reference a verified plan in `.github/glados/exchange_plans.json`.

The editor reads `.github/glados/accounts.json` for account key/label/enabled/autoExchange, `.github/glados/exchange_plans.json` for verified choices, and `.github/glados/account_policies.json` for the current per-account selection. It never reads GitHub Secret values or GLaDOS cookie contents.

Saving updates only `account_policies.json` on `master` through the GitHub Contents API, using the file SHA observed when the editor loaded for optimistic concurrency. If another device changes the file first, GitHub rejects the stale save instead of letting it overwrite newer policy. After a successful write, the editor reads the file back and verifies the exact decoded policy before reporting success. The small Git commit produced by each real policy change is intentional: it gives rollback/history and avoids coupling policy delivery to the Account Center workflow generator.

## Safety / compatibility

- Missing policy file or missing account mapping → `auto`.
- Malformed JSON / missing or unverified fixed plan → backend falls back to smart best plan and records a warning.
- `autoExchange=false` continues to disable exchange regardless of selected plan.
- `checkin.py` reads the policy file directly from the checked-out repository, so Account Center can regenerate `gladosAccounts.yml` later without erasing policy delivery.
- CI validates policy-file structure and fixed-plan validity. A stale mapping from a later deleted account is harmless and is pruned the next time the policy editor saves.
- V2.0.6 business/UI logic remains in the original native binary instead of being reimplemented.

## Build

```bash
./app_integration/build-v207.sh \
  "/path/to/trusted/GLaDOS Account Center.app" \
  "/path/to/output/GLaDOS Account Center.app"
```

The build is arm64, requires the macOS/Xcode command-line toolchain, signs the helper/plugin/parent bundle ad hoc, and performs `codesign --verify --deep --strict` before reporting success.
