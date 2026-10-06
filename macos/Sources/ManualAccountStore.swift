import Foundation
import Security
import CryptoKit
import CommonCrypto
import SQLite3
import Darwin

enum ManualAccountError: LocalizedError {
    case invalid(String)
    case storage
    case keychain(OSStatus)
    case password
    case conflict
    case remoteSessionFormat
    var errorDescription: String? {
        switch self {
        case .invalid(let reason): return reason
        case .storage: return "本机账号资料无法安全读取或保存；原有资料未被替换。"
        case .keychain: return "无法访问本机钥匙串；请解锁这台 Mac 后重试。登录资料未发送到 GitHub。"
        case .password: return "密码错误、文件损坏或文件格式不受支持；未导入任何账号。"
        case .conflict: return "账号身份发生冲突，已停止保存；原有账号没有被覆盖。"
        case .remoteSessionFormat: return "无法确认生产 master 支持当前登录资料格式，已停止同步。当前账号的本机登录资料已保留，此次凭据未写入 GitHub Secret。请确认云端部署和 GitHub 连接后，点击“同步本机资料”重试。"
        }
    }
}

/// The public capability marker must be deployed alongside the matching Python
/// readers on production master before any structured Secret is published.
enum ManualSessionFormatGate {
    static let markerPath = ".github/glados/session-format.json"
    private static let schema = "glados.manual-session"
    private struct Header: Decodable { var schema: String; var version: Int }

    static func requiresRemoteSupport(_ value: String) throws -> Bool {
        let data = Data(value.utf8)
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              object["schema"] as? String == schema else { return false }
        guard let header = try? JSONDecoder().decode(Header.self, from: data), header.version == 1 else {
            throw ManualAccountError.remoteSessionFormat
        }
        return true
    }

    static func validateMarker(_ data: Data) throws {
        guard data.count <= 4096,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == Set(["schema", "version"]),
              let header = try? JSONDecoder().decode(Header.self, from: data),
              header.schema == schema, header.version == 1 else {
            throw ManualAccountError.remoteSessionFormat
        }
    }
}

enum ManualValidation {
    static let allowedHosts: Set<String> = ["glados.cloud", "glados.network", "glados.rocks", "glados.one", "glados.space", "glados.vip", "glados-facility.com", "railgun.info"]
    static func key(_ value: String) throws -> String {
        let key = value.uppercased()
        guard key.range(of: "^[A-F0-9]{16}$", options: .regularExpression) != nil else {
            throw ManualAccountError.invalid("账号标识格式无效。")
        }
        return key
    }
    static func email(_ value: String) throws -> String {
        let clean = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard clean.utf8.count <= 254,
              clean.range(of: "^[^\\s<>@,;]+@[^\\s<>@,;]+\\.[^\\s<>@,;]+$", options: .regularExpression) != nil,
              !clean.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
            throw ManualAccountError.invalid("请输入完整、有效的账号邮箱。")
        }
        return clean
    }
    static func date(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: value) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: value)
    }
    static func timestamp() -> String { ISO8601DateFormatter().string(from: Date()) }
    static func safeLabel(_ label: String, key: String) -> String {
        let clean = label.trimmingCharacters(in: .whitespacesAndNewlines)
        return clean.isEmpty || clean.count > 80 || clean.contains("@") || clean.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) })
            ? "GLaDOS \(key.prefix(6))" : clean
    }
}

/// This value is written only after an explicit capture/import/sync action. No network,
/// login, cookie renewal or browser operation exists in this module.
struct ManualSession: Codable, Equatable, Sendable {
    var schema = "glados.manual-session"
    var version = 1
    var accountKey: String
    var email: String
    var cookieHeader: String
    var host: String
    var userAgent: String
    var browser: String
    var capturedAt: String

    func validated() throws -> ManualSession {
        guard schema == "glados.manual-session", version == 1 else {
            throw ManualAccountError.invalid("不支持此版本的登录资料。")
        }
        var value = self
        value.accountKey = try ManualValidation.key(accountKey)
        value.email = try ManualValidation.email(email)
        value.host = host.lowercased()
        guard ManualValidation.allowedHosts.contains(value.host),
              value.host.range(of: "^[a-z0-9.-]+$", options: .regularExpression) != nil,
              !userAgent.isEmpty, userAgent.utf8.count <= 2048,
              !userAgent.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
              !browser.isEmpty, browser.utf8.count <= 100,
              !browser.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
              let captured = ManualValidation.date(capturedAt), captured <= Date().addingTimeInterval(300),
              !cookieHeader.isEmpty, cookieHeader.utf8.count <= 32768,
              !cookieHeader.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }) else {
            throw ManualAccountError.invalid("登录资料缺少完整浏览器上下文，或字段格式无效。请手动重新读取该账号。")
        }
        var cookies: [String: String] = [:]
        for raw in cookieHeader.split(separator: ";", omittingEmptySubsequences: true) {
            let part = raw.trimmingCharacters(in: .whitespaces)
            guard let equal = part.firstIndex(of: "=") else { throw ManualAccountError.invalid("Cookie 格式无效。") }
            let name = String(part[..<equal])
            let content = String(part[part.index(after: equal)...])
            guard name.range(of: "^[!#$%&'*+.^_`|~0-9A-Za-z:-]+$", options: .regularExpression) != nil,
                  cookies[name] == nil else {
                throw ManualAccountError.invalid("Cookie 字段缺失或重复。")
            }
            cookies[name] = content
        }
        let hasGld = cookies["gld:sess"] != nil || cookies["gld:sess.sig"] != nil
        let pair = hasGld ? ["gld:sess", "gld:sess.sig"] : ["koa:sess", "koa:sess.sig"]
        // The current gld pair can coexist with an expired, partial legacy koa
        // pair. Browser auxiliary cookies may legally have an empty value.
        guard pair.allSatisfy({ cookies[$0]?.isEmpty == false }) else {
            throw ManualAccountError.invalid("登录 Cookie 不完整。请通过正常网页登录后重新读取。")
        }
        return value
    }

    func encoded() throws -> Data {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(validated())
    }
}

struct ManualAccountRecord: Codable, Equatable, Sendable {
    var accountKey: String
    var email: String = ""
    var emailVerified: Bool = false
    var label: String
    var enabled: Bool = true
    var autoExchange: Bool = false
    var visible: Bool = true
    var credentialID: String?
    var previousCredentialID: String?
    var legacyCredentialIDs: [String]?
    var pendingPublication: Bool = false
    // Optional fields keep v1 local metadata readable. Only a fresh, explicit
    // manual capture permits replacing an already registered cloud credential.
    var credentialSource: String?
    var publicationSkipped: Bool?
    var sessionHost: String?
    var updatedAt: String = ManualValidation.timestamp()
    var hasCredentials: Bool { credentialID != nil }
    var allowsManualCredentialReplacement: Bool {
        credentialSource == "manual-capture" && pendingPublication && publicationSkipped != true
    }
}

struct ManualArchiveAccount: Codable, Equatable, Sendable {
    var accountKey: String
    var email: String
    var emailVerified: Bool
    var label: String
    var enabled: Bool
    var autoExchange: Bool
    var session: ManualSession?
    var legacySessions: [LegacySessionMaterial]? = nil
    var previousSession: ManualSession? = nil

    func validated() throws -> ManualArchiveAccount {
        var value = self
        value.accountKey = try ManualValidation.key(accountKey)
        value.email = email.isEmpty ? "" : try ManualValidation.email(email)
        value.label = ManualValidation.safeLabel(label, key: value.accountKey)
        if let session {
            let valid = try session.validated()
            guard valid.accountKey == value.accountKey, valid.email == value.email else { throw ManualAccountError.conflict }
            value.session = valid
        } else if emailVerified && value.email.isEmpty { throw ManualAccountError.conflict }
        if let legacySessions {
            guard legacySessions.count <= 8 else { throw ManualAccountError.invalid("旧登录资料数量无效。") }
            for material in legacySessions { try material.validate(key: value.accountKey, email: value.email) }
        }
        if let previousSession {
            let previous = try previousSession.validated()
            guard previous.accountKey == value.accountKey, previous.email == value.email else { throw ManualAccountError.conflict }
            value.previousSession = previous
        }
        return value
    }
}

/// Original v2.0.10 runner records contain email/key/cookie/generation/verified_at,
/// without a captured host or user agent. Keep their exact bytes as archival data;
/// never upgrade them to a runnable ManualSession or send them to the cloud.
struct LegacySessionMaterial: Codable, Equatable, Sendable {
    var kind: String
    var rawJSON: Data
    func validate(key: String, email: String) throws {
        guard ["active", "candidate"].contains(kind), rawJSON.count <= 48 * 1024,
              let object = try JSONSerialization.jsonObject(with: rawJSON) as? [String: Any],
              let storedKey = object["key"] as? String, try ManualValidation.key(storedKey) == key,
              let storedEmail = object["email"] as? String,
              let cookie = object["cookie"] as? String, !cookie.isEmpty, cookie.utf8.count <= 32768 else {
            throw ManualAccountError.invalid("旧登录资料格式异常，未丢弃或替换原记录。")
        }
        let clean = try ManualValidation.email(storedEmail)
        if !email.isEmpty, clean != email { throw ManualAccountError.conflict }
    }
}

struct ManualArchiveContents: Codable, Sendable {
    var schema = "glados.account-backup.contents"
    var version = 1
    var createdAt = ManualValidation.timestamp()
    var accounts: [ManualArchiveAccount]
}

struct ManualImportPlan: Sendable {
    var additions: [ManualArchiveAccount]
    var skipped: Int
    var metadataOnly: Int { additions.filter { $0.session == nil }.count }

    static func make(_ contents: ManualArchiveContents, existing: [String: String], additionalKeys: Set<String> = []) throws -> ManualImportPlan {
        guard contents.schema == "glados.account-backup.contents", contents.version == 1,
              ManualValidation.date(contents.createdAt) != nil, !contents.accounts.isEmpty,
              contents.accounts.count <= 1000 else { throw ManualAccountError.invalid("账号备份结构无效或账号数量超出限制。") }
        // Validate the whole file before deriving a write plan, including skipped rows.
        let validated = try contents.accounts.map { try $0.validated() }
        let existingKeys = Set(existing.keys).union(additionalKeys)
        var seen: [String: ManualArchiveAccount] = [:]
        var emails = existing.reduce(into: [String: String]()) { result, item in
            if !item.value.isEmpty { result[item.value.lowercased()] = item.key }
        }
        var skipped = 0
        for account in validated {
            if existingKeys.contains(account.accountKey) { skipped += 1; continue }
            if let prior = seen[account.accountKey] {
                guard prior == account else { throw ManualAccountError.conflict }
                skipped += 1; continue
            }
            if !account.email.isEmpty {
                if let other = emails[account.email], other != account.accountKey { throw ManualAccountError.conflict }
                emails[account.email] = account.accountKey
            }
            seen[account.accountKey] = account
        }
        return ManualImportPlan(additions: seen.values.sorted { $0.accountKey < $1.accountKey }, skipped: skipped)
    }
}

protocol ManualSessionVault: AnyObject, Sendable {
    func read(_ id: String) throws -> Data?
    func add(_ data: Data, id: String) throws
    func remove(_ id: String) throws
    func readLegacy(key: String, kind: String) throws -> Data?
}

private final class LegacyReadBuffer: @unchecked Sendable {
    private let lock = NSLock()
    private var data = Data()
    private var invalid = false
    func append(_ bytes: Data) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard data.count + bytes.count <= 65536 else { invalid = true; return false }
        data.append(bytes); return true
    }
    func result() -> Data? { lock.lock(); defer { lock.unlock() }; return invalid ? nil : data }
}

final class NativeManualSessionVault: ManualSessionVault, @unchecked Sendable {
    static let service = "com.enoch.glados-account-center.manual-sessions"
    private func query(_ id: String) throws -> [String: Any] {
        guard id.range(of: "^[A-F0-9]{16}\\.[a-f0-9-]{36}$", options: .regularExpression) != nil else { throw ManualAccountError.storage }
        return [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: Self.service,
                kSecAttrAccount as String: id, kSecAttrSynchronizable as String: false,
                kSecUseAuthenticationUI as String: kSecUseAuthenticationUIFail]
    }
    func read(_ id: String) throws -> Data? {
        var query = try query(id); query[kSecReturnData as String] = true; query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?; let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data, data.count <= 128 * 1024 else { throw ManualAccountError.keychain(status) }
        return data
    }
    func add(_ data: Data, id: String) throws {
        guard !data.isEmpty, data.count <= 128 * 1024 else { throw ManualAccountError.storage }
        var query = try query(id); query[kSecValueData as String] = data
        query[kSecAttrLabel as String] = "GLaDOS Account Center 手动登录资料"
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else { throw ManualAccountError.keychain(status) }
    }
    func remove(_ id: String) throws {
        let status = SecItemDelete(try query(id) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw ManualAccountError.keychain(status) }
    }
    func readLegacy(key: String, kind: String) throws -> Data? {
        let key = try ManualValidation.key(key)
        guard ["active", "candidate"].contains(kind) else { throw ManualAccountError.storage }
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.enoch.glados-account-center.login-refresh",
            kSecAttrAccount as String: kind + "-" + key, kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne, kSecUseAuthenticationUI as String: kSecUseAuthenticationUIFail]
        var result: CFTypeRef?; let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        if status != errSecSuccess {
            // Old items may trust only the original signed helper. It is invoked
            // solely by explicit export, with its documented read-only get request.
            if let helper = retainedLegacyHelper() { return try readLegacyUsingHelper(helper, key: kind + "-" + key) }
            throw ManualAccountError.keychain(status)
        }
        guard let data = result as? Data, data.count <= 48 * 1024 else { throw ManualAccountError.storage }
        return data
    }
    private func retainedLegacyHelper() -> URL? {
        guard Bundle.main.bundleURL.pathExtension == "app" else { return nil }
        let helper = Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/RefreshSecretStore")
        guard FileManager.default.isExecutableFile(atPath: helper.path),
              (try? helper.resourceValues(forKeys: [.isSymbolicLinkKey]))?.isSymbolicLink != true else { return nil }
        return helper
    }
    private func readLegacyUsingHelper(_ helper: URL, key: String) throws -> Data? {
        let process = Process(); process.executableURL = helper
        let input = Pipe(), output = Pipe()
        process.standardInput = input; process.standardOutput = output; process.standardError = FileHandle.nullDevice
        let exited = DispatchSemaphore(value: 0), read = DispatchSemaphore(value: 0)
        let buffer = LegacyReadBuffer()
        process.terminationHandler = { _ in exited.signal() }
        try process.run()
        DispatchQueue.global(qos: .userInitiated).async {
            defer { read.signal() }
            while let bytes = try? output.fileHandleForReading.read(upToCount: 8192), !bytes.isEmpty {
                if !buffer.append(bytes) { break }
            }
            try? output.fileHandleForReading.close()
        }
        try input.fileHandleForWriting.write(contentsOf: JSONSerialization.data(withJSONObject: ["op": "get", "key": key]))
        try input.fileHandleForWriting.close()
        let deadline = DispatchTime.now() + 15
        guard exited.wait(timeout: deadline) == .success, read.wait(timeout: deadline) == .success else {
            if process.isRunning { process.terminate() }
            throw ManualAccountError.storage
        }
        guard process.terminationStatus == 0, let data = buffer.result(),
              let response = try JSONSerialization.jsonObject(with: data) as? [String: Any], response["ok"] as? Bool == true else {
            throw ManualAccountError.invalid("旧登录资料仍受原钥匙串保护，暂时无法读取；原记录已保留，未导出不完整备份。")
        }
        if response["found"] as? Bool == false { return nil }
        guard let value = response["value"] as? [String: Any] else { throw ManualAccountError.storage }
        return try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    }
}

final class LocalManualAccountStore: @unchecked Sendable {
    struct Index: Codable { var version = 1; var accounts: [String: ManualAccountRecord] = [:] }
    let directory: URL
    private let vault: ManualSessionVault
    private let mutex = NSRecursiveLock()
    private let fileManager = FileManager.default
    private var indexURL: URL { directory.appendingPathComponent("accounts.json") }

    init(directory: URL? = nil, vault: ManualSessionVault = NativeManualSessionVault()) throws {
        self.directory = directory ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/GLaDOS Account Center/ManualAccounts", isDirectory: true)
        self.vault = vault
        try fileManager.createDirectory(at: self.directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        guard (try self.directory.resourceValues(forKeys: [.isSymbolicLinkKey])).isSymbolicLink != true else { throw ManualAccountError.storage }
        try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: self.directory.path)
        _ = try records()
    }

    private func locked<T>(_ operation: () throws -> T) throws -> T {
        mutex.lock(); defer { mutex.unlock() }
        let lockPath = directory.appendingPathComponent("accounts.lock").path
        let fd = Darwin.open(lockPath, O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, S_IRUSR | S_IWUSR)
        guard fd >= 0 else { throw ManualAccountError.storage }
        defer { Darwin.close(fd) }
        guard flock(fd, LOCK_EX) == 0 else { throw ManualAccountError.storage }
        defer { flock(fd, LOCK_UN) }
        return try operation()
    }
    private func load() throws -> Index {
        guard fileManager.fileExists(atPath: indexURL.path) else { return Index() }
        do {
            let values = try indexURL.resourceValues(forKeys: [.isSymbolicLinkKey, .fileSizeKey])
            guard values.isSymbolicLink != true, (values.fileSize ?? Int.max) <= 2 * 1024 * 1024 else { throw ManualAccountError.storage }
            let index = try JSONDecoder().decode(Index.self, from: Data(contentsOf: indexURL))
            guard index.version == 1, index.accounts.count <= 1000 else { throw ManualAccountError.storage }
            for (key, record) in index.accounts {
                guard try ManualValidation.key(key) == key, record.accountKey == key else { throw ManualAccountError.storage }
                if !record.email.isEmpty { _ = try ManualValidation.email(record.email) }
                if let source = record.credentialSource, !["manual-capture", "import"].contains(source) { throw ManualAccountError.storage }
                if let host = record.sessionHost, !ManualValidation.allowedHosts.contains(host) { throw ManualAccountError.storage }
                for id in [record.credentialID, record.previousCredentialID].compactMap({ $0 }) + (record.legacyCredentialIDs ?? []) {
                    guard id.hasPrefix(key + "."), id.range(of: "^[A-F0-9]{16}\\.[a-f0-9-]{36}$", options: .regularExpression) != nil else { throw ManualAccountError.storage }
                }
            }
            return index
        } catch { throw ManualAccountError.storage }
    }
    private func save(_ index: Index) throws {
        let temporary = directory.appendingPathComponent("accounts-\(UUID().uuidString).tmp")
        do {
            guard index.accounts.count <= 1000 else { throw ManualAccountError.storage }
            let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
            let data = try encoder.encode(index)
            guard data.count <= 2 * 1024 * 1024 else { throw ManualAccountError.storage }
            let fd = Darwin.open(temporary.path, O_CREAT | O_EXCL | O_WRONLY | O_NOFOLLOW | O_CLOEXEC, S_IRUSR | S_IWUSR)
            guard fd >= 0 else { throw ManualAccountError.storage }
            let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
            try handle.write(contentsOf: data); try handle.synchronize(); try handle.close()
            guard Darwin.rename(temporary.path, indexURL.path) == 0 else { throw ManualAccountError.storage }
        } catch {
            try? fileManager.removeItem(at: temporary)
            throw ManualAccountError.storage
        }
    }
    func records() throws -> [String: ManualAccountRecord] { try locked { try load().accounts } }
    func session(for key: String) throws -> ManualSession? {
        try locked {
            let key = try ManualValidation.key(key)
            guard let record = try load().accounts[key], let id = record.credentialID else { return nil }
            guard let data = try vault.read(id) else { throw ManualAccountError.storage }
            let session = try JSONDecoder().decode(ManualSession.self, from: data).validated()
            guard session.accountKey == key, session.email == record.email else { throw ManualAccountError.conflict }
            return session
        }
    }
    func rememberEmail(key raw: String, email rawEmail: String, verified: Bool, allowManualChange: Bool = false) throws {
        let key = try ManualValidation.key(raw); let email = try ManualValidation.email(rawEmail)
        try locked {
            var index = try load()
            if index.accounts.contains(where: { $0.key != key && $0.value.email == email }) { throw ManualAccountError.conflict }
            var record = index.accounts[key] ?? ManualAccountRecord(accountKey: key, label: "GLaDOS \(key.prefix(6))", visible: false)
            if !record.email.isEmpty, record.email != email {
                guard allowManualChange, !record.hasCredentials, !record.emailVerified else { throw ManualAccountError.conflict }
            }
            record.email = email; record.emailVerified = record.emailVerified || verified; record.updatedAt = ManualValidation.timestamp()
            index.accounts[key] = record; try save(index)
        }
    }
    func rememberSummaries(_ summaries: [ManualAccountRecord]) throws {
        try locked {
            var index = try load()
            for summary in summaries {
                let key = try ManualValidation.key(summary.accountKey)
                var record = index.accounts[key] ?? summary
                record.label = summary.label; record.enabled = summary.enabled; record.autoExchange = summary.autoExchange; record.visible = true
                index.accounts[key] = record
            }
            try save(index)
        }
    }
    func saveCapture(_ session: ManualSession, label: String, enabled: Bool, autoExchange: Bool, pendingPublication: Bool) throws {
        let valid = try session.validated()
        try locked {
            var index = try load()
            if index.accounts.contains(where: { $0.key != valid.accountKey && $0.value.email == valid.email }) { throw ManualAccountError.conflict }
            var record = index.accounts[valid.accountKey] ?? ManualAccountRecord(accountKey: valid.accountKey, label: label)
            if !record.email.isEmpty, record.email != valid.email { throw ManualAccountError.conflict }
            let newID = valid.accountKey + "." + UUID().uuidString.lowercased()
            try vault.add(valid.encoded(), id: newID)
            let obsolete = record.previousCredentialID
            record.previousCredentialID = record.credentialID; record.credentialID = newID
            record.email = valid.email; record.emailVerified = true; record.label = label
            record.enabled = enabled; record.autoExchange = autoExchange; record.visible = true
            record.pendingPublication = pendingPublication; record.updatedAt = ManualValidation.timestamp()
            record.credentialSource = "manual-capture"; record.publicationSkipped = false
            record.sessionHost = valid.host
            index.accounts[valid.accountKey] = record
            do { try save(index) } catch { try? vault.remove(newID); throw error }
            if let obsolete { try? vault.remove(obsolete) }
        }
    }
    func markPublished(_ keys: Set<String>) throws {
        try locked {
            var index = try load()
            for key in keys { index.accounts[key]?.pendingPublication = false; index.accounts[key]?.publicationSkipped = false }
            try save(index)
        }
    }
    func markPublicationSkipped(_ keys: Set<String>) throws {
        guard !keys.isEmpty else { return }
        try locked {
            var index = try load()
            for key in keys { index.accounts[key]?.pendingPublication = false; index.accounts[key]?.publicationSkipped = true }
            try save(index)
        }
    }
    func hide(_ key: String) throws {
        try locked { var index = try load(); index.accounts[key]?.visible = false; index.accounts[key]?.pendingPublication = false; try save(index) }
    }
    func archive() throws -> ManualArchiveContents {
        try locked {
            let index = try load()
            var accounts: [ManualArchiveAccount] = []
            for record in index.accounts.values.sorted(by: { $0.accountKey < $1.accountKey }) {
                var session: ManualSession?
                var previous: ManualSession?
                if let id = record.credentialID {
                    guard let data = try vault.read(id) else { throw ManualAccountError.storage }
                    session = try JSONDecoder().decode(ManualSession.self, from: data).validated()
                }
                if let id = record.previousCredentialID {
                    guard let data = try vault.read(id) else { throw ManualAccountError.storage }
                    previous = try JSONDecoder().decode(ManualSession.self, from: data).validated()
                }
                var legacy: [LegacySessionMaterial] = []
                for id in record.legacyCredentialIDs ?? [] {
                    guard let data = try vault.read(id) else { throw ManualAccountError.storage }
                    legacy.append(try JSONDecoder().decode(LegacySessionMaterial.self, from: data))
                }
                for kind in ["active", "candidate"] {
                    if let data = try vault.readLegacy(key: record.accountKey, kind: kind) {
                        let material = LegacySessionMaterial(kind: kind, rawJSON: data)
                        if !legacy.contains(material) { legacy.append(material) }
                    }
                }
                accounts.append(try ManualArchiveAccount(accountKey: record.accountKey, email: record.email, emailVerified: record.emailVerified, label: record.label, enabled: record.enabled, autoExchange: record.autoExchange, session: session, legacySessions: legacy.isEmpty ? nil : legacy, previousSession: previous).validated())
            }
            return ManualArchiveContents(accounts: accounts)
        }
    }
    /// One metadata commit publishes the batch. Any failed keychain write removes
    /// newly staged items, leaving every old record and its credential untouched.
    func applyImport(_ plan: ManualImportPlan, externalKeys: Set<String>) throws -> [ManualArchiveAccount] {
        try locked {
            var index = try load()
            let rechecked = try ManualImportPlan.make(ManualArchiveContents(accounts: plan.additions), existing: index.accounts.mapValues(\.email), additionalKeys: externalKeys)
            guard !rechecked.additions.isEmpty else { return [] }
            guard index.accounts.count + rechecked.additions.count <= 1000 else { throw ManualAccountError.invalid("本机账号资料数量已达上限，未导入任何账号。") }
            var staged: [String] = []
            do {
                for account in rechecked.additions {
                    var record = ManualAccountRecord(accountKey: account.accountKey, email: account.email, emailVerified: account.emailVerified, label: account.label, enabled: account.enabled, autoExchange: account.autoExchange)
                    record.credentialSource = "import"; record.publicationSkipped = false
                    if let session = account.session {
                        let id = account.accountKey + "." + UUID().uuidString.lowercased()
                        try vault.add(session.encoded(), id: id); staged.append(id)
                        record.credentialID = id; record.pendingPublication = true; record.sessionHost = session.host
                    }
                    if let legacy = account.legacySessions, !legacy.isEmpty {
                        var ids: [String] = []
                        for material in legacy {
                            let id = account.accountKey + "." + UUID().uuidString.lowercased()
                            try vault.add(JSONEncoder().encode(material), id: id); staged.append(id); ids.append(id)
                        }
                        record.legacyCredentialIDs = ids
                    }
                    if let previous = account.previousSession {
                        let id = account.accountKey + "." + UUID().uuidString.lowercased()
                        try vault.add(previous.encoded(), id: id); staged.append(id); record.previousCredentialID = id
                    }
                    index.accounts[account.accountKey] = record
                }
                try save(index)
            } catch { for id in staged { try? vault.remove(id) }; throw error }
            return rechecked.additions
        }
    }

    /// Reads only identity columns from the old module. It never starts maintenance,
    /// reads old credentials, deletes the old database, or upgrades a pending email.
    func migrateLegacyEmails(at databaseURL: URL? = nil) throws {
        let url = databaseURL ?? directory.deletingLastPathComponent().appendingPathComponent("login-refresh.sqlite")
        guard fileManager.fileExists(atPath: url.path) else { return }
        var database: OpaquePointer?
        guard sqlite3_open_v2(url.path, &database, SQLITE_OPEN_READONLY | SQLITE_OPEN_NOMUTEX, nil) == SQLITE_OK, let database else {
            if let database { sqlite3_close(database) }; throw ManualAccountError.storage
        }
        defer { sqlite3_close(database) }
        sqlite3_busy_timeout(database, 1000)
        var identities: [String: (String, Bool)] = [:]
        for (table, verified) in [("identity_pending", false), ("identity", true)] {
            var statement: OpaquePointer?
            guard sqlite3_prepare_v2(database, "SELECT account_key,email FROM \(table) LIMIT 1001", -1, &statement, nil) == SQLITE_OK, let statement else { continue }
            defer { sqlite3_finalize(statement) }
            while sqlite3_step(statement) == SQLITE_ROW {
                guard let keyBytes = sqlite3_column_text(statement, 0), let emailBytes = sqlite3_column_text(statement, 1),
                      let key = try? ManualValidation.key(String(cString: keyBytes)),
                      let email = try? ManualValidation.email(String(cString: emailBytes)) else { continue }
                guard identities[key] != nil || identities.count < 1000 else { throw ManualAccountError.storage }
                identities[key] = (email, verified)
            }
        }
        try locked {
            var index = try load()
            for (key, (email, verified)) in identities.sorted(by: { $0.key < $1.key }) {
                if let old = index.accounts[key], !old.email.isEmpty { continue }
                if index.accounts.contains(where: { $0.key != key && $0.value.email == email }) { continue }
                var record = index.accounts[key] ?? ManualAccountRecord(accountKey: key, label: "GLaDOS \(key.prefix(6))", visible: false)
                record.email = email; record.emailVerified = verified; index.accounts[key] = record
            }
            try save(index)
        }
    }
}

enum ManualArchiveCrypto {
    private struct Envelope: Codable {
        var schema = "glados.account-backup"
        var version = 1
        var cipher = "AES-256-GCM"
        var kdf = "PBKDF2-HMAC-SHA256"
        var iterations = 600_000
        var salt: Data
        var sealed: Data
        var authenticatedHeader: Data { Data("\(schema)|\(version)|\(cipher)|\(kdf)|\(iterations)|\(salt.base64EncodedString())".utf8) }
    }
    private static func key(password: String, salt: Data, iterations: Int) throws -> SymmetricKey {
        guard password.count >= 12, password.utf8.count <= 1024, salt.count == 32, iterations == 600_000 else { throw ManualAccountError.password }
        var output = Data(count: 32)
        let passwordData = Array(password.utf8)
        let status = output.withUnsafeMutableBytes { outputBytes in
            passwordData.withUnsafeBytes { passwordBytes in
                salt.withUnsafeBytes { saltBytes in
                    CCKeyDerivationPBKDF(CCPBKDFAlgorithm(kCCPBKDF2), passwordBytes.baseAddress?.assumingMemoryBound(to: Int8.self), passwordData.count,
                                        saltBytes.baseAddress?.assumingMemoryBound(to: UInt8.self), salt.count,
                                        CCPseudoRandomAlgorithm(kCCPRFHmacAlgSHA256), UInt32(iterations),
                                        outputBytes.baseAddress?.assumingMemoryBound(to: UInt8.self), 32)
                }
            }
        }
        guard status == kCCSuccess else { throw ManualAccountError.password }
        return SymmetricKey(data: output)
    }
    static func encrypt(_ contents: ManualArchiveContents, password: String) throws -> Data {
        guard !contents.accounts.isEmpty else { throw ManualAccountError.invalid("没有可导出的本地账号资料。") }
        _ = try ManualImportPlan.make(contents, existing: [:])
        var salt = Data(count: 32)
        let status = salt.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, 32, $0.baseAddress!) }
        guard status == errSecSuccess else { throw ManualAccountError.storage }
        var envelope = Envelope(salt: salt, sealed: Data())
        let key = try key(password: password, salt: salt, iterations: envelope.iterations)
        let plain = try JSONEncoder().encode(contents)
        guard plain.count <= 24 * 1024 * 1024 else { throw ManualAccountError.invalid("账号备份超过大小限制。") }
        let box = try AES.GCM.seal(plain, using: key, authenticating: envelope.authenticatedHeader)
        guard let combined = box.combined else { throw ManualAccountError.storage }
        envelope.sealed = combined
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(envelope)
    }
    static func decrypt(_ data: Data, password: String) throws -> ManualArchiveContents {
        do {
            guard data.count <= 32 * 1024 * 1024 else { throw ManualAccountError.password }
            let envelope = try JSONDecoder().decode(Envelope.self, from: data)
            guard envelope.schema == "glados.account-backup", envelope.version == 1,
                  envelope.cipher == "AES-256-GCM", envelope.kdf == "PBKDF2-HMAC-SHA256",
                  envelope.sealed.count >= 28 else { throw ManualAccountError.password }
            let key = try key(password: password, salt: envelope.salt, iterations: envelope.iterations)
            let box = try AES.GCM.SealedBox(combined: envelope.sealed)
            let plain = try AES.GCM.open(box, using: key, authenticating: envelope.authenticatedHeader)
            let contents = try JSONDecoder().decode(ManualArchiveContents.self, from: plain)
            _ = try ManualImportPlan.make(contents, existing: [:])
            return contents
        } catch { throw ManualAccountError.password }
    }
}
