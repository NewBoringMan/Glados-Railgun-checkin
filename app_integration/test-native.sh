#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NATIVE_TEST_DIR="$(mktemp -d "${TMPDIR:-/private/tmp}/glados-manual-native.XXXXXX")"
cleanup_native_tests() {
    /bin/rm -rf "$NATIVE_TEST_DIR"
}
trap cleanup_native_tests EXIT

NATIVE_TEST_ARCH="$(uname -m)"
/usr/bin/xcrun swiftc -parse-as-library \
    -target "${NATIVE_TEST_ARCH}-apple-macos13.0" \
    -module-cache-path "$NATIVE_TEST_DIR/ModuleCache" \
    -framework Foundation -framework Security -framework CryptoKit -lsqlite3 \
    "$ROOT/macos/Sources/ManualAccountStore.swift" \
    "$ROOT/macos/Sources/WorkflowReceipts.swift" \
    "$ROOT/macos/Sources/WorkflowRunClient.swift" \
    "$ROOT/macos/Tests/manual_accounts_native.swift" \
    "$ROOT/macos/Tests/workflow_receipts_native.swift" \
    "$ROOT/macos/Tests/workflow_run_client_native.swift" \
    -o "$NATIVE_TEST_DIR/manual-accounts-tests"

"$NATIVE_TEST_DIR/manual-accounts-tests" "$NATIVE_TEST_DIR/isolated-fixtures"
