// Internal Account Center component; not a separate .app or a general Keychain CLI.
// Only this app's fixed service and validated record keys are addressable.
import Foundation
import Security
import LocalAuthentication

private let productionService = "com.enoch.glados-account-center.login-refresh"
private let testService = "com.enoch.glados-account-center.login-refresh.selftest"
private let inputLimit = 65536

struct StoreFailure: Error { let reason: String; let status: OSStatus? }

final class RefreshSecretStore {
    private let service: String
    private let context: LAContext

    init(testing: Bool = false) {
        service = testing ? testService : productionService
        context = LAContext()
        context.interactionNotAllowed = true
    }

    private func validated(_ key: String) throws -> String {
        let pattern = "^(gmail-client|gmail-token|candidate-[A-F0-9]{16}|active-[A-F0-9]{16}|test-[a-f0-9-]{36})$"
        guard key.range(of: pattern, options: .regularExpression) != nil,
              (!key.hasPrefix("test-") || service == testService) else {
            throw StoreFailure(reason: "invalid_secret_key", status: nil)
        }
        return key
    }

    private func query(_ key: String) throws -> [String: Any] {
        // No wildcards or enumeration: exact service and account, one item only.
        return [kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: service,
                kSecAttrAccount as String: try validated(key),
                kSecUseAuthenticationContext as String: context]
    }

    private func check(_ status: OSStatus) throws {
        guard status == errSecSuccess else {
            let reason: String
            switch status {
            case errSecInteractionNotAllowed, errSecAuthFailed: reason = "keychain_locked_or_approval_required"
            default: reason = "keychain_operation_failed"
            }
            throw StoreFailure(reason: reason, status: status)
        }
    }

    func get(_ key: String) throws -> Data? {
        var q = try query(key)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        try check(status)
        guard let data = result as? Data, data.count <= inputLimit else {
            throw StoreFailure(reason: "invalid_secret_record", status: nil)
        }
        return data
    }

    func put(_ key: String, data: Data) throws {
        guard !data.isEmpty, data.count <= 32768,
              (try? JSONSerialization.jsonObject(with: data)) is [String: Any] else {
            throw StoreFailure(reason: "invalid_secret_record", status: nil)
        }
        let q = try query(key)
        let update = SecItemUpdate(q as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if update == errSecItemNotFound {
            var item = q
            item[kSecValueData as String] = data
            item[kSecAttrLabel as String] = "GLaDOS Account Center login refresh"
            // The file-based macOS Keychain uses its encrypted at-rest storage and
            // default application access control. Do not opt in to iCloud sync or
            // change any system Keychain settings/access lists.
            try check(SecItemAdd(item as CFDictionary, nil))
        } else { try check(update) }
    }

    func delete(_ key: String) throws {
        let status = SecItemDelete(try query(key) as CFDictionary)
        if status != errSecItemNotFound { try check(status) }
    }
}

#if REFRESH_SECRET_CLI
@main
struct SecretStoreMain {
    static func respond(_ value: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { return }
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([10]))
    }

    static func main() {
        // Legacy Keychain may consult this process-wide flag in addition to
        // LAContext. It changes this short-lived worker only, never system policy.
        guard SecKeychainSetUserInteractionAllowed(false) == errSecSuccess else {
            respond(["ok": false, "reason": "keychain_noninteractive_unavailable"]); return
        }
        do {
            if CommandLine.arguments == [CommandLine.arguments[0], "--self-test"] {
                let store = RefreshSecretStore(testing: true)
                let key = "test-" + UUID().uuidString.lowercased()
                var removed = false
                defer { if !removed { try? store.delete(key) } }
                guard try store.get(key) == nil else { throw StoreFailure(reason: "test_collision", status: nil) }
                let first = Data("{\"synthetic\":\"not-a-real-credential\"}".utf8)
                let second = Data("{\"synthetic\":\"replacement\"}".utf8)
                try store.put(key, data: first)
                guard try store.get(key) == first else { throw StoreFailure(reason: "test_readback_failed", status: nil) }
                try store.put(key, data: second)
                guard try store.get(key) == second else { throw StoreFailure(reason: "test_update_failed", status: nil) }
                try store.delete(key)
                guard try store.get(key) == nil else { throw StoreFailure(reason: "test_cleanup_failed", status: nil) }
                removed = true
                respond(["ok": true, "self_test": "put_get_update_delete", "record_removed": true,
                         "production_service_touched": false, "ui_allowed": false])
                return
            }
            guard CommandLine.arguments.count == 1 else { throw StoreFailure(reason: "stdin_only", status: nil) }
            var data = Data()
            while data.count <= inputLimit {
                let part = try FileHandle.standardInput.read(upToCount: min(8192, inputLimit + 1 - data.count)) ?? Data()
                if part.isEmpty { break }
                data.append(part)
            }
            guard data.count <= inputLimit,
                  let request = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let op = request["op"] as? String, let key = request["key"] as? String,
                  Set(request.keys).isSubset(of: ["op", "key", "value"]) else {
                throw StoreFailure(reason: "invalid_secret_request", status: nil)
            }
            let store = RefreshSecretStore()
            switch op {
            case "get":
                if let stored = try store.get(key) {
                    guard let value = try JSONSerialization.jsonObject(with: stored) as? [String: Any] else {
                        throw StoreFailure(reason: "invalid_secret_record", status: nil)
                    }
                    // The host captures this pipe in memory; never relay it to logs.
                    respond(["ok": true, "found": true, "value": value])
                } else { respond(["ok": true, "found": false]) }
            case "put":
                guard let value = request["value"] as? [String: Any] else {
                    throw StoreFailure(reason: "invalid_secret_record", status: nil)
                }
                try store.put(key, data: JSONSerialization.data(withJSONObject: value))
                respond(["ok": true])
            case "delete":
                try store.delete(key); respond(["ok": true])
            default: throw StoreFailure(reason: "invalid_secret_operation", status: nil)
            }
        } catch let error as StoreFailure {
            var result: [String: Any] = ["ok": false, "reason": error.reason]
            if let status = error.status { result["status"] = status }
            respond(result)
        } catch { respond(["ok": false, "reason": "invalid_secret_request"]) }
    }
}
#endif
