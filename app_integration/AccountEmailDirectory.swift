import Foundation
import SQLite3

// Read-only access to the existing app's private identity database. No credentials,
// status mutations, network calls or directory creation occur in this adapter.
enum AccountEmailDirectory {
    static func load(at url: URL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/GLaDOS Account Center/login-refresh.sqlite")) -> [String: String] {
        guard FileManager.default.fileExists(atPath: url.path) else { return [:] }
        var database: OpaquePointer?
        guard sqlite3_open_v2(url.path, &database, SQLITE_OPEN_READONLY | SQLITE_OPEN_NOMUTEX, nil) == SQLITE_OK,
              let database else {
            if let database { sqlite3_close(database) }
            return [:]
        }
        defer { sqlite3_close(database) }
        sqlite3_busy_timeout(database, 1000)
        var result: [String: String] = [:]
        // Confirmed identity wins. A manually entered target is only a display aid
        // here, never a proof authorizing replacement of an account credential.
        for query in ["SELECT account_key,email FROM identity_pending", "SELECT account_key,email FROM identity"] {
            var statement: OpaquePointer?
            guard sqlite3_prepare_v2(database, query, -1, &statement, nil) == SQLITE_OK,
                  let statement else { continue }
            while sqlite3_step(statement) == SQLITE_ROW {
                guard let keyBytes = sqlite3_column_text(statement, 0), let emailBytes = sqlite3_column_text(statement, 1) else { continue }
                let key = String(cString: keyBytes).uppercased()
                let email = String(cString: emailBytes).trimmingCharacters(in: .whitespacesAndNewlines)
                guard key.range(of: "^[A-F0-9]{16}$", options: .regularExpression) != nil,
                      email.range(of: "^[^\\s<>@,;]+@[^\\s<>@,;]+\\.[^\\s<>@,;]+$", options: .regularExpression) != nil else { continue }
                result[key] = email
            }
            sqlite3_finalize(statement)
        }
        return result
    }
}
