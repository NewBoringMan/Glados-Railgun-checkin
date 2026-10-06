import Foundation
import SQLite3

final class FixtureVault: ManualSessionVault, @unchecked Sendable {
    var items: [String: Data] = [:]
    var legacy: [String: Data] = [:]
    var additions = 0
    var failAt: Int?
    func read(_ id: String) throws -> Data? { items[id] }
    func add(_ data: Data, id: String) throws {
        additions += 1
        if additions == failAt { throw ManualAccountError.storage }
        guard items[id] == nil else { throw ManualAccountError.conflict }
        items[id] = data
    }
    func remove(_ id: String) throws { items.removeValue(forKey: id) }
    func readLegacy(key: String, kind: String) throws -> Data? { legacy[kind + "-" + key] }
}

enum NativeTestFailure: Error { case failed(String) }

@main
struct ManualAccountsNativeTests {
    static let firstKey = "AAAAAAAAAAAAAAAA"
    static let secondKey = "BBBBBBBBBBBBBBBB"
    static let thirdKey = "CCCCCCCCCCCCCCCC"
    static var checks = 0

    static func expect(_ condition: @autoclosure () throws -> Bool, _ name: String) throws {
        guard try condition() else { throw NativeTestFailure.failed(name) }
        checks += 1
    }
    static func rejects(_ name: String, _ operation: () throws -> Void) throws {
        do { try operation() } catch { checks += 1; return }
        throw NativeTestFailure.failed(name)
    }
    static func session(_ key: String = firstKey, email: String = "first@example.test") -> ManualSession {
        ManualSession(accountKey: key, email: email,
                      cookieHeader: "koa:sess=fixture-session; koa:sess.sig=fixture-signature; gld:sess=fixture-device; gld:sess.sig=fixture-device-signature; locale=en",
                      host: "glados.cloud", userAgent: "FixtureBrowser/1.0", browser: "fixture", capturedAt: "2024-01-01T00:00:00Z")
    }
    static func account(_ session: ManualSession) -> ManualArchiveAccount {
        ManualArchiveAccount(accountKey: session.accountKey, email: session.email, emailVerified: true,
                             label: "GLaDOS fixture", enabled: true, autoExchange: false, session: session)
    }

    static func main() throws {
        guard CommandLine.arguments.count == 2 else { throw NativeTestFailure.failed("isolated directory argument required") }
        let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        guard !FileManager.default.fileExists(atPath: root.path) else { throw NativeTestFailure.failed("test directory must be new") }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: root) }
        let vault = FixtureVault()
        let directory = root.appendingPathComponent("ManualAccounts")
        let store = try LocalManualAccountStore(directory: directory, vault: vault)
        let first = session()

        try expect(first.validated() == first, "preserve full browser session")
        let wire = try JSONSerialization.jsonObject(with: first.encoded()) as! [String: Any]
        try expect(Set(wire.keys) == Set(["schema", "version", "accountKey", "email", "cookieHeader", "host", "userAgent", "browser", "capturedAt"]), "exact cloud wire fields")
        try expect(wire["cookieHeader"] as? String == first.cookieHeader, "all cookie fields preserved")
        var bad = first; bad.host = "sub.glados.cloud"
        try rejects("reject unapproved subdomain") { _ = try bad.validated() }
        bad = first; bad.userAgent = "Fixture\r\nCookie: leaked"
        try rejects("reject header injection") { _ = try bad.validated() }
        bad = first; bad.cookieHeader = "koa:sess=x; koa:sess.sig=y; gld:sess=z"
        try rejects("reject partial session pair") { _ = try bad.validated() }
        bad = first; bad.cookieHeader += "; locale=zh"
        try rejects("reject duplicate cookies") { _ = try bad.validated() }
        var compatible = first; compatible.cookieHeader = "gld:sess=x; gld:sess.sig=y; koa:sess=old; locale="
        try expect(compatible.validated().cookieHeader == compatible.cookieHeader, "complete gld accepts residual koa half-pair and empty auxiliary value")
        bad = first; bad.cookieHeader = "gld:sess=; gld:sess.sig=y; koa:sess=x; koa:sess.sig=z"
        try rejects("empty current session cannot fall back to legacy pair") { _ = try bad.validated() }
        bad = first; bad.cookieHeader = "koa:sess=x; locale="
        try rejects("legacy-only session still requires both nonempty values") { _ = try bad.validated() }

        try store.saveCapture(first, label: "GLaDOS fixture", enabled: true, autoExchange: false, pendingPublication: true)
        try expect(store.session(for: firstKey) == first, "manual capture locally recoverable")
        try expect(store.records()[firstKey]?.allowsManualCredentialReplacement == true, "explicit capture may replace the same registered account")
        let metadataURL = directory.appendingPathComponent("accounts.json")
        let before = try Data(contentsOf: metadataURL)
        try expect(!String(decoding: before, as: UTF8.self).contains("fixture-session"), "metadata has no cookie")
        let permissions = try FileManager.default.attributesOfItem(atPath: metadataURL.path)[.posixPermissions] as? NSNumber
        try expect(permissions?.intValue == 0o600, "owner-only metadata")
        let reopened = try LocalManualAccountStore(directory: directory, vault: vault)
        try expect(reopened.records()[firstKey]?.email == first.email, "email persists across reopen")
        try expect(reopened.records()[firstKey]?.sessionHost == first.host, "verified host persists for subsequent explicit browser actions")
        try rejects("blank failure cannot clear identity") { try store.rememberEmail(key: firstKey, email: "", verified: false) }
        bad = first; bad.email = "wrong@example.test"
        try rejects("capture cannot replace another email") { try store.saveCapture(bad, label: "fixture", enabled: true, autoExchange: true, pendingPublication: true) }
        try expect(Data(contentsOf: metadataURL) == before, "failed identity check has no metadata write")

        var changed = first; changed.cookieHeader = first.cookieHeader.replacingOccurrences(of: "fixture-session", with: "new-session")
        let duplicatePlan = try ManualImportPlan.make(ManualArchiveContents(accounts: [account(changed)]), existing: [firstKey: first.email])
        try expect(duplicatePlan.additions.isEmpty && duplicatePlan.skipped == 1, "existing key skipped even with newer cookie")
        let second = session(secondKey, email: "second@example.test")
        let third = session(thirdKey, email: "third@example.test")
        let distinct = ManualArchiveContents(accounts: [account(second), account(third)])
        let plan = try ManualImportPlan.make(distinct, existing: [firstKey: first.email])
        let secretOnly = try ManualImportPlan.make(ManualArchiveContents(accounts: [account(second)]), existing: [:], additionalKeys: [secondKey])
        try expect(secretOnly.additions.isEmpty && secretOnly.skipped == 1, "secret-only cloud records are existing accounts even without workflow config")
        vault.failAt = vault.additions + 2
        try rejects("batch storage failure") { _ = try store.applyImport(plan, externalKeys: []) }
        try expect(Data(contentsOf: metadataURL) == before, "failed batch preserves every old record")
        try expect(vault.items.count == 1 && store.session(for: firstKey) == first, "failed batch rolls back staged secrets")
        vault.failAt = nil
        let applied = try store.applyImport(plan, externalKeys: [])
        try expect(applied.count == 2 && store.records().count == 3, "only new accounts added")
        try expect(store.records()[secondKey]?.credentialSource == "import" && store.records()[secondKey]?.allowsManualCredentialReplacement == false, "import never authorizes replacement of existing cloud secrets")
        try store.markPublicationSkipped([secondKey])
        try expect(store.records()[secondKey]?.pendingPublication == false && store.records()[secondKey]?.publicationSkipped == true, "concurrent cloud registration disables imported credential retry")
        var legacyRecord = ManualAccountRecord(accountKey: thirdKey, label: "fixture")
        legacyRecord.pendingPublication = true
        let sourceLess = try JSONDecoder().decode(ManualAccountRecord.self, from: JSONEncoder().encode(legacyRecord))
        try expect(!sourceLess.allowsManualCredentialReplacement, "old source-less metadata remains readable and never grants replacement permission")
        try expect(store.session(for: firstKey) == first, "import leaves original credential untouched")
        let conflicted = ManualArchiveContents(accounts: [account(session("DDDDDDDDDDDDDDDD", email: first.email))])
        try rejects("reject cross-account email conflicts") { _ = try ManualImportPlan.make(conflicted, existing: [firstKey: first.email]) }
        let repeated = try ManualImportPlan.make(ManualArchiveContents(accounts: [account(second), account(second)]), existing: [:])
        try expect(repeated.additions.count == 1 && repeated.skipped == 1, "identical rows deduplicate")
        var conflicting = second; conflicting.cookieHeader += "; other=1"
        try rejects("conflicting new rows fail whole file") { _ = try ManualImportPlan.make(ManualArchiveContents(accounts: [account(second), account(conflicting)]), existing: [:]) }

        let oldJSON = try JSONSerialization.data(withJSONObject: ["key": firstKey, "email": first.email, "cookie": "koa:sess=legacy; koa:sess.sig=legacy-sig;", "generation": "fixture-generation", "verified_at": 1_700_000_000.0])
        vault.legacy["active-" + firstKey] = oldJSON
        let contents = try store.archive()
        try expect(contents.accounts.first(where: { $0.accountKey == firstKey })?.legacySessions?.first?.rawJSON == oldJSON, "old keychain record included without inventing UA")
        try expect(vault.legacy["active-" + firstKey] == oldJSON, "old keychain source left intact")

        let password = "fixture-password-2026"
        try rejects("short multibyte password is not twelve characters") { _ = try ManualArchiveCrypto.encrypt(contents, password: "密码四字") }
        let encrypted = try ManualArchiveCrypto.encrypt(contents, password: password)
        let decoded = try ManualArchiveCrypto.decrypt(encrypted, password: password)
        try expect(decoded.accounts == contents.accounts, "encrypted backup lossless roundtrip")
        let outerText = String(decoding: encrypted, as: UTF8.self)
        try expect(!outerText.contains(first.email) && !outerText.contains("fixture-session") && !outerText.contains("legacy-sig"), "encrypted file exposes no account contents")
        try rejects("wrong password rejected") { _ = try ManualArchiveCrypto.decrypt(encrypted, password: "wrong-password-2026") }
        var envelope = try JSONSerialization.jsonObject(with: encrypted) as! [String: Any]
        var sealed = Data(base64Encoded: envelope["sealed"] as! String)!
        sealed[sealed.count - 1] ^= 1; envelope["sealed"] = sealed.base64EncodedString()
        let tampered = try JSONSerialization.data(withJSONObject: envelope)
        try rejects("ciphertext tamper rejected") { _ = try ManualArchiveCrypto.decrypt(tampered, password: password) }
        envelope["iterations"] = 1
        let weak = try JSONSerialization.data(withJSONObject: envelope)
        try rejects("KDF downgrade rejected") { _ = try ManualArchiveCrypto.decrypt(weak, password: password) }

        let targetVault = FixtureVault()
        let target = try LocalManualAccountStore(directory: root.appendingPathComponent("Imported"), vault: targetVault)
        let restore = try ManualImportPlan.make(decoded, existing: [:])
        _ = try target.applyImport(restore, externalKeys: [])
        let roundtrip = try target.archive()
        try expect(roundtrip.accounts == contents.accounts, "portable backup restores sessions and legacy archive")
        let metadataOnly = ManualArchiveAccount(accountKey: "EEEEEEEEEEEEEEEE", email: "pending@example.test", emailVerified: false, label: "fixture", enabled: true, autoExchange: false, session: nil)
        let metadataPlan = try ManualImportPlan.make(ManualArchiveContents(accounts: [metadataOnly]), existing: [:])
        _ = try target.applyImport(metadataPlan, externalKeys: [])
        try expect(target.records()[metadataOnly.accountKey]?.visible == true && target.session(for: metadataOnly.accountKey) == nil, "metadata-only accounts visible but not runnable")

        let legacyDB = root.appendingPathComponent("login-refresh.sqlite")
        var database: OpaquePointer?
        guard sqlite3_open(legacyDB.path, &database) == SQLITE_OK else { throw NativeTestFailure.failed("fixture sqlite open") }
        let sql = "CREATE TABLE identity_pending(account_key TEXT,email TEXT); CREATE TABLE identity(account_key TEXT,email TEXT); INSERT INTO identity_pending VALUES('FFFFFFFFFFFFFFFF','pending-legacy@example.test'); INSERT INTO identity VALUES('FFFFFFFFFFFFFFFF','confirmed-legacy@example.test');"
        guard sqlite3_exec(database, sql, nil, nil, nil) == SQLITE_OK else { throw NativeTestFailure.failed("fixture sqlite schema") }
        sqlite3_close(database)
        let legacyBefore = try Data(contentsOf: legacyDB)
        try target.migrateLegacyEmails(at: legacyDB)
        try expect(target.records()["FFFFFFFFFFFFFFFF"]?.email == "confirmed-legacy@example.test", "confirmed legacy identity wins")
        try expect(Data(contentsOf: legacyDB) == legacyBefore, "legacy sqlite remains byte-identical")
        try expect(target.session(for: "FFFFFFFFFFFFFFFF") == nil, "email migration does not manufacture credentials")
        print("PASS: \(checks) isolated native account-store checks; no real Keychain, network, or user directories accessed.")
    }
}
