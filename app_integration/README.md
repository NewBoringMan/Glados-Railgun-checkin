# GLaDOS Account Center V2.0.9 single-app integration

V2.0.9 keeps the proven V2.0.6/V2.0.7/V2.0.8 Account Center business core intact, keeps the single-app architecture, and adds a visible exchange-policy entry in the main window title bar. The installed product must be exactly one macOS application: `GLaDOS Account Center.app`.

## Architecture

- `GLaDOSAccountCenter.real`: the proven native SwiftUI Account Center core. Existing account management, status, calendar, run history, GitHub management and add-account flow remain unchanged.
- `launcher.c`: a minimal launcher that injects `PolicyMenuPlugin.dylib` and executes the proven native core.
- `PolicyMenuPlugin.m`: adds a visible `兑换方案` button to the Account Center main window title bar, keeps `账号兑换方案…` in the app menu as a fallback, and loads `GLaDOSPolicyEditor.dylib` in the same process with `dlopen`/`dlsym`.
- `PolicyEditor.swift`: an in-process SwiftUI `NSWindow`. It is not an app, creates no helper process, and reads/writes only `.github/glados/account_policies.json` through GitHub CLI/API. Cookies and Secret values are never read.
- `SafariExtensionSource/`: maintainable Safari Web Extension source/resources and native handler. `build-v209.sh` uses Xcode's `safari-web-extension-converter` plus `xcodebuild` to produce a standards-compliant `com.apple.Safari.web-extension` `.appex`, then embeds only that extension at `GLaDOS Account Center.app/Contents/PlugIns/`; no standalone `GLaDOS Safari Bridge.app` is required.
- V2.0.9 Build 20010 removes the last legacy Safari helper/setup-directory fallback. Safari setup now opens Safari directly and refers only to the extension embedded in Account Center.
- `build-v209.sh`: builds/signs the true single-app bundle, rebuilds the Safari extension from source, and refuses output if any nested `.app` remains.

## Account exchange policy

GitHub remains the single source of truth:

`.github/glados/account_policies.json`

Supported choices are generated from the verified exchange catalog. Current verified choices include smart/auto, `100 -> 10 days`, `200 -> 30 days`, and `500 -> 100 days`. Missing/empty account policies safely default to smart/auto. Per-account policy remains independent from the generated `gladosAccounts.yml` workflow.

Saving uses the GitHub Contents API with the file SHA observed at load time. A stale editor cannot silently overwrite a newer change from another computer. Fixed mappings for deleted accounts are pruned on the next successful editor save.

## Single-app rules

1. Never ship `Contents/Applications/*.app`.
2. Do not create a standalone exchange-policy app or Safari bridge app.
3. Helper implementation code may be a dylib/framework or `.appex`, because macOS does not expose those as separate user apps.
4. Build/test copies go under `/private/tmp` and are deleted after validation; do not keep `.app` backups in Spotlight-indexed workspaces.
5. Rollback copies are compressed ZIP archives, not `.app` directories.
6. Future V2.0.9+ work should upgrade this one Account Center bundle and preserve existing business/data contracts unless an explicit migration is required.

## Build

```bash
./app_integration/build-v209.sh \
  '/Users/enoch/Applications/GLaDOS Account Center.app' \
  '/private/tmp/GLaDOS-Account-Center-v2.0.9-final.app'
```

The build script verifies signing, rebuilds the Safari Web Extension from source with a macOS 12 deployment target, validates its extension point/bundle identifier, requires the embedded `.appex`, and fails if any nested `.app` exists. Xcode command-line tools are therefore a build-time dependency only; the installed product remains one Account Center app.

## Data that must never be removed during upgrades

- `~/Library/Application Support/GLaDOS Account Center/BrowserProfiles`
- Cookies/login state
- GitHub Secrets
- `.github/glados/accounts.json`
- `.github/glados/account_policies.json`
- production workflows/schedules and account history

V2.0.9 changes packaging/integration visibility only; check-in, points, status fallback and exchange execution logic remain the validated production logic.
