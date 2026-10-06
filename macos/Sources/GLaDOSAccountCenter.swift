import SwiftUI
import AppKit
import Foundation

private let defaultRepository = "NewBoringMan/Glados-Railgun-checkin"
private let defaultBranch = "master"
private let checkinWorkflowName = "gladosAccounts.yml"
private let statusWorkflowName = "gladosStatus.yml"
private let accountsConfigPath = ".github/glados/accounts.json"
private let exchangeCatalogPath = ".github/glados/exchange_plans.json"
private let checkinWorkflowPath = ".github/workflows/gladosAccounts.yml"
private let legacyWorkflowPath = ".github/workflows/gladosCheck.yml"
private let statusWorkflowPath = ".github/workflows/gladosStatus.yml"

struct ScheduleConfig: Codable, Equatable, Sendable {
    var timezone: String = "Asia/Taipei"
    var times: [String] = ["05:00", "17:00"]
}

struct AccountDefaults: Codable, Equatable, Sendable {
    var enabled: Bool = true
    var autoExchange: Bool = false
}

struct AccountConfig: Codable, Equatable, Identifiable, Sendable {
    var enabled: Bool = true
    var autoExchange: Bool = false
    var label: String
    var id: String = ""

    enum CodingKeys: String, CodingKey { case enabled, autoExchange, label }
}

struct RepositoryConfig: Codable, Equatable, Sendable {
    var version: Int = 2
    var schedule = ScheduleConfig()
    var defaults = AccountDefaults()
    var accounts: [String: AccountConfig] = [:]
}

struct ExchangePlan: Codable, Hashable, Identifiable, Sendable {
    var id: String
    var points: Int
    var days: Int
    var verified: Bool
    var costPerDay: Double { Double(points) / Double(days) }
}

struct PlanCatalog: Codable, Sendable {
    struct Policy: Codable, Sendable { var primary: String; var tieBreakers: [String] }
    var version: Int
    var updatedAt: String
    var selectionPolicy: Policy
    var plans: [ExchangePlan]

    var bestPlan: ExchangePlan? {
        plans.filter { $0.verified && $0.points > 0 && $0.days > 0 }.min {
            if abs($0.costPerDay - $1.costPerDay) > 0.0000001 { return $0.costPerDay < $1.costPerDay }
            if $0.days != $1.days { return $0.days < $1.days }
            if $0.points != $1.points { return $0.points < $1.points }
            return $0.id < $1.id
        }
    }
}

struct CheckinDay: Codable, Hashable, Identifiable, Sendable {
    var date: String
    var state: String
    var pointsDelta: Int?
    var id: String { date }

    enum CodingKeys: String, CodingKey {
        case date, state
        case pointsDelta = "points_delta"
    }
}

struct AccountStatus: Codable, Identifiable, Sendable {
    var accountKey: String
    var domain: String
    var ok: Bool
    var daysLeft: Int?
    var pointsTotal: Int?
    var email: String
    var planName: String
    var vipLevel: Int?
    var streak: Int?
    var checkinHistory: [CheckinDay]?
    var historySource: String?
    var statusWarning: String?
    var sessionWarning: String?
    var errorKind: String?
    var authenticationState: String?
    var checkinVerified: Bool?
    var error: String
    var id: String { accountKey }

    enum CodingKeys: String, CodingKey {
        case accountKey = "account_key"
        case domain, ok
        case daysLeft = "days_left"
        case pointsTotal = "points_total"
        case email
        case planName = "plan_name"
        case vipLevel = "vip_level"
        case streak
        case checkinHistory = "checkin_history"
        case historySource = "history_source"
        case statusWarning = "status_warning"
        case sessionWarning = "session_warning"
        case errorKind = "error_kind"
        case authenticationState = "authentication_state"
        case checkinVerified = "checkin_verified"
        case error
    }
}

struct RunInfo: Identifiable, Hashable, Sendable {
    var id: Int
    var status: String
    var conclusion: String
    var url: String
    var createdAt: String
}

struct RepositorySnapshot: Sendable {
    var config: RepositoryConfig
    var catalog: PlanCatalog
    var runs: [RunInfo]
}


struct BrowserOption: Identifiable, Hashable, Sendable {
    let id: String
    let label: String
    let appPath: String
    let note: String
    let systemImage: String
}

struct CapturePayload: Sendable {
    var accountKey: String
    var secretName: String
    var cookieHeader: String
    var email: String
    var daysLeft: Int?
    var host: String
    var browser: String
    var userAgent: String
    var capturedAt: String?

    func manualSession() throws -> ManualSession {
        let session = try ManualSession(accountKey: accountKey, email: email, cookieHeader: cookieHeader,
                                        host: host, userAgent: userAgent, browser: browser,
                                        capturedAt: capturedAt ?? ManualValidation.timestamp()).validated()
        guard secretName == "GLADOS_ACCOUNT_\(session.accountKey)" else { throw ManualAccountError.conflict }
        return session
    }
}

struct CommandResult: Sendable {
    var stdout: String
    var stderr: String
    var exitCode: Int32
}

enum AppError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case .message(let text): return text }
    }
}

final class LockedDataBox: @unchecked Sendable {
    private let lock = NSLock()
    private var storage = Data()
    func append(_ data: Data) { guard !data.isEmpty else { return }; lock.lock(); storage.append(data); lock.unlock() }
    func snapshot() -> Data { lock.lock(); defer { lock.unlock() }; return storage }
}

final class ProcessRunner {
    static func executable(_ name: String) -> URL? {
        let candidates = ["/opt/homebrew/bin/\(name)", "/usr/local/bin/\(name)", "/usr/bin/\(name)"]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }.map(URL.init(fileURLWithPath:))
    }

    static func run(_ executable: URL, _ arguments: [String], input: Data? = nil, environment: [String: String]? = nil, timeout: TimeInterval = 180) throws -> CommandResult {
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        if let environment { process.environment = environment }
        let out = Pipe(); let err = Pipe(); let stdin = Pipe()
        process.standardOutput = out; process.standardError = err
        if input != nil { process.standardInput = stdin }

        let stdoutBox = LockedDataBox(); let stderrBox = LockedDataBox()
        out.fileHandleForReading.readabilityHandler = { handle in stdoutBox.append(handle.availableData) }
        err.fileHandleForReading.readabilityHandler = { handle in stderrBox.append(handle.availableData) }

        try process.run()
        if let input {
            stdin.fileHandleForWriting.write(input)
            try? stdin.fileHandleForWriting.close()
        }
        let deadline = Date().addingTimeInterval(timeout)
        while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.08) }
        if process.isRunning {
            process.terminate()
            out.fileHandleForReading.readabilityHandler = nil
            err.fileHandleForReading.readabilityHandler = nil
            throw AppError.message("命令执行超时：\(executable.lastPathComponent)")
        }
        Thread.sleep(forTimeInterval: 0.03)
        out.fileHandleForReading.readabilityHandler = nil
        err.fileHandleForReading.readabilityHandler = nil
        stdoutBox.append(out.fileHandleForReading.readDataToEndOfFile())
        stderrBox.append(err.fileHandleForReading.readDataToEndOfFile())
        let stdout = String(decoding: stdoutBox.snapshot(), as: UTF8.self)
        let stderr = String(decoding: stderrBox.snapshot(), as: UTF8.self)
        return CommandResult(stdout: stdout, stderr: stderr, exitCode: process.terminationStatus)
    }
}

final class GitHubClient: @unchecked Sendable {
    let repo: String
    let branch: String
    let gh: URL
    let receiptStore: LocalManualAccountStore?

    init(repo: String = defaultRepository, branch: String = defaultBranch, receiptStore: LocalManualAccountStore? = nil) throws {
        guard let gh = ProcessRunner.executable("gh") else { throw AppError.message("未找到 GitHub CLI。请先运行安装依赖脚本。") }
        self.repo = repo; self.branch = branch; self.gh = gh; self.receiptStore = receiptStore
    }

    func ensureReady() throws {
        let auth = try ProcessRunner.run(gh, ["auth", "status", "--active", "--hostname", "github.com"], timeout: 30)
        guard auth.exitCode == 0 else {
            throw AppError.message("本机 GitHub CLI 尚未登录。ChatGPT 中连接 GitHub 不等于这台 Mac 上的 gh 已授权。请点击“连接 GitHub”，或运行交付包中的“连接 GitHub.command”。")
        }
        let view = try ProcessRunner.run(gh, ["repo", "view", repo, "--json", "viewerPermission"], timeout: 30)
        guard view.exitCode == 0 else { throw AppError.message(view.stderr.isEmpty ? "无法访问目标 GitHub 仓库。" : view.stderr) }
        let object = try jsonObject(view.stdout)
        let permission = object["viewerPermission"] as? String ?? ""
        guard ["ADMIN", "MAINTAIN", "WRITE"].contains(permission) else { throw AppError.message("当前 GitHub 账户没有仓库写入权限。") }
    }

    func readTextFile(_ path: String, ref: String? = nil) throws -> (String, String?) {
        let endpoint = "repos/\(repo)/contents/\(path)?ref=\(ref ?? branch)"
        let result = try ProcessRunner.run(gh, ["api", endpoint], timeout: 45)
        guard result.exitCode == 0 else { throw AppError.message(result.stderr.isEmpty ? "无法读取 \(path)" : result.stderr) }
        let object = try jsonObject(result.stdout)
        guard var encoded = object["content"] as? String else { throw AppError.message("GitHub 返回的 \(path) 缺少 content。") }
        encoded = encoded.replacingOccurrences(of: "\n", with: "")
        guard let data = Data(base64Encoded: encoded), let text = String(data: data, encoding: .utf8) else { throw AppError.message("无法解码 \(path)。") }
        return (text, object["sha"] as? String)
    }

    func upsertTextFile(_ path: String, content: String, message: String) throws {
        let current = try? readTextFile(path)
        var payload: [String: Any] = [
            "message": message,
            "content": Data(content.utf8).base64EncodedString(),
            "branch": branch
        ]
        if let sha = current?.1 { payload["sha"] = sha }
        let data = try JSONSerialization.data(withJSONObject: payload)
        let endpoint = "repos/\(repo)/contents/\(path)"
        let result = try ProcessRunner.run(gh, ["api", endpoint, "--method", "PUT", "--input", "-"], input: data, timeout: 90)
        guard result.exitCode == 0 else { throw AppError.message(result.stderr.isEmpty ? "写入 \(path) 失败。" : result.stderr) }
    }

    func setSecret(name: String, value: String) throws {
        guard name.range(of: "^GLADOS_ACCOUNT_[A-F0-9]{16}$", options: .regularExpression) != nil else { throw AppError.message("Secret 名称未通过安全校验。") }
        guard value.utf8.count <= 48 * 1024 - 1 else { throw AppError.message("登录资料超过 GitHub Secret 的容量限制；完整资料已保存在本机，未发送截断内容。") }
        let structured = try ManualSessionFormatGate.requiresRemoteSupport(value)
        if structured {
            do {
                // Secrets are shared by repository: a candidate App must verify
                // production master on every write, regardless of its UI branch.
                let marker = try readTextFile(ManualSessionFormatGate.markerPath, ref: defaultBranch).0
                try ManualSessionFormatGate.validateMarker(Data(marker.utf8))
            } catch { throw ManualAccountError.remoteSessionFormat }
        }
        let key = String(name.dropFirst("GLADOS_ACCOUNT_".count))
        if structured {
            guard let receiptStore else { throw ManualAccountError.storage }
            try receiptStore.recordPublication(key: key, state: "pending")
        }
        do {
            let result = try ProcessRunner.run(gh, ["secret", "set", name, "--repo", repo], input: Data((value + "\n").utf8), timeout: 60)
            guard result.exitCode == 0 else { throw AppError.message("更新 GitHub Secret 未完成；已保存的本机登录资料保留，可手动重试同步。") }
            if structured { try receiptStore?.recordPublication(key: key, state: "acknowledged") }
        } catch {
            if structured { try receiptStore?.recordPublication(key: key, state: "uncertain") }
            throw AppError.message("此次发布结果尚不确定；本机登录资料已保留，可查看阶段回执后手动同步。")
        }
    }

    func deleteSecret(name: String) throws {
        let result = try ProcessRunner.run(gh, ["secret", "delete", name, "--repo", repo], timeout: 60)
        guard result.exitCode == 0 else { throw AppError.message(result.stderr.isEmpty ? "删除 GitHub Secret 失败。" : result.stderr) }
    }

    func managedAccountKeys() throws -> Set<String> {
        let result = try ProcessRunner.run(gh, ["secret", "list", "--repo", repo, "--json", "name"], timeout: 45)
        guard result.exitCode == 0,
              let rows = try JSONSerialization.jsonObject(with: Data(result.stdout.utf8)) as? [[String: Any]] else {
            throw AppError.message("无法核对已有账号目录；未导入或覆盖账号。")
        }
        return Set(rows.compactMap { row in
            guard let name = row["name"] as? String, name.hasPrefix("GLADOS_ACCOUNT_"),
                  let key = try? ManualValidation.key(String(name.dropFirst("GLADOS_ACCOUNT_".count))) else { return nil }
            return key
        })
    }

    private func apiJSON(_ endpoint: String, method: String = "GET", payload: [String: Any]? = nil) throws -> [String: Any] {
        var arguments = ["api", "repos/\(repo)/\(endpoint)", "--method", method]
        var input: Data?
        if let payload { arguments += ["--input", "-"]; input = try JSONSerialization.data(withJSONObject: payload) }
        let result = try ProcessRunner.run(gh, arguments, input: input, timeout: 90)
        guard result.exitCode == 0 else { throw AppError.message("GitHub 配置更新未完成；本机资料保留。请同步配置后手动重试。") }
        return try jsonObject(result.stdout)
    }

    /// One Git commit publishes all related files. The non-force ref update rejects
    /// concurrent changes; no best-effort rollback can overwrite another writer.
    func atomicWriteTextFiles(_ files: [String: String], expected: [String: String], message: String) throws {
        let allowed = Set([accountsConfigPath, checkinWorkflowPath, statusWorkflowPath, legacyWorkflowPath])
        guard Set(files.keys).isSubset(of: allowed), Set(files.keys) == Set(expected.keys) else { throw AppError.message("配置写入范围无效。") }
        let reference = try apiJSON("git/ref/heads/\(branch)")
        guard let object = reference["object"] as? [String: Any], let head = object["sha"] as? String,
              head.range(of: "^[a-f0-9]{40}$", options: .regularExpression) != nil else { throw AppError.message("无法核对仓库版本。") }
        for (path, prior) in expected {
            guard try readTextFile(path, ref: head).0 == prior else { throw AppError.message("账号配置已在其他位置改变；未覆盖最新配置。请重新同步后再试。") }
        }
        let changed = files.filter { expected[$0.key] != $0.value }
        if changed.isEmpty { return }
        let commit = try apiJSON("git/commits/\(head)")
        guard let tree = commit["tree"] as? [String: Any], let treeSHA = tree["sha"] as? String else { throw AppError.message("仓库树信息不完整。") }
        let entries = changed.keys.sorted().map { ["path": $0, "mode": "100644", "type": "blob", "content": changed[$0]!] }
        let newTree = try apiJSON("git/trees", method: "POST", payload: ["base_tree": treeSHA, "tree": entries])
        guard let newTreeSHA = newTree["sha"] as? String else { throw AppError.message("无法准备账号配置。") }
        let created = try apiJSON("git/commits", method: "POST", payload: ["message": message, "tree": newTreeSHA, "parents": [head]])
        guard let newSHA = created["sha"] as? String else { throw AppError.message("无法创建账号配置版本。") }
        _ = try apiJSON("git/refs/heads/\(branch)", method: "PATCH", payload: ["sha": newSHA, "force": false])
    }

    func recentRuns(workflow: String, limit: Int = 12) throws -> [RunInfo] {
        let result = try ProcessRunner.run(gh, ["run", "list", "--repo", repo, "--workflow", workflow, "--limit", String(limit), "--json", "databaseId,status,conclusion,url,createdAt"], timeout: 45)
        guard result.exitCode == 0 else { throw AppError.message(result.stderr) }
        guard let array = try JSONSerialization.jsonObject(with: Data(result.stdout.utf8)) as? [[String: Any]] else { return [] }
        return array.compactMap {
            guard let id = $0["databaseId"] as? Int else { return nil }
            return RunInfo(id: id, status: $0["status"] as? String ?? "", conclusion: $0["conclusion"] as? String ?? "", url: $0["url"] as? String ?? "", createdAt: $0["createdAt"] as? String ?? "")
        }
    }

    private func jsonObject(_ text: String) throws -> [String: Any] {
        guard let object = try JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any] else { throw AppError.message("GitHub 返回了无法解析的数据。") }
        return object
    }
}

final class CaptureService: @unchecked Sendable {
    func availableBrowsers() -> [BrowserOption] {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates: [BrowserOption] = [
            BrowserOption(id: "safari", label: "Safari", appPath: "/Applications/Safari.app", note: "使用普通 Safari 登录状态，需要启用配套 Safari 扩展。", systemImage: "safari"),
            BrowserOption(id: "chrome", label: "Google Chrome", appPath: "/Applications/Google Chrome.app", note: "使用 Account Center 专用资料目录，登录状态可持续保留。", systemImage: "globe"),
            BrowserOption(id: "edge", label: "Microsoft Edge", appPath: "/Applications/Microsoft Edge.app", note: "使用专用资料目录。", systemImage: "globe"),
            BrowserOption(id: "brave", label: "Brave Browser", appPath: "/Applications/Brave Browser.app", note: "使用专用资料目录。", systemImage: "shield"),
            BrowserOption(id: "arc", label: "Arc", appPath: "/Applications/Arc.app", note: "使用专用资料目录。", systemImage: "circle.hexagongrid"),
            BrowserOption(id: "firefox", label: "Mozilla Firefox", appPath: "/Applications/Firefox.app", note: "通过 WebDriver 隔离窗口读取；可能需要重新登录。", systemImage: "flame"),
            BrowserOption(id: "opera", label: "Opera", appPath: "/Applications/Opera.app", note: "使用专用资料目录。", systemImage: "globe"),
            BrowserOption(id: "opera-gx", label: "Opera GX", appPath: "/Applications/Opera GX.app", note: "使用专用资料目录。", systemImage: "gamecontroller"),
            BrowserOption(id: "vivaldi", label: "Vivaldi", appPath: "/Applications/Vivaldi.app", note: "使用专用资料目录。", systemImage: "globe"),
            BrowserOption(id: "chromium", label: "Chromium", appPath: "/Applications/Chromium.app", note: "使用专用资料目录。", systemImage: "globe")
        ]
        return candidates.filter { option in
            if FileManager.default.fileExists(atPath: option.appPath) { return true }
            if option.id == "safari" && FileManager.default.fileExists(atPath: "/System/Applications/Safari.app") { return true }
            let userPath = "\(home)/Applications/\((option.appPath as NSString).lastPathComponent)"
            let externalPath = "/Volumes/MacData/Applications/\((option.appPath as NSString).lastPathComponent)"
            return FileManager.default.fileExists(atPath: userPath) || FileManager.default.fileExists(atPath: externalPath)
        }
    }

    func capture(browserID: String, expectedAccountKey: String? = nil, expectedHost: String? = nil) throws -> CapturePayload? {
        guard let node = ProcessRunner.executable("node") else { throw AppError.message("未找到 Node.js。请运行安装依赖脚本。") }
        guard let resourceURL = Bundle.main.resourceURL else { throw AppError.message("应用资源目录不可用。") }
        let helper = resourceURL.appendingPathComponent("capture_account.js")
        guard FileManager.default.fileExists(atPath: helper.path) else { throw AppError.message("账号读取组件缺失。") }
        var environment = ProcessInfo.processInfo.environment
        environment["GLADOS_CAPTURE_ONLY"] = "1"
        environment["GLADOS_BROWSER_ID"] = browserID
        environment.removeValue(forKey: "GLADOS_EXPECTED_ACCOUNT_KEY")
        environment.removeValue(forKey: "GLADOS_EXPECTED_HOST")
        if let expectedAccountKey { environment["GLADOS_EXPECTED_ACCOUNT_KEY"] = try ManualValidation.key(expectedAccountKey) }
        if let expectedHost {
            guard ManualValidation.allowedHosts.contains(expectedHost) else { throw ManualAccountError.conflict }
            environment["GLADOS_EXPECTED_HOST"] = expectedHost
        }
        let result = try ProcessRunner.run(node, [helper.path], environment: environment, timeout: 3600)
        guard result.exitCode == 0 else {
            if captureErrorCode(result.stderr) == "cancelled" { return nil }
            throw AppError.message(redact(result.stderr))
        }
        guard let line = result.stdout.split(separator: "\n").map(String.init).first(where: { $0.hasPrefix("GLADOS_CAPTURE_JSON=") }) else { throw AppError.message("账号读取组件没有返回有效结果。") }
        let json = String(line.dropFirst("GLADOS_CAPTURE_JSON=".count))
        guard let object = try JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any],
              let key = object["accountKey"] as? String,
              let secret = object["secretName"] as? String,
              let cookie = object["cookieHeader"] as? String else { throw AppError.message("账号读取结果格式无效。") }
        let payload = CapturePayload(accountKey: key, secretName: secret, cookieHeader: cookie, email: object["email"] as? String ?? "", daysLeft: object["daysLeft"] as? Int, host: object["host"] as? String ?? "", browser: object["browser"] as? String ?? "", userAgent: object["userAgent"] as? String ?? "", capturedAt: object["capturedAt"] as? String)
        let session = try payload.manualSession()
        if let expectedAccountKey, session.accountKey != expectedAccountKey { throw ManualAccountError.conflict }
        if let expectedHost, session.host != expectedHost { throw ManualAccountError.conflict }
        return payload
    }

    private func captureErrorCode(_ text: String) -> String? {
        let prefix = "GLADOS_CAPTURE_ERROR_CODE="
        let codes = text.split(separator: "\n").map(String.init).filter { $0.hasPrefix(prefix) }.map { String($0.dropFirst(prefix.count)) }
        return codes.count == 1 ? codes[0] : nil
    }
    private func redact(_ text: String) -> String {
        // Display only our own fixed messages for a strict helper-code whitelist;
        // raw browser/HTTP stderr can contain credentials and is never shown.
        switch captureErrorCode(text) {
        case "browser_connection": return "无法连接到该账号的浏览器读取窗口。请自行退出该账号的专属浏览器实例，再重新读取登录信息。"
        case "identity_mismatch": return "当前浏览器账号与待更新账号不一致。请切换到该账号正常登录后重新读取。"
        case "cookie_scope_mismatch": return "各接口所需登录 Cookie 不一致，当前保存格式无法安全复用，未保存；请保留原网页登录。"
        case "cookie_scope_unavailable": return "当前 Firefox 或 geckodriver 不支持所需的只读 Cookie 接口，或无法核实浏览器上下文。请升级后重试，或手动使用 Edge / Safari。"
        case "missing_identity": return "网站未返回可核验的账号标识和邮箱，此次读取暂时无法保存。已记录的账号资料保留。"
        case "verification_required": return "网站要求完成登录验证。请在所选浏览器中正常完成验证，再手动重新读取。"
        case "safari_extension_unavailable": return "Safari 登录读取扩展尚不可用。请在 Safari 设置中启用配套扩展，并允许访问对应的 GLaDOS 页面。"
        default: return "手动读取未完成。请确认已正常登录所选账号，并完成网站要求的验证。"
        }
    }
}

enum WorkflowBuilder {
    static func checkin(config: RepositoryConfig) -> String {
        let enabled = config.accounts.keys.sorted().filter { config.accounts[$0]?.enabled == true }
        var lines = [
            "# Managed by GLaDOS Account Center V2.",
            "# Account cookies remain in independent GitHub Actions secrets.",
            "name: GLaDOS Multi-Account Check-in", "",
            "on:", "  workflow_dispatch:", "    inputs:", "      account:",
            "        description: Account key to run, or all", "        required: false", "        default: all", "        type: string"
        ]
        lines.append(contentsOf: scheduleLines(config.schedule))
        lines.append(contentsOf: ["", "permissions:", "  contents: read", "", "concurrency:", "  group: glados-multi-account-${{ github.ref }}", "  cancel-in-progress: false", "", "jobs:"])
        if enabled.isEmpty {
            lines.append(contentsOf: ["  no_enabled_accounts:", "    runs-on: ubuntu-latest", "    steps:", "      - run: echo 'No enabled GLaDOS accounts.'"])
        }
        for key in enabled {
            let exchange = config.accounts[key]?.autoExchange == true ? "true" : "false"
            lines.append(contentsOf: accountJob(key: key, autoExchange: exchange))
        }
        return lines.joined(separator: "\n") + "\n"
    }

    static func status(config: RepositoryConfig) -> String {
        var lines = [
            "name: GLaDOS Account Status", "", "on:", "  workflow_dispatch:", "    inputs:", "      account:",
            "        description: Account key to refresh, or all", "        required: false", "        default: all", "        type: string", "",
            "permissions:", "  contents: read", "", "concurrency:", "  group: glados-status-${{ github.ref }}", "  cancel-in-progress: true", "", "jobs:"
        ]
        for key in config.accounts.keys.sorted() { lines.append(contentsOf: statusJob(key: key)) }
        if config.accounts.isEmpty { lines.append(contentsOf: ["  no_accounts:", "    runs-on: ubuntu-latest", "    steps:", "      - run: echo 'No GLaDOS accounts.'"]) }
        return lines.joined(separator: "\n") + "\n"
    }

    static func legacy() -> String {
        let lines = [
            "# Legacy fallback only. Scheduled check-ins are managed by gladosAccounts.yml.",
            "name: auto check (legacy fallback)", "", "on:", "  workflow_dispatch:", "",
            "jobs:", "  build:", "    name: glados/railgun legacy fallback", "    runs-on: ubuntu-latest", "    timeout-minutes: 5",
            "    permissions:", "      contents: read", "    steps:",
            "      - name: Checkout Code", "        uses: actions/checkout@v6",
            "      - name: Set up Python", "        uses: actions/setup-python@v6", "        with:", "          python-version: '3.13'",
            "      - name: Install dependencies", "        run: pip install -r requirements.txt",
            "      - name: Running legacy fallback check-in", "        env:",
            "          GLADOS_COOKIES: ${{ secrets.GLADOS_COOKIES }}",
            "          GLADOS_AUTO_EXCHANGE: 'false'",
            "          GLADOS_EXCHANGE_CATALOG: '.github/glados/exchange_plans.json'",
            "          PUSHDEER_SENDKEY: ${{ secrets.PUSHDEER_SENDKEY }}",
            "          GLADOS_VERBOSE: ${{ secrets.GLADOS_VERBOSE }}",
            "        run: python checkin.py"
        ]
        return lines.joined(separator: "\n") + "\n"
    }

    private static func scheduleLines(_ schedule: ScheduleConfig) -> [String] {
        var groups: [Int: [Int]] = [:]
        for value in schedule.times {
            let parts = value.split(separator: ":")
            guard parts.count == 2, let hour = Int(parts[0]), let minute = Int(parts[1]), (0...23).contains(hour), (0...59).contains(minute) else { continue }
            groups[minute, default: []].append(hour)
        }
        var lines = ["  schedule:"]
        for minute in groups.keys.sorted() {
            let hours = Array(Set(groups[minute] ?? [])).sorted().map(String.init).joined(separator: ",")
            lines.append("    - cron: '\(minute) \(hours) * * *'")
            lines.append("      timezone: '\(schedule.timezone)'")
        }
        return lines
    }

    static func accountJob(key: String, autoExchange: String) -> [String] {
        [
            "  account_\(key.lowercased()):", "    name: GLaDOS account \(key)",
            "    if: ${{ github.event_name == 'schedule' || inputs.account == 'all' || inputs.account == '\(key)' }}",
            "    runs-on: ubuntu-latest", "    timeout-minutes: 5", "    steps:",
            "      - name: Checkout code", "        uses: actions/checkout@v6",
            "      - name: Set up Python", "        uses: actions/setup-python@v6", "        with:", "          python-version: '3.13'",
            "      - name: Install dependencies", "        run: pip install -r requirements.txt",
            "      - name: Run check-in", "        env:",
            "          GLADOS_COOKIES: ${{ secrets.GLADOS_ACCOUNT_\(key) }}",
            "          GLADOS_ACCOUNT_KEY: '\(key)'", "          GLADOS_AUTO_EXCHANGE: '\(autoExchange)'",
            "          GLADOS_EXCHANGE_CATALOG: '.github/glados/exchange_plans.json'",
            "          PUSHDEER_SENDKEY: ${{ secrets.PUSHDEER_SENDKEY }}", "          GLADOS_VERBOSE: 'true'",
            "        run: python checkin.py", ""
        ]
    }

    static func statusJob(key: String) -> [String] {
        [
            "  account_\(key.lowercased()):", "    name: GLaDOS status \(key)",
            "    if: ${{ inputs.account == 'all' || inputs.account == '\(key)' }}", "    runs-on: ubuntu-latest", "    timeout-minutes: 3", "    steps:",
            "      - uses: actions/checkout@v6", "      - uses: actions/setup-python@v6", "        with:", "          python-version: '3.13'",
            "      - run: pip install -r requirements.txt", "      - name: Read account status", "        env:",
            "          GLADOS_COOKIES: ${{ secrets.GLADOS_ACCOUNT_\(key) }}", "          GLADOS_ACCOUNT_KEY: '\(key)'", "        run: python status.py", ""
        ]
    }
}

@MainActor
final class AppModel: ObservableObject {
    @Published var config = RepositoryConfig()
    @Published var catalog: PlanCatalog?
    @Published var statuses: [String: AccountStatus] = [:]
    @Published var runs: [RunInfo] = []
    @Published var workflowReceipts: [WorkflowRunReceipt] = []
    @Published var checkinWatch: CheckinWatchStatus?
    @Published var busyMessage: String?
    @Published var errorMessage: String?
    @Published var infoMessage: String?
    @Published var pendingCapture: CapturePayload?
    @Published var browserOptions: [BrowserOption] = []
    @Published var showBrowserPicker = false
    @Published var isGitHubReady = false
    @Published var githubConnectionIssue: String?
    @Published var scheduleDates: [Date] = []
    @Published var localAccounts: [String: ManualAccountRecord] = [:]
    @Published var transferRequest: AccountTransferRequest?
    @Published var emailEditRequest: AccountEmailEditRequest?

    private let encoder: JSONEncoder = { let e = JSONEncoder(); e.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]; return e }()
    private let decoder = JSONDecoder()
    private var client: GitHubClient?
    private let captureService = CaptureService()
    private var localStore: LocalManualAccountStore?
    private var captureExpectedAccountKey: String?

    init() {
        loadStatusCache()
        loadRepositoryCache()
        do {
            let store = try LocalManualAccountStore()
            try store.migrateLegacyEmails()
            localStore = store
            try updateLocalSummaries()
            for status in statuses.values where status.ok && !status.email.isEmpty {
                try? store.rememberEmail(key: status.accountKey, email: status.email, verified: true)
            }
            localAccounts = try store.records()
            workflowReceipts = try store.workflowRecords()
        } catch { errorMessage = error.localizedDescription }
        Task { await bootstrap() }
    }

    var accountsSorted: [(String, AccountConfig)] {
        var all = config.accounts
        for (key, record) in localAccounts where record.visible && all[key] == nil {
            all[key] = AccountConfig(enabled: record.enabled, autoExchange: record.autoExchange, label: record.label)
        }
        return all.keys.sorted {
            let left = accountDisplayName($0), right = accountDisplayName($1)
            return left == right ? $0 < $1 : left.localizedStandardCompare(right) == .orderedAscending
        }.compactMap { key in all[key].map { (key, $0) } }
    }
    func accountDisplayName(_ key: String) -> String {
        let email = localAccounts[key]?.email ?? ""
        return email.isEmpty ? "待补邮箱" : email
    }
    func isCloudAccount(_ key: String) -> Bool { config.accounts[key] != nil }
    private func knownSessionHost(_ key: String) -> String? {
        if let host = localAccounts[key]?.sessionHost, ManualValidation.allowedHosts.contains(host) { return host }
        if let status = statuses[key], status.ok, ManualValidation.allowedHosts.contains(status.domain) { return status.domain }
        return nil
    }
    func editEmail(_ key: String) { errorMessage = nil; emailEditRequest = AccountEmailEditRequest(key: key, email: localAccounts[key]?.email ?? "") }
    func saveEmail(_ key: String, email: String) {
        do {
            guard let localStore else { throw ManualAccountError.storage }
            try localStore.rememberEmail(key: key, email: email, verified: false, allowManualChange: true)
            localAccounts = try localStore.records(); emailEditRequest = nil
        } catch { errorMessage = error.localizedDescription }
    }
    private func updateLocalSummaries() throws {
        guard let localStore else { return }
        let summaries = config.accounts.map { key, value in
            ManualAccountRecord(accountKey: key, label: value.label, enabled: value.enabled, autoExchange: value.autoExchange)
        }
        try localStore.rememberSummaries(summaries)
        localAccounts = try localStore.records()
    }
    var healthyCount: Int { statuses.values.filter(\.ok).count }
    var exchangeEnabledCount: Int { config.accounts.values.filter(\.autoExchange).count }
    var enabledCount: Int { config.accounts.values.filter(\.enabled).count }

    func bootstrap() async {
        githubConnectionIssue = nil
        busyMessage = "正在连接 GitHub…"
        await Task.yield()
        let receiptStore = localStore
        do {
            let result = try await Self.background { () -> (GitHubClient, RepositorySnapshot) in
                let client = try GitHubClient(receiptStore: receiptStore)
                try client.ensureReady()
                return (client, try Self.fetchRepositoryState(client))
            }
            self.client = result.0
            try self.applySnapshot(result.1)
            self.isGitHubReady = true
        } catch {
            self.client = nil
            self.isGitHubReady = false
            self.githubConnectionIssue = error.localizedDescription
        }
        busyMessage = nil
        if isGitHubReady && shouldAutoRefresh { await refreshAllStatuses() }
    }

    func retryGitHubConnection(showFailure: Bool = true) async {
        await bootstrap()
        if showFailure, !isGitHubReady { errorMessage = githubConnectionIssue ?? "GitHub 连接失败。" }
    }

    func startGitHubLogin() {
        guard let gh = ProcessRunner.executable("gh") else {
            errorMessage = "未找到 GitHub CLI。请先运行交付包中的“安装依赖.command”。"
            return
        }
        do {
            let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
                .appendingPathComponent("GLaDOS Account Center", isDirectory: true)
            try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
            let scriptURL = base.appendingPathComponent("连接 GitHub.command")
            let script = """
            #!/bin/zsh
            set -e
            clear
            echo "GLaDOS Account Center · GitHub 授权"
            echo ""
            "\(gh.path)" auth login --hostname github.com --web --git-protocol https --scopes repo,workflow
            "\(gh.path)" auth refresh -h github.com -s workflow || true
            echo ""
            "\(gh.path)" auth status --active --hostname github.com
            echo ""
            echo "授权完成。现在可以回到 GLaDOS Account Center，点击“重新检测”。"
            printf "按任意键关闭此窗口…"
            read -k 1
            """
            try script.write(to: scriptURL, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: scriptURL.path)
            NSWorkspace.shared.open(scriptURL)
            infoMessage = "已打开 GitHub 官方网页登录授权。完成后回到应用，点击“重新检测”。授权令牌由 GitHub CLI 保存到系统凭据存储，本应用不会读取或保存令牌明文。"
        } catch {
            errorMessage = "无法启动 GitHub 授权：\(error.localizedDescription)"
        }
    }

    var shouldAutoRefresh: Bool {
        let last = UserDefaults.standard.object(forKey: "lastStatusRefresh") as? Date
        return last == nil || Date().timeIntervalSince(last!) > 15 * 60
    }

    func reloadRepository() async {
        guard let client else { return }
        await perform("正在同步账号配置…") {
            let snapshot = try await Self.background { try Self.fetchRepositoryState(client) }
            try self.applySnapshot(snapshot)
        }
    }

    func refreshAllStatuses(userInitiated: Bool = false) async { await refreshStatuses(account: "all", userInitiated: userInitiated) }
    func refreshStatus(key: String, userInitiated: Bool = false) async { await refreshStatuses(account: key, userInitiated: userInitiated) }

    private func refreshStatuses(account: String, userInitiated: Bool = false) async {
        guard let client else { return }
        await perform(account == "all" ? "正在只读刷新全部 GLaDOS 状态…" : "正在只读刷新账号状态…") {
            let output = try await Self.background { () -> (WorkflowRunReceipt, String) in
                let receipt = try client.triggerWorkflow(statusWorkflowName, account: account, replaceUnidentifiedStatus: userInitiated)
                let (_, _, logs) = try client.waitForRun(receipt, timeout: 360)
                return (receipt, logs)
            }
            try self.applyStatusLogs(output.1, receipt: output.0)
        }
    }

    private func applyStatusLogs(_ logs: String, receipt: WorkflowRunReceipt) throws {
        let parsed = parseStatusLogs(logs)
        guard !parsed.isEmpty, parsed.allSatisfy({ status in
            (try? ManualValidation.key(status.accountKey)) != nil && (receipt.account == "all" || status.accountKey == receipt.account)
        }) else {
            var pending = receipt; pending.queryState = .interrupted
            try localStore?.saveWorkflow(pending)
            throw WorkflowReceiptError.protocolError
        }
        for status in parsed {
            statuses[status.accountKey] = status
            if status.ok, !status.email.isEmpty {
                try localStore?.rememberEmail(key: status.accountKey, email: status.email, verified: true)
            }
        }
        if let store = localStore { localAccounts = try store.records() }
        saveStatusCache()
        UserDefaults.standard.set(Date(), forKey: "lastStatusRefresh")
    }

    func latestReceipt(for key: String) -> WorkflowRunReceipt? {
        workflowReceipts.first { $0.account == key && $0.supersededBy == nil }
    }
    func accountStageText(_ key: String) -> String? {
        guard let record = localAccounts[key], record.hasCredentials else { return nil }
        let read = record.readState == "imported" ? "已导入本机" : "已读取并保存本机"
        let publication: String
        switch record.publicationState {
        case "acknowledged": publication = "发布已确认"
        case "pending", "uncertain": publication = "发布结果待核对"
        case "skipped": publication = "已有账号，发布已跳过"
        default: publication = record.pendingPublication ? "待手动发布" : "发布回执未记录"
        }
        let verification = workflowReceipts.first { $0.account == key && $0.workflow == statusWorkflowName && $0.credentialID == record.credentialID }
        return "\(read) · \(publication) · \(verification?.stateText ?? "云端资料待查询")"
    }

    func retryExistingWorkflow(_ original: WorkflowRunReceipt) async {
        guard let client, original.supersededBy == nil else { return }
        if original.workflow == statusWorkflowName, original.runID == nil {
            await refreshStatuses(account: original.account, userInitiated: true)
            return
        }
        var suppliedID: Int?
        if original.runID == nil {
            let alert = NSAlert(); alert.messageText = "重查原任务"
            alert.informativeText = "请输入此次派发在 GitHub 运行记录中的任务编号。应用会核对工作流、分支、时间和账号，且不会重新派发。"
            let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
            field.placeholderString = "GitHub Run ID"; alert.accessoryView = field
            alert.addButton(withTitle: "核对并重查"); alert.addButton(withTitle: "取消")
            guard alert.runModal() == .alertFirstButtonReturn else { return }
            do { suppliedID = try WorkflowRunRules.runID(field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)) }
            catch { errorMessage = error.localizedDescription; return }
        }
        let selectedID = suppliedID
        await perform("正在只读重查已有任务…") {
            let result = try await Self.background { () -> (WorkflowRunReceipt, String, String) in
                let receipt = try selectedID.map { try client.bindExistingRun($0, to: original) } ?? original
                let (conclusion, _, logs) = try client.waitForRun(receipt, timeout: 360)
                return (receipt, conclusion, logs)
            }
            if result.0.workflow == statusWorkflowName { try self.applyStatusLogs(result.2, receipt: result.0) }
            self.infoMessage = "原任务查询完成：\(result.1)。未重新读取登录信息、发布凭据或派发任务。"
        }
    }

    func refreshCheckinWatch() async {
        do { checkinWatch = try await Self.background { try CheckinWatchStatus.request("status") } }
        catch { checkinWatch = nil }
    }
    func updateCheckinWatch(_ action: String, enabled: Bool? = nil) async {
        await perform("正在更新签到失败通知设置…") {
            let status = try await Self.background { try CheckinWatchStatus.request(action, enabled: enabled) }
            self.checkinWatch = status
            guard status.ok else { throw AppError.message(status.error ?? "签到失败通知设置未完成。") }
            if action == "test" { self.infoMessage = "测试通知已提交给通知组件；是否显示取决于系统通知权限。" }
        }
    }

    func startCapture(expectedAccountKey: String? = nil) async {
        captureExpectedAccountKey = expectedAccountKey
        browserOptions = captureService.availableBrowsers()
        if browserOptions.isEmpty {
            errorMessage = "没有找到受支持的浏览器。支持 Safari、Chrome、Edge、Brave、Arc、Firefox、Opera、Opera GX、Vivaldi 和 Chromium。"
            return
        }
        showBrowserPicker = true
    }

    func capture(using browser: BrowserOption) async {
        showBrowserPicker = false
        let service = captureService
        let expected = captureExpectedAccountKey
        let host = expected.flatMap { knownSessionHost($0) }
        await perform("正在通过 \(browser.label) 读取 GLaDOS 登录信息…") {
            let captured = try await Task.detached(priority: .userInitiated) { try service.capture(browserID: browser.id, expectedAccountKey: expected, expectedHost: host) }.value
            guard let payload = captured else { return }
            let session = try payload.manualSession()
            if let oldEmail = self.localAccounts[session.accountKey]?.email, !oldEmail.isEmpty, oldEmail != session.email { throw ManualAccountError.conflict }
            self.pendingCapture = payload
        }
    }

    func commitCapture(autoExchange: Bool, enabled: Bool) async {
        guard let capture = pendingCapture else { return }
        guard let store = localStore else { errorMessage = ManualAccountError.storage.localizedDescription; return }
        let client = self.client
        let label = config.accounts[capture.accountKey]?.label ?? localAccounts[capture.accountKey]?.label ?? "GLaDOS \(capture.accountKey.prefix(6))"
        var published = false
        await perform("正在将手动登录资料保存到本机…") {
            let session = try capture.manualSession()
            if let expected = self.captureExpectedAccountKey, session.accountKey != expected { throw ManualAccountError.conflict }
            try await Self.background { try store.saveCapture(session, label: label, enabled: enabled, autoExchange: autoExchange, pendingPublication: true) }
            self.localAccounts = try store.records()
            self.pendingCapture = nil
            self.captureExpectedAccountKey = nil
            guard let client else {
                self.infoMessage = "登录资料已保存在本机钥匙串。连接 GitHub 后可手动同步该账号。"
                return
            }
            self.busyMessage = "本机已保存，正在同步此次手动登录资料…"
            let next = try await Self.background { () -> RepositoryConfig in
                let latest = try Self.readConfiguration(client)
                var next = latest
                let prior = latest.accounts[session.accountKey]
                next.accounts[session.accountKey] = AccountConfig(enabled: enabled, autoExchange: autoExchange, label: prior?.label ?? ManualValidation.safeLabel(label, key: session.accountKey))
                try client.setSecret(name: "GLADOS_ACCOUNT_\(session.accountKey)", value: String(decoding: session.encoded(), as: UTF8.self))
                if prior == nil {
                    try Self.appendConfiguration(next, addedKeys: [session.accountKey], expected: latest, client: client)
                } else if next != latest {
                    try Self.writeConfiguration(next, client: client, message: "Update manually selected GLaDOS account settings")
                }
                try store.markPublished([session.accountKey])
                return next
            }
            self.config = next; try self.updateLocalSummaries(); self.saveRepositoryCache(); self.syncScheduleDates()
            published = true
            self.infoMessage = "此次手动登录资料已保存在本机并同步。邮箱将一直保留；后续不会自动登录或更新 Cookie。"
        }
        if published { await refreshStatus(key: capture.accountKey) }
    }

    func cancelCapture() { pendingCapture = nil; captureExpectedAccountKey = nil }

    func chooseImportFile() {
        let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.title = "导入 GLaDOS 账号备份"; panel.message = "选择从 Account Center 导出的加密备份文件。"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        errorMessage = nil
        transferRequest = AccountTransferRequest(kind: .importFile, url: url)
    }

    func chooseExportFile() {
        let panel = NSSavePanel(); panel.title = "导出 GLaDOS 账号备份"
        panel.nameFieldStringValue = "GLaDOS-accounts.gladosbackup"
        panel.message = "文件将使用你设置的密码加密，包含本机已保存的登录资料。"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        errorMessage = nil
        transferRequest = AccountTransferRequest(kind: .exportFile, url: url)
    }

    func exportAccounts(to url: URL, password: String) async {
        guard let store = localStore else { errorMessage = ManualAccountError.storage.localizedDescription; return }
        await perform("正在加密导出本地账号资料…") {
            let counts = try await Self.background { () -> (Int, Int) in
                let contents = try store.archive()
                let encrypted = try ManualArchiveCrypto.encrypt(contents, password: password)
                try encrypted.write(to: url, options: .atomic)
                try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
                return (contents.accounts.count, contents.accounts.filter { $0.session != nil }.count)
            }
            self.transferRequest = nil
            self.infoMessage = "已加密导出 \(counts.0) 个账号，其中 \(counts.1) 个包含本机登录凭据。其余仅有邮箱/设置；GitHub Secret 无法读回，需手动重新读取后才能补齐。"
        }
    }

    func importAccounts(from url: URL, password: String) async {
        guard let store = localStore else { errorMessage = ManualAccountError.storage.localizedDescription; return }
        let client = self.client; let cached = config
        await perform("正在解密、验证并核对重复账号…") {
            let result = try await Self.background { () -> (ManualImportPlan, [ManualArchiveAccount], RepositoryConfig) in
                let info = try url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
                guard info.isRegularFile == true, (info.fileSize ?? Int.max) <= 32 * 1024 * 1024 else { throw ManualAccountError.password }
                let contents = try ManualArchiveCrypto.decrypt(Data(contentsOf: url), password: password)
                let latest = try client.map { try Self.readConfiguration($0) } ?? cached
                let remoteKeys = try client.map { try $0.managedAccountKeys() } ?? Set<String>()
                let external = Set(latest.accounts.keys).union(remoteKeys)
                let records = try store.records()
                let plan = try ManualImportPlan.make(contents, existing: records.mapValues(\.email), additionalKeys: external)
                if plan.additions.isEmpty { return (plan, [], latest) }
                let added = try store.applyImport(plan, externalKeys: external)
                return (plan, added, latest)
            }
            self.localAccounts = try store.records()
            self.config = result.2; self.saveRepositoryCache()
            self.transferRequest = nil
            let complete = result.1.filter { $0.session != nil }
            if let client, !complete.isEmpty {
                self.busyMessage = "账号已保存在本机，正在添加新的云端签到任务…"
                do {
                    let next = try await Self.background { try Self.publishNewAccounts(complete, store: store, client: client) }
                    self.config = next; try self.updateLocalSummaries(); self.saveRepositoryCache(); self.syncScheduleDates()
                } catch {
                    self.localAccounts = try store.records()
                    throw AppError.message("新账号已保存在本机，云端同步尚未完成；原账号未被覆盖。可在账号卡片点“同步本机资料”。\n" + error.localizedDescription)
                }
            }
            let skipped = result.0.skipped + result.0.additions.count - result.1.count
            let metadataOnly = result.1.filter { $0.session == nil }.count
            let cloudSkipped = complete.filter { self.localAccounts[$0.accountKey]?.publicationSkipped == true }.count
            self.infoMessage = "本机新增 \(result.1.count) 个账号，跳过 \(skipped) 个已记录/重复账号。" +
                (metadataOnly > 0 ? "其中 \(metadataOnly) 个缺少本机登录凭据，已在账号列表显示为待补登录信息。" : "") +
                (cloudSkipped > 0 ? "同步前发现 \(cloudSkipped) 个账号已有云端记录，已跳过同步并保留本机备份，未覆盖云端登录信息。" : "") +
                (client == nil && !complete.isEmpty ? "连接 GitHub 后，可逐账号手动同步本机资料。" : "")
        }
    }

    func synchronizeLocalAccount(_ key: String) async {
        guard let store = localStore, let client else { errorMessage = "请先连接本机 GitHub。"; return }
        await perform("正在同步此账号的本机资料…") {
            let next = try await Self.background { () -> RepositoryConfig in
                guard let record = try store.records()[key], let session = try store.session(for: key) else { throw AppError.message("此账号缺少本机登录信息，请手动重新读取。") }
                let latest = try Self.readConfiguration(client)
                if record.allowsManualCredentialReplacement {
                    // The user explicitly captured this exact account. Restoring
                    // an imported backup never grants permission to overwrite it.
                    try client.setSecret(name: "GLADOS_ACCOUNT_\(key)", value: String(decoding: session.encoded(), as: UTF8.self))
                    var next = latest
                    if latest.accounts[key] == nil {
                        next.accounts[key] = AccountConfig(enabled: record.enabled, autoExchange: record.autoExchange, label: ManualValidation.safeLabel(record.label, key: key))
                        try Self.appendConfiguration(next, addedKeys: [key], expected: latest, client: client)
                    }
                    try store.markPublished([key]); return next
                }
                guard record.pendingPublication, record.publicationSkipped != true else { return latest }
                let account = ManualArchiveAccount(accountKey: key, email: record.email, emailVerified: record.emailVerified, label: record.label, enabled: record.enabled, autoExchange: record.autoExchange, session: session)
                return try Self.publishNewAccounts([account], store: store, client: client)
            }
            self.config = next; try self.updateLocalSummaries(); self.saveRepositoryCache(); self.syncScheduleDates()
            self.infoMessage = self.localAccounts[key]?.publicationSkipped == true
                ? "已发现此账号的云端记录，跳过导入同步；本机备份已保留，云端登录信息未被覆盖。需要更换登录信息时，请明确选择“重新读取登录信息”。"
                : "该账号的本机资料已同步；其他账号和签到时间保持原样。"
        }
    }

    nonisolated private static func publishNewAccounts(_ accounts: [ManualArchiveAccount], store: LocalManualAccountStore, client: GitHubClient) throws -> RepositoryConfig {
        let latest = try readConfiguration(client)
        let registered = Set(latest.accounts.keys).union(try client.managedAccountKeys())
        var next = latest; var added = Set<String>(); var skipped = Set<String>()
        for raw in accounts {
            let account = try raw.validated()
            // Recheck names before publication as well as during initial import:
            // a Secret can exist before its accounts.json entry is committed.
            if registered.contains(account.accountKey) { skipped.insert(account.accountKey); continue }
            guard let session = account.session else { continue }
            guard let saved = try store.session(for: account.accountKey), saved == session else { throw ManualAccountError.conflict }
            if try client.managedAccountKeys().contains(account.accountKey) { skipped.insert(account.accountKey); continue }
            try client.setSecret(name: "GLADOS_ACCOUNT_\(account.accountKey)", value: String(decoding: session.encoded(), as: UTF8.self))
            next.accounts[account.accountKey] = AccountConfig(enabled: account.enabled, autoExchange: account.autoExchange, label: ManualValidation.safeLabel(account.label, key: account.accountKey))
            added.insert(account.accountKey)
        }
        try store.markPublicationSkipped(skipped)
        if !added.isEmpty { try appendConfiguration(next, addedKeys: added, expected: latest, client: client) }
        try store.markPublished(added)
        return next
    }

    func setEnabled(_ key: String, _ value: Bool) async {
        guard let client else { return }
        var next = config; guard var item = next.accounts[key] else { return }
        item.enabled = value; next.accounts[key] = item
        await perform(value ? "正在开启自动签到…" : "正在暂停自动签到…") {
            try await Self.background { try Self.writeConfiguration(next, client: client, message: value ? "Enable GLaDOS account check-in" : "Disable GLaDOS account check-in") }
            self.config = next
            try self.updateLocalSummaries(); self.saveRepositoryCache()
        }
    }

    func setAutoExchange(_ key: String, _ value: Bool) async {
        guard let client else { return }
        var next = config; guard var item = next.accounts[key] else { return }
        item.autoExchange = value; next.accounts[key] = item
        await perform(value ? "正在开启最优积分兑换…" : "正在关闭积分兑换…") {
            try await Self.background { try Self.writeConfiguration(next, client: client, message: value ? "Enable optimal exchange for GLaDOS account" : "Disable GLaDOS account exchange") }
            self.config = next
            try self.updateLocalSummaries(); self.saveRepositoryCache()
        }
    }

    func renameAccount(_ key: String, label: String) async {
        guard let client else { return }
        let clean = label.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty, clean.count <= 80, !clean.contains("@") else { errorMessage = "备注不能为空、最多 80 字；邮箱请通过“账号邮箱”入口保存在本机。"; return }
        var next = config; guard var item = next.accounts[key] else { return }
        item.label = clean; next.accounts[key] = item
        await perform("正在更新账号名称…") {
            try await Self.background { try Self.writeConfiguration(next, client: client, message: "Rename GLaDOS account label") }
            self.config = next
            try self.updateLocalSummaries(); self.saveRepositoryCache()
        }
    }

    func upsertVerifiedPlan(planID: String, points: Int, days: Int) async {
        guard let client, var current = catalog else { return }
        let cleanID = planID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard cleanID.range(of: "^plan[A-Za-z0-9_-]+$", options: .regularExpression) != nil else { errorMessage = "方案 ID 必须以 plan 开头，只能包含字母、数字、_ 或 -。"; return }
        guard points > 0, days > 0 else { errorMessage = "积分和天数必须大于 0。"; return }
        if let index = current.plans.firstIndex(where: { $0.id == cleanID }) {
            current.plans[index] = ExchangePlan(id: cleanID, points: points, days: days, verified: true)
        } else {
            current.plans.append(ExchangePlan(id: cleanID, points: points, days: days, verified: true))
        }
        current.updatedAt = Self.todayString()
        await perform("正在保存已验证兑换方案…") {
            try await Self.background { try Self.writeCatalog(current, client: client, message: "Update verified GLaDOS exchange plan catalog") }
            self.catalog = current
            if let best = current.bestPlan { self.infoMessage = "兑换方案已更新。当前最优：\(best.points) 积分 → \(best.days) 天。" }
        }
    }

    func setPlanVerified(_ planID: String, _ verified: Bool) async {
        guard let client, var current = catalog else { return }
        guard let index = current.plans.firstIndex(where: { $0.id == planID }) else { return }
        if !verified && current.plans.filter({ $0.verified && $0.id != planID }).isEmpty {
            errorMessage = "至少需要保留一个已验证兑换方案；如不想兑换，请关闭账号的自动兑换开关。"
            return
        }
        current.plans[index].verified = verified
        current.updatedAt = Self.todayString()
        await perform(verified ? "正在启用兑换方案…" : "正在停用兑换方案…") {
            try await Self.background { try Self.writeCatalog(current, client: client, message: verified ? "Verify GLaDOS exchange plan" : "Disable GLaDOS exchange plan") }
            self.catalog = current
        }
    }

    private static func todayString() -> String {
        let formatter = DateFormatter(); formatter.calendar = Calendar(identifier: .gregorian); formatter.locale = Locale(identifier: "en_US_POSIX"); formatter.timeZone = TimeZone(identifier: "Asia/Taipei"); formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: Date())
    }

    func runCheckin(_ key: String) async {
        guard let client else { return }
        var completed = false
        await perform("正在手动执行该账号签到…") {
            let output = try await Self.background { () -> (String, String) in
                let receipt = try client.triggerWorkflow(checkinWorkflowName, account: key)
                let (conclusion, url, _) = try client.waitForRun(receipt)
                return (conclusion, url)
            }
            completed = true
            self.infoMessage = "手动签到已完成：\(output.0)"
            if let target = URL(string: output.1) { NSWorkspace.shared.open(target) }
            self.runs = (try? await Self.background { try client.recentRuns(workflow: checkinWorkflowName) }) ?? self.runs
        }
        if completed { await refreshStatus(key: key) }
    }

    func deleteAutomation(_ key: String) async {
        guard let client else { return }
        var next = config; next.accounts.removeValue(forKey: key)
        await perform("正在移除账号自动化配置…") {
            try await Self.background {
                try Self.writeConfiguration(next, client: client, message: "Remove GLaDOS account automation")
                try client.deleteSecret(name: "GLADOS_ACCOUNT_\(key)")
            }
            self.config = next
            self.statuses.removeValue(forKey: key)
            try self.localStore?.hide(key)
            if let store = self.localStore { self.localAccounts = try store.records() }
            self.saveRepositoryCache()
            self.saveStatusCache()
            self.infoMessage = "已从自动化中移除该账号；不会删除 GLaDOS 网站账户。"
        }
    }

    func saveSchedule() async {
        guard let client, scheduleDates.count >= 2 else { return }
        let formatter = DateFormatter(); formatter.dateFormat = "HH:mm"; formatter.timeZone = TimeZone(identifier: "Asia/Taipei")
        let times = scheduleDates.map { formatter.string(from: $0) }.sorted()
        var next = config; next.schedule.timezone = "Asia/Taipei"; next.schedule.times = times
        await perform("正在更新签到时间…") {
            try await Self.background { try Self.writeConfiguration(next, client: client, message: "Update GLaDOS check-in schedule") }
            self.config = next
            self.saveRepositoryCache()
            self.syncScheduleDates()
            self.infoMessage = "签到时间已更新为台湾时间：\(times.joined(separator: "、"))"
        }
    }

    func restoreRecommendedSchedule() { scheduleDates = [dateForTime("05:00"), dateForTime("17:00")] }

    func openRun(_ run: RunInfo) { if let url = URL(string: run.url) { NSWorkspace.shared.open(url) } }
    func openRepository() { if let url = URL(string: "https://github.com/\(defaultRepository)") { NSWorkspace.shared.open(url) } }
    func openGlados(accountKey: String? = nil) {
        guard let accountKey else { if let url = URL(string: "https://glados.cloud/console/checkin") { NSWorkspace.shared.open(url) }; return }
        do {
            let key = try ManualValidation.key(accountKey)
            let home = FileManager.default.homeDirectoryForCurrentUser
            let candidates = [URL(fileURLWithPath: "/Applications/Microsoft Edge.app"), home.appendingPathComponent("Applications/Microsoft Edge.app"), URL(fileURLWithPath: "/Volumes/MacData/Applications/Microsoft Edge.app")]
            guard let browser = candidates.first(where: { FileManager.default.fileExists(atPath: $0.path) }) else {
                throw AppError.message("未找到 Microsoft Edge。账号专属网页登录需要原有的独立 Edge 资料目录；没有打开其他账号的浏览器会话。")
            }
            let profile = home.appendingPathComponent("Library/Application Support/GLaDOS Account Center/BrowserProfiles/accounts/\(key)/edge", isDirectory: true)
            try FileManager.default.createDirectory(at: profile, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let process = Process(); process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
            let host = knownSessionHost(key) ?? "glados.cloud"
            process.arguments = ["-na", browser.path, "--args", "--user-data-dir=\(profile.path)", "--no-first-run", "--no-default-browser-check", "https://\(host)/console/checkin"]
            try process.run()
        } catch { errorMessage = error.localizedDescription }
    }

    private func applySnapshot(_ snapshot: RepositorySnapshot) throws {
        config = snapshot.config
        catalog = snapshot.catalog
        runs = snapshot.runs
        try updateLocalSummaries()
        saveRepositoryCache()
        syncScheduleDates()
    }

    nonisolated private static func background<T: Sendable>(_ operation: @escaping @Sendable () throws -> T) async throws -> T {
        try await Task.detached(priority: .userInitiated, operation: operation).value
    }

    nonisolated private static func fetchRepositoryState(_ client: GitHubClient) throws -> RepositorySnapshot {
        let configText = try client.readTextFile(accountsConfigPath).0
        let catalogText = try client.readTextFile(exchangeCatalogPath).0
        let decoder = JSONDecoder()
        let config = try decoder.decode(RepositoryConfig.self, from: Data(configText.utf8))
        let catalog = try decoder.decode(PlanCatalog.self, from: Data(catalogText.utf8))
        let runs = try client.recentRuns(workflow: checkinWorkflowName)
        return RepositorySnapshot(config: config, catalog: catalog, runs: runs)
    }

    nonisolated private static func writeCatalog(_ catalog: PlanCatalog, client: GitHubClient, message: String) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(catalog)
        guard let text = String(data: data, encoding: .utf8) else { throw AppError.message("无法编码兑换方案目录。") }
        try client.upsertTextFile(exchangeCatalogPath, content: text + "\n", message: message)
    }

    nonisolated private static func readConfiguration(_ client: GitHubClient) throws -> RepositoryConfig {
        let value = try JSONDecoder().decode(RepositoryConfig.self, from: Data(client.readTextFile(accountsConfigPath).0.utf8))
        guard value.accounts.count <= 1000 else { throw AppError.message("账号配置数量无效。") }
        for key in value.accounts.keys { guard try ManualValidation.key(key) == key else { throw AppError.message("账号配置标识无效。") } }
        return value
    }

    nonisolated private static func appendConfiguration(_ next: RepositoryConfig, addedKeys: Set<String>, expected: RepositoryConfig, client: GitHubClient) throws {
        var withoutNew = next
        for key in addedKeys {
            guard try ManualValidation.key(key) == key, expected.accounts[key] == nil, next.accounts[key] != nil else { throw ManualAccountError.conflict }
            withoutNew.accounts.removeValue(forKey: key)
        }
        guard withoutNew == expected else { throw AppError.message("新增账号不能改变既有账号设置或签到时间。") }
        let previousConfig = try client.readTextFile(accountsConfigPath).0
        guard try JSONDecoder().decode(RepositoryConfig.self, from: Data(previousConfig.utf8)) == expected else {
            throw AppError.message("账号配置已改变；新资料保存在本机，未覆盖最新设置。")
        }
        let previousCheckin = try client.readTextFile(checkinWorkflowPath).0
        let previousStatus = try client.readTextFile(statusWorkflowPath).0
        guard previousCheckin.contains("\njobs:\n"), previousStatus.contains("\njobs:\n") else { throw AppError.message("现有工作流结构不兼容，未修改任务。") }
        var checkin = previousCheckin + (previousCheckin.hasSuffix("\n") ? "" : "\n")
        var status = previousStatus + (previousStatus.hasSuffix("\n") ? "" : "\n")
        for key in addedKeys.sorted() {
            let job = "  account_\(key.lowercased()):"
            guard !previousCheckin.components(separatedBy: .newlines).contains(job), !previousStatus.components(separatedBy: .newlines).contains(job) else { throw AppError.message("该账号已有工作流记录，已停止重复添加。") }
            if next.accounts[key]?.enabled == true {
                checkin += "\n" + WorkflowBuilder.accountJob(key: key, autoExchange: next.accounts[key]?.autoExchange == true ? "true" : "false").joined(separator: "\n")
            }
            status += "\n" + WorkflowBuilder.statusJob(key: key).joined(separator: "\n")
        }
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        let configText = String(decoding: try encoder.encode(next), as: UTF8.self) + "\n"
        try client.atomicWriteTextFiles([accountsConfigPath: configText, checkinWorkflowPath: checkin, statusWorkflowPath: status],
                                       expected: [accountsConfigPath: previousConfig, checkinWorkflowPath: previousCheckin, statusWorkflowPath: previousStatus],
                                       message: "Add manually imported GLaDOS accounts without changing existing settings")
    }

    nonisolated private static func writeConfiguration(_ next: RepositoryConfig, client: GitHubClient, message: String) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(next)
        guard let json = String(data: data, encoding: .utf8) else { throw AppError.message("无法编码账号配置。") }

        // Keep related files in one commit; never roll back over concurrent edits.
        let previousCheckin = try client.readTextFile(checkinWorkflowPath).0
        let previousLegacy = try client.readTextFile(legacyWorkflowPath).0
        let previousStatus = try client.readTextFile(statusWorkflowPath).0
        let previousConfig = try client.readTextFile(accountsConfigPath).0
        let nextCheckin = WorkflowBuilder.checkin(config: next)
        let nextLegacy = WorkflowBuilder.legacy()
        let nextStatus = WorkflowBuilder.status(config: next)
        let nextConfig = json + "\n"
        try client.atomicWriteTextFiles([accountsConfigPath: nextConfig, checkinWorkflowPath: nextCheckin, legacyWorkflowPath: nextLegacy, statusWorkflowPath: nextStatus],
                                       expected: [accountsConfigPath: previousConfig, checkinWorkflowPath: previousCheckin, legacyWorkflowPath: previousLegacy, statusWorkflowPath: previousStatus], message: message)
    }

    private func parseStatusLogs(_ logs: String) -> [AccountStatus] {
        logs.split(separator: "\n").compactMap { line in
            guard let range = line.range(of: "GLADOS_STATUS_JSON=") else { return nil }
            let json = String(line[range.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
            return try? decoder.decode(AccountStatus.self, from: Data(json.utf8))
        }
    }

    private func statusCacheURL() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let dir = base.appendingPathComponent("GLaDOS Account Center", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("status-cache.json")
    }

    private func loadStatusCache() {
        guard let data = try? Data(contentsOf: statusCacheURL()), let items = try? decoder.decode([AccountStatus].self, from: data) else { return }
        for item in items { if (try? ManualValidation.key(item.accountKey)) == item.accountKey { statuses[item.accountKey] = item } }
    }

    private func saveStatusCache() {
        if let data = try? encoder.encode(Array(statuses.values)) { try? data.write(to: statusCacheURL(), options: .atomic) }
    }

    private func repositoryCacheURL() -> URL { statusCacheURL().deletingLastPathComponent().appendingPathComponent("repository-cache.json") }
    private func loadRepositoryCache() {
        let url = repositoryCacheURL()
        guard let info = try? url.resourceValues(forKeys: [.fileSizeKey]), (info.fileSize ?? Int.max) < 2 * 1024 * 1024,
              let data = try? Data(contentsOf: url), let saved = try? decoder.decode(RepositoryConfig.self, from: data),
              saved.accounts.keys.allSatisfy({ (try? ManualValidation.key($0)) == $0 }) else { return }
        config = saved; syncScheduleDates()
    }
    private func saveRepositoryCache() {
        let url = repositoryCacheURL()
        if let data = try? encoder.encode(config) {
            try? data.write(to: url, options: .atomic)
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        }
    }

    private func syncScheduleDates() { scheduleDates = config.schedule.times.map(dateForTime) }
    private func dateForTime(_ text: String) -> Date {
        let parts = text.split(separator: ":").compactMap { Int($0) }
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = TimeZone(identifier: "Asia/Taipei")!
        var comps = calendar.dateComponents([.year, .month, .day], from: Date())
        comps.hour = parts.first ?? 5; comps.minute = parts.count > 1 ? parts[1] : 0; comps.second = 0
        return calendar.date(from: comps) ?? Date()
    }

    private func perform(_ message: String, operation: @escaping () async throws -> Void) async {
        guard busyMessage == nil else { return }
        errorMessage = nil
        busyMessage = message
        await Task.yield()
        do { try await operation() }
        catch { errorMessage = error.localizedDescription }
        if let store = localStore {
            do { localAccounts = try store.records(); workflowReceipts = try store.workflowRecords() }
            catch { errorMessage = error.localizedDescription }
        }
        busyMessage = nil
    }
}

enum SidebarRoute: String, CaseIterable, Identifiable {
    case overview = "概览", accounts = "账号", punches = "签到日历", runs = "运行记录", plans = "积分方案", settings = "设置"
    var id: String { rawValue }
    var icon: String {
        switch self {
        case .overview: return "square.grid.2x2"
        case .accounts: return "person.2"
        case .punches: return "calendar.badge.checkmark"
        case .runs: return "clock.arrow.circlepath"
        case .plans: return "giftcard"
        case .settings: return "gearshape"
        }
    }
}

@main
struct GLaDOSAccountCenterApp: App {
    @StateObject private var model = AppModel()
    var body: some Scene {
        WindowGroup { RootView().environmentObject(model).frame(minWidth: 1040, minHeight: 680) }
            .windowStyle(.titleBar)
            .commands {
                CommandGroup(after: .newItem) {
                    Button("导入账号…") { model.chooseImportFile() }.disabled(model.busyMessage != nil)
                    Button("导出账号…") { model.chooseExportFile() }.disabled(model.busyMessage != nil)
                }
            }
        Settings { SettingsView().environmentObject(model).frame(width: 520, height: 360) }
    }
}

struct RootView: View {
    @EnvironmentObject var model: AppModel
    @State private var selection: SidebarRoute? = .overview
    var body: some View {
        NavigationSplitView {
            List(SidebarRoute.allCases, selection: $selection) { route in Label(route.rawValue, systemImage: route.icon).tag(route) }
                .navigationTitle("GLaDOS")
                .safeAreaInset(edge: .bottom) {
                    VStack(alignment: .leading, spacing: 8) {
                        Divider()
                        HStack { Circle().fill(model.isGitHubReady ? Color.green : Color.orange).frame(width: 8, height: 8); Text(model.isGitHubReady ? "GitHub 已连接" : "需要连接 GitHub").font(.caption) }
                        if !model.isGitHubReady {
                            HStack(spacing: 6) {
                                Button("连接 GitHub") { model.startGitHubLogin() }.buttonStyle(.bordered)
                                Button("重新检测") { Task { await model.retryGitHubConnection() } }.buttonStyle(.bordered)
                            }.controlSize(.small)
                        }
                        Text("台湾时间 · \(model.config.schedule.times.joined(separator: " / "))").font(.caption2).foregroundStyle(.secondary)
                    }.padding(.horizontal).padding(.bottom, 8)
                }
        } detail: {
            Group {
                switch selection ?? .overview {
                case .overview: OverviewView()
                case .accounts: AccountsView()
                case .punches: PunchesView()
                case .runs: RunsView()
                case .plans: PlansView()
                case .settings: SettingsView()
                }
            }
            .toolbar {
                ToolbarItemGroup {
                    if model.isGitHubReady {
                        Button { Task { await model.refreshAllStatuses(userInitiated: true) } } label: { Label("刷新", systemImage: "arrow.clockwise") }.disabled(model.busyMessage != nil)
                    } else {
                        Button { model.startGitHubLogin() } label: { Label("连接 GitHub", systemImage: "person.crop.circle.badge.plus") }.disabled(model.busyMessage != nil)
                        Button { Task { await model.retryGitHubConnection() } } label: { Label("重新检测", systemImage: "arrow.clockwise") }.disabled(model.busyMessage != nil)
                    }
                    Button { Task { await model.startCapture() } } label: { Label("新增账号", systemImage: "plus") }.disabled(model.busyMessage != nil)
                    Menu {
                        Button("导入账号…") { model.chooseImportFile() }
                        Button("导出账号…") { model.chooseExportFile() }
                    } label: { Label("导入 / 导出", systemImage: "square.and.arrow.up.on.square") }.disabled(model.busyMessage != nil)
                }
            }
        }
        .overlay { if let message = model.busyMessage { BusyOverlay(message: message) } }
        .alert("操作未完成", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) { Button("确定", role: .cancel) {} } message: { Text(model.errorMessage ?? "") }
        .alert("完成", isPresented: Binding(get: { model.infoMessage != nil }, set: { if !$0 { model.infoMessage = nil } })) { Button("确定", role: .cancel) {} } message: { Text(model.infoMessage ?? "") }
        .sheet(isPresented: $model.showBrowserPicker) { BrowserPickerView() }
        .sheet(item: $model.transferRequest) { request in AccountTransferView(request: request) }
        .sheet(item: $model.emailEditRequest) { request in AccountEmailEditor(request: request) }
        .sheet(item: Binding(get: { model.pendingCapture.map { CaptureBox(value: $0) } }, set: { if $0 == nil { model.cancelCapture() } })) { box in CaptureConfirmView(payload: box.value, existing: model.config.accounts[box.value.accountKey]) }
    }
}

struct BrowserPickerView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 5) {
                Text("选择浏览器").font(.title2.bold())
                Text("手动读取所选账号的完整登录信息并保存到这台 Mac。网站需要登录或验证时，由你正常完成。").foregroundStyle(.secondary)
            }
            ScrollView {
                LazyVStack(spacing: 10) {
                    ForEach(model.browserOptions) { browser in
                        Button {
                            dismiss()
                            Task { await model.capture(using: browser) }
                        } label: {
                            HStack(spacing: 14) {
                                Image(systemName: browser.systemImage).font(.title2).frame(width: 34).foregroundStyle(.blue)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(browser.label).font(.headline).foregroundStyle(.primary)
                                    Text(browser.note).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.leading)
                                }
                                Spacer()
                                Image(systemName: "chevron.right").foregroundStyle(.tertiary)
                            }.padding(14).background(Color.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 14))
                        }.buttonStyle(.plain)
                    }
                }
            }.frame(maxHeight: 430)
            HStack { Spacer(); Button("取消") { dismiss() } }
        }.padding(24).frame(width: 560)
    }
}

struct CaptureBox: Identifiable { let id = UUID(); let value: CapturePayload }

struct CaptureConfirmView: View {
    @EnvironmentObject var model: AppModel
    let payload: CapturePayload
    @State private var autoExchange: Bool
    @State private var enabled: Bool
    @Environment(\.dismiss) private var dismiss

    init(payload: CapturePayload, existing: AccountConfig?) {
        self.payload = payload
        _autoExchange = State(initialValue: existing?.autoExchange ?? false)
        _enabled = State(initialValue: existing?.enabled ?? true)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 14) {
                Image(systemName: "person.crop.circle.badge.checkmark").font(.system(size: 42)).foregroundStyle(.blue)
                VStack(alignment: .leading) { Text("确认账号设置").font(.title2.bold()); Text(payload.email.isEmpty ? "待补邮箱" : payload.email).foregroundStyle(.secondary) }
            }
            GroupBox {
                VStack(alignment: .leading, spacing: 10) {
                    LabeledContent("账号标识", value: payload.accountKey)
                    LabeledContent("浏览器", value: payload.browser)
                    LabeledContent("剩余天数", value: payload.daysLeft.map { "\($0) 天" } ?? "未提供")
                }.frame(maxWidth: .infinity)
            }
            Toggle("开启自动签到", isOn: $enabled)
            Toggle("达到最优门槛后自动兑换积分", isOn: $autoExchange)
            if let best = model.catalog?.bestPlan {
                Text("当前最优：\(best.points) 积分 → \(best.days) 天（\(best.costPerDay, specifier: "%.2f") 积分/天）。未达到 \(best.points) 分不会调用兑换接口。")
                    .font(.callout).foregroundStyle(.secondary)
            }
            Text("凭据先保存到本机钥匙串，再同步此次手动操作。账号邮箱不会因登录失效而消失。").font(.caption).foregroundStyle(.secondary)
            if let error = model.errorMessage { Text(error).font(.callout).foregroundStyle(.orange) }
            HStack { Spacer(); Button("取消") { model.cancelCapture(); dismiss() }.disabled(model.busyMessage != nil); Button("保存账号") { Task { await model.commitCapture(autoExchange: autoExchange, enabled: enabled) } }.buttonStyle(.borderedProminent).disabled(model.busyMessage != nil) }
        }.padding(24).frame(width: 540)
        .interactiveDismissDisabled(model.busyMessage != nil)
    }
}

struct OverviewView: View {
    @EnvironmentObject var model: AppModel
    let columns = [GridItem(.adaptive(minimum: 210), spacing: 14)]
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 5) { Text("账号中心").font(.largeTitle.bold()); Text("管理 GLaDOS 多账号签到、积分兑换和 GitHub 自动化。状态刷新为只读，不会触发签到。 ").foregroundStyle(.secondary) }
                if !model.isGitHubReady {
                    HStack(alignment: .top, spacing: 16) {
                        Image(systemName: "lock.shield.fill").font(.system(size: 34)).foregroundStyle(.blue)
                        VStack(alignment: .leading, spacing: 7) {
                            Text("连接这台 Mac 的 GitHub CLI").font(.headline)
                            Text(model.githubConnectionIssue ?? "需要一次 GitHub 官方网页登录授权，之后 Account Center 才能读取账号配置、更新 Secrets 和触发 Actions。ChatGPT 中连接 GitHub 与本机 gh 授权是两套独立登录。")
                                .font(.callout).foregroundStyle(.secondary)
                            HStack {
                                Button("连接 GitHub") { model.startGitHubLogin() }.buttonStyle(.borderedProminent)
                                Button("重新检测") { Task { await model.retryGitHubConnection() } }.buttonStyle(.bordered)
                            }
                        }
                        Spacer()
                    }.padding(18).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                }
                LazyVGrid(columns: columns, spacing: 14) {
                    MetricCard(title: "账号总数", value: "\(model.accountsSorted.count)", icon: "person.2.fill")
                    MetricCard(title: "自动签到", value: "\(model.enabledCount)", icon: "checkmark.circle.fill")
                    MetricCard(title: "资料可读取", value: "\(model.healthyCount)", icon: "heart.fill")
                    MetricCard(title: "自动兑换", value: "\(model.exchangeEnabledCount)", icon: "gift.fill")
                }
                if let best = model.catalog?.bestPlan {
                    HStack(spacing: 18) {
                        VStack(alignment: .leading, spacing: 8) { Text("当前最优兑换").font(.headline); Text("\(best.points) → \(best.days) 天").font(.system(size: 32, weight: .bold, design: .rounded)); Text("\(best.costPerDay, specifier: "%.2f") 积分/天 · 自动兑换只在达到门槛时执行").foregroundStyle(.secondary) }
                        Spacer(); Image(systemName: "giftcard.fill").font(.system(size: 46)).foregroundStyle(.orange)
                    }.padding(20).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                }
                VStack(alignment: .leading, spacing: 12) {
                    HStack { Text("账号状态").font(.title2.bold()); Spacer(); Button("只读刷新") { Task { await model.refreshAllStatuses(userInitiated: true) } }.disabled(model.busyMessage != nil) }
                    ForEach(Array(model.accountsSorted.prefix(6)), id: \.0) { key, account in AccountCompactRow(key: key, account: account, status: model.statuses[key]) }
                }
            }.padding(28)
        }.navigationTitle("概览")
    }
}

struct MetricCard: View {
    let title: String; let value: String; let icon: String
    var body: some View {
        HStack { VStack(alignment: .leading, spacing: 8) { Text(title).font(.callout).foregroundStyle(.secondary); Text(value).font(.system(size: 30, weight: .semibold, design: .rounded)) }; Spacer(); Image(systemName: icon).font(.title2).foregroundStyle(.blue) }
            .padding(18).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

struct AccountCompactRow: View {
    @EnvironmentObject var model: AppModel
    let key: String; let account: AccountConfig; let status: AccountStatus?
    var body: some View {
        HStack(spacing: 14) {
            Circle().fill(status?.ok == true ? Color.green : status == nil ? Color.gray : Color.orange).frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 3) {
                Text(model.accountDisplayName(key)).font(.headline)
                Text(key).font(.caption.monospaced()).foregroundStyle(.secondary)
                if model.localAccounts[key]?.email.isEmpty != false { Button("补全邮箱") { model.editEmail(key) }.font(.caption) }
            }
            Spacer()
            if let points = status?.pointsTotal { Label("\(points)", systemImage: "star.circle").foregroundStyle(.secondary) }
            if let days = status?.daysLeft { Label("\(days) 天", systemImage: "calendar").foregroundStyle(.secondary) }
            if let streak = status?.streak, streak > 0 { Label("\(streak) 连签", systemImage: "flame.fill").foregroundStyle(.orange) }
            if model.isCloudAccount(key) {
                StatusPill(text: account.enabled ? "签到开" : "签到停", positive: account.enabled)
                StatusPill(text: account.autoExchange ? "兑换开" : "兑换关", positive: account.autoExchange)
            } else { StatusPill(text: "仅本机", positive: false) }
        }.padding(14).background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 14))
    }
}

struct AccountsView: View {
    @EnvironmentObject var model: AppModel
    @State private var deletingKey: String?
    @State private var editingAccount: AccountEditBox?
    var body: some View {
        ScrollView {
            LazyVStack(spacing: 14) {
                ForEach(model.accountsSorted, id: \.0) { key, account in AccountCard(key: key, account: account, status: model.statuses[key], deletingKey: $deletingKey, editingAccount: $editingAccount) }
            }.padding(24)
        }.navigationTitle("账号")
        .alert("从自动化中移除此账号？", isPresented: Binding(get: { deletingKey != nil }, set: { if !$0 { deletingKey = nil } })) {
            Button("取消", role: .cancel) { deletingKey = nil }
            Button("移除自动化", role: .destructive) { if let key = deletingKey { Task { await model.deleteAutomation(key) } }; deletingKey = nil }
        } message: { Text("这会删除该账号的 GitHub Secret 和自动签到任务，但不会删除 GLaDOS 网站账户。") }
        .sheet(item: $editingAccount) { item in AccountLabelEditor(item: item) }
    }
}

struct AccountEditBox: Identifiable { let key: String; let label: String; var id: String { key } }

struct AccountLabelEditor: View {
    @EnvironmentObject var model: AppModel
    let item: AccountEditBox
    @State private var label: String
    @Environment(\.dismiss) private var dismiss
    init(item: AccountEditBox) { self.item = item; _label = State(initialValue: item.label) }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("编辑账号名称").font(.title2.bold())
            Text(item.key).font(.caption.monospaced()).foregroundStyle(.secondary)
            TextField("备注名称", text: $label).textFieldStyle(.roundedBorder)
            HStack { Spacer(); Button("取消") { dismiss() }; Button("保存") { Task { await model.renameAccount(item.key, label: label); dismiss() } }.buttonStyle(.borderedProminent) }
        }.padding(24).frame(width: 440)
    }
}

struct AccountCard: View {
    @EnvironmentObject var model: AppModel
    let key: String; let account: AccountConfig; let status: AccountStatus?
    @Binding var deletingKey: String?
    @Binding var editingAccount: AccountEditBox?
    private var statusText: String {
        if !model.isCloudAccount(key) {
            if model.localAccounts[key]?.publicationSkipped == true { return "已有云端记录，已跳过同步" }
            return model.localAccounts[key]?.hasCredentials == true ? "待同步到 GitHub" : "待补登录信息"
        }
        return status?.ok == true ? "资料可读取" : status == nil ? "等待刷新" : "需要处理"
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(model.accountDisplayName(key)).font(.title3.bold())
                    Text(key).font(.caption.monospaced()).foregroundStyle(.secondary)
                    if model.localAccounts[key]?.email.isEmpty != false { Button("补全邮箱") { model.editEmail(key) }.font(.caption) }
                }
                Spacer(); Circle().fill(status?.ok == true ? Color.green : status == nil ? Color.gray : Color.orange).frame(width: 11, height: 11)
                Text(statusText).foregroundStyle(.secondary)
            }
            HStack(spacing: 28) {
                MiniStat(label: "积分", value: status?.pointsTotal.map(String.init) ?? "—")
                MiniStat(label: "剩余", value: status?.daysLeft.map { "\($0) 天" } ?? "—")
                MiniStat(label: "套餐", value: status?.planName.isEmpty == false ? status!.planName : "—")
                if let streak = status?.streak { MiniStat(label: "连续签到", value: "\(streak) 天") }
                Spacer()
            }
            if let history = status?.checkinHistory, !history.isEmpty {
                PunchHistoryView(history: history, streak: status?.streak, compact: true)
            }
            Divider()
            HStack {
                Toggle("自动签到", isOn: Binding(get: { account.enabled }, set: { value in Task { await model.setEnabled(key, value) } })).toggleStyle(.switch).disabled(!model.isCloudAccount(key) || !model.isGitHubReady)
                Toggle("自动兑换", isOn: Binding(get: { account.autoExchange }, set: { value in Task { await model.setAutoExchange(key, value) } })).toggleStyle(.switch).disabled(!model.isCloudAccount(key) || !model.isGitHubReady)
                Spacer()
                Button("刷新状态") { Task { await model.refreshStatus(key: key, userInitiated: true) } }.disabled(!model.isCloudAccount(key) || !model.isGitHubReady || model.busyMessage != nil)
                Button("立即签到") { Task { await model.runCheckin(key) } }.disabled(!model.isCloudAccount(key) || !model.isGitHubReady)
                Menu {
                    Button("打开 GLaDOS") { model.openGlados(accountKey: key) }
                    Button("账号邮箱（仅本机）") { model.editEmail(key) }
                    Button("编辑备注名称") { editingAccount = AccountEditBox(key: key, label: account.label) }
                    Button("重新读取登录信息") { Task { await model.startCapture(expectedAccountKey: key) } }
                    if model.localAccounts[key]?.pendingPublication == true || !model.isCloudAccount(key) {
                        Button("同步本机资料") { Task { await model.synchronizeLocalAccount(key) } }.disabled(model.localAccounts[key]?.hasCredentials != true || model.localAccounts[key]?.publicationSkipped == true || !model.isGitHubReady)
                    }
                    Divider()
                    Button("移除自动化", role: .destructive) { deletingKey = key }.disabled(!model.isCloudAccount(key))
                } label: { Image(systemName: "ellipsis.circle") }
            }
            if let stages = model.accountStageText(key) { Text(stages).font(.caption).foregroundStyle(.secondary) }
            if let receipt = model.latestReceipt(for: key) {
                HStack { Text(receipt.stateText).font(.caption).foregroundStyle(.secondary); Spacer(); Button(receipt.retryTitle) { Task { await model.retryExistingWorkflow(receipt) } }.disabled(!model.isGitHubReady || model.busyMessage != nil || receipt.supersededBy != nil) }
            }
            if let error = status?.error, !error.isEmpty { Text(error).font(.caption).foregroundStyle(.orange) }
            if let warning = status?.statusWarning, !warning.isEmpty { Text(warning).font(.caption).foregroundStyle(.secondary) }
            if let warning = status?.sessionWarning, !warning.isEmpty { Text(warning).font(.caption).foregroundStyle(.orange) }
            if model.localAccounts[key]?.publicationSkipped == true {
                Text("导入同步已跳过现有云端账号；本机备份已保留。需要更换该账号凭据时，请手动重新读取登录信息。").font(.caption).foregroundStyle(.secondary)
            }
            if model.localAccounts[key]?.hasCredentials != true {
                Text("本机尚未保存完整登录凭据；手动重新读取后可随加密备份一起导出。").font(.caption).foregroundStyle(.secondary)
            }
        }.padding(20).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }
}

struct MiniStat: View { let label: String; let value: String; var body: some View { VStack(alignment: .leading, spacing: 4) { Text(label).font(.caption).foregroundStyle(.secondary); Text(value).font(.headline) } } }
struct StatusPill: View { let text: String; let positive: Bool; var body: some View { Text(text).font(.caption2.bold()).padding(.horizontal, 8).padding(.vertical, 4).background((positive ? Color.green : Color.gray).opacity(0.14), in: Capsule()).foregroundStyle(positive ? .green : .secondary) } }

struct PunchHistoryView: View {
    let history: [CheckinDay]
    let streak: Int?
    var compact: Bool = false
    private let columns = Array(repeating: GridItem(.fixed(18), spacing: 5), count: 18)

    private func fill(_ state: String) -> Color {
        switch state {
        case "checked": return .blue
        case "missed": return Color.secondary.opacity(0.16)
        case "pending": return .orange.opacity(0.55)
        default: return Color.secondary.opacity(0.06)
        }
    }

    private func label(_ state: String) -> String {
        switch state {
        case "checked": return "已签到"
        case "missed": return "漏签"
        case "pending": return "今日待签到"
        default: return "历史数据不足"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 8 : 12) {
            HStack {
                Text(compact ? "最近 35 天签到" : "Daily Punch · 最近 35 天").font(compact ? .caption.bold() : .headline)
                Spacer()
                if let streak { Label("连续 \(streak) 天", systemImage: "flame.fill").font(.caption.bold()).foregroundStyle(streak > 0 ? .orange : .secondary) }
            }
            LazyVGrid(columns: columns, alignment: .leading, spacing: 5) {
                ForEach(Array(history.suffix(35))) { day in
                    RoundedRectangle(cornerRadius: 4, style: .continuous)
                        .fill(fill(day.state))
                        .frame(width: 18, height: 18)
                        .overlay {
                            if day.state == "unknown" {
                                RoundedRectangle(cornerRadius: 4, style: .continuous).stroke(Color.secondary.opacity(0.24), lineWidth: 1)
                            }
                        }
                        .help("\(day.date) · \(label(day.state))" + (day.pointsDelta.map { " · +\($0) 积分" } ?? ""))
                }
            }
            HStack(spacing: 14) {
                PunchLegend(color: .blue, text: "已签到")
                PunchLegend(color: Color.secondary.opacity(0.16), text: "漏签")
                PunchLegend(color: .orange.opacity(0.55), text: "今日待签")
                PunchLegend(color: Color.secondary.opacity(0.06), text: "数据不足", outlined: true)
                Spacer()
                if let first = history.suffix(35).first?.date, let last = history.suffix(35).last?.date { Text("\(first) – \(last)").font(.caption2).foregroundStyle(.tertiary) }
            }
        }
        .padding(compact ? 12 : 16)
        .background(Color.primary.opacity(compact ? 0.025 : 0.035), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

struct PunchLegend: View {
    let color: Color
    let text: String
    var outlined: Bool = false
    var body: some View {
        HStack(spacing: 5) {
            RoundedRectangle(cornerRadius: 3).fill(color).frame(width: 12, height: 12)
                .overlay { if outlined { RoundedRectangle(cornerRadius: 3).stroke(Color.secondary.opacity(0.24), lineWidth: 1) } }
            Text(text).font(.caption2).foregroundStyle(.secondary)
        }
    }
}

struct PunchesView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("签到日历").font(.largeTitle.bold())
                        Text("从 GLaDOS 积分历史只读推断签到记录；较早记录不足时显示为“数据不足”，不会误判成漏签。").foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("只读刷新全部") { Task { await model.refreshAllStatuses(userInitiated: true) } }.buttonStyle(.borderedProminent).disabled(model.busyMessage != nil)
                }
                ForEach(model.accountsSorted, id: \.0) { key, account in
                    let status = model.statuses[key]
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(model.accountDisplayName(key)).font(.title3.bold())
                                Text(key).font(.caption.monospaced()).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if let streak = status?.streak { StatusPill(text: "连续 \(streak) 天", positive: streak > 0) }
                            StatusPill(text: status?.ok == true ? "资料可读取" : "等待刷新", positive: status?.ok == true)
                        }
                        if let history = status?.checkinHistory, !history.isEmpty {
                            PunchHistoryView(history: history, streak: status?.streak)
                        } else {
                            HStack(spacing: 10) {
                                Image(systemName: "calendar.badge.exclamationmark").foregroundStyle(.secondary)
                                Text("尚无签到历史数据。点击只读刷新后获取。").foregroundStyle(.secondary)
                            }.padding(.vertical, 18)
                        }
                    }
                    .padding(18)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                }
            }.padding(24)
        }.navigationTitle("签到日历")
    }
}

struct RunsView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        List {
            if !model.workflowReceipts.isEmpty {
                Section("本机任务回执") {
                    ForEach(model.workflowReceipts.prefix(100)) { receipt in
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(receipt.title + " · " + (receipt.account == "all" ? "全部账号" : model.accountDisplayName(receipt.account))).font(.headline)
                                Text(receipt.stateText).font(.caption).foregroundStyle(.secondary)
                                Text(receipt.dispatchedAt + (receipt.runID.map { " · Run #\($0)" } ?? " · 编号待核对")).font(.caption.monospaced()).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button(receipt.retryTitle) { Task { await model.retryExistingWorkflow(receipt) } }.disabled(!model.isGitHubReady || model.busyMessage != nil || receipt.supersededBy != nil)
                        }
                    }
                }
            }
            Section("GitHub 最近签到") {
                ForEach(model.runs) { run in
                    HStack { Image(systemName: run.conclusion == "success" ? "checkmark.circle.fill" : run.status == "in_progress" ? "clock.fill" : "exclamationmark.circle.fill").foregroundStyle(run.conclusion == "success" ? .green : .orange); VStack(alignment: .leading) { Text("Run #\(run.id)").font(.headline); Text(run.createdAt).font(.caption).foregroundStyle(.secondary) }; Spacer(); Text(run.conclusion.isEmpty ? run.status : run.conclusion).foregroundStyle(.secondary); Button("打开") { model.openRun(run) } }
                }
            }
        }.navigationTitle("运行记录")
    }
}

struct PlanEditBox: Identifiable { let id = UUID(); let existing: ExchangePlan? }

struct PlanEditorView: View {
    @EnvironmentObject var model: AppModel
    let existing: ExchangePlan?
    @State private var planID: String
    @State private var pointsText: String
    @State private var daysText: String
    @State private var confirmed = false
    @Environment(\.dismiss) private var dismiss

    init(existing: ExchangePlan?) {
        self.existing = existing
        _planID = State(initialValue: existing?.id ?? "plan")
        _pointsText = State(initialValue: existing.map { String($0.points) } ?? "")
        _daysText = State(initialValue: existing.map { String($0.days) } ?? "")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(existing == nil ? "添加已验证兑换方案" : "编辑兑换方案").font(.title2.bold())
            Text("这里保存的是可信方案目录。自动兑换只会使用已验证方案；不要根据未经确认的网页文本直接填写。 ").foregroundStyle(.secondary)
            Form {
                TextField("方案 ID，例如 plan800", text: $planID)
                TextField("所需积分", text: $pointsText)
                TextField("兑换天数", text: $daysText)
                Toggle("我已确认此方案的 ID、积分和天数均来自当前 GLaDOS 页面/接口", isOn: $confirmed)
            }.formStyle(.grouped).frame(height: 245)
            if let points = Int(pointsText), let days = Int(daysText), points > 0, days > 0 {
                Text("单位成本：\(Double(points) / Double(days), specifier: "%.3f") 积分/天").font(.headline)
            }
            HStack {
                Spacer(); Button("取消") { dismiss() }
                Button("保存为已验证") {
                    guard let points = Int(pointsText), let days = Int(daysText) else { return }
                    Task { await model.upsertVerifiedPlan(planID: planID, points: points, days: days); dismiss() }
                }.buttonStyle(.borderedProminent).disabled(!confirmed || Int(pointsText) == nil || Int(daysText) == nil)
            }
        }.padding(24).frame(width: 590)
    }
}

struct PlansView: View {
    @EnvironmentObject var model: AppModel
    @State private var editing: PlanEditBox?
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("已验证积分方案").font(.largeTitle.bold())
                        Text("自动兑换只使用经过确认的方案。选择规则：积分/天最低；相同成本时优先兑换周期更短的方案。未知方案 Fail-Closed，不会自动花积分。 ").foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button { editing = PlanEditBox(existing: nil) } label: { Label("添加方案", systemImage: "plus") }.buttonStyle(.borderedProminent)
                }
                if let best = model.catalog?.bestPlan {
                    HStack {
                        VStack(alignment: .leading, spacing: 6) { Text("当前最优").font(.headline); Text("\(best.points) 积分 → \(best.days) 天").font(.system(size: 30, weight: .bold, design: .rounded)); Text("\(best.costPerDay, specifier: "%.3f") 积分/天 · 自动兑换门槛 \(best.points) 分").foregroundStyle(.secondary) }
                        Spacer(); Image(systemName: "crown.fill").font(.system(size: 40)).foregroundStyle(.yellow)
                    }.padding(20).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18))
                }
                Grid(alignment: .leading, horizontalSpacing: 30, verticalSpacing: 12) {
                    GridRow { Text("方案").bold(); Text("积分").bold(); Text("天数").bold(); Text("积分/天").bold(); Text("状态").bold(); Text("") }
                    Divider().gridCellColumns(6)
                    ForEach(model.catalog?.plans.sorted { $0.points < $1.points } ?? []) { plan in
                        GridRow {
                            Text(plan.id).monospaced(); Text("\(plan.points)"); Text("\(plan.days)"); Text("\(plan.costPerDay, specifier: "%.3f")")
                            Toggle("", isOn: Binding(get: { plan.verified }, set: { value in Task { await model.setPlanVerified(plan.id, value) } })).labelsHidden().toggleStyle(.switch)
                            Button("编辑") { editing = PlanEditBox(existing: plan) }
                        }
                    }
                }.padding(20).background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 16))
                Text("如果 GLaDOS 未来出现新档位，先在此确认方案 ID、积分和天数；保存后最优算法会立即重新计算门槛。无法可靠确认的新方案不会进入自动兑换。 ").font(.caption).foregroundStyle(.secondary)
            }.padding(28)
        }.navigationTitle("积分方案")
        .sheet(item: $editing) { box in PlanEditorView(existing: box.existing) }
    }
}

struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        Form {
            Section("自动签到计划") {
                LabeledContent("时区", value: "Asia/Taipei（台湾）")
                if model.scheduleDates.count >= 2 {
                    DatePicker("每天第 1 次", selection: Binding(get: { model.scheduleDates[0] }, set: { model.scheduleDates[0] = $0 }), displayedComponents: .hourAndMinute)
                    DatePicker("每天第 2 次", selection: Binding(get: { model.scheduleDates[1] }, set: { model.scheduleDates[1] = $0 }), displayedComponents: .hourAndMinute)
                }
                HStack { Button("恢复 05:00 / 17:00") { model.restoreRecommendedSchedule() }; Spacer(); Button("保存时间") { Task { await model.saveSchedule() } }.buttonStyle(.borderedProminent) }
            }
            Section("仓库") { LabeledContent("GitHub", value: defaultRepository); LabeledContent("分支", value: defaultBranch); Button("打开仓库") { model.openRepository() } }
            Section("签到失败通知") {
                Toggle("监测定时签到失败", isOn: Binding(get: { model.checkinWatch?.enabled ?? false }, set: { enabled in Task { await model.updateCheckinWatch("set-enabled", enabled: enabled) } })).disabled(model.checkinWatch == nil || model.busyMessage != nil)
                Text("独立只读检查 GitHub 定时签到结果并发送本机通知，不读取验证码或更新登录资料。").font(.caption).foregroundStyle(.secondary)
                LabeledContent("通知权限", value: model.checkinWatch?.authorizationText ?? "组件暂不可用")
                if let checked = model.checkinWatch?.lastCheckedAt { LabeledContent("最近检查", value: checked) }
                HStack {
                    Button("允许通知") { Task { await model.updateCheckinWatch("authorize") } }
                    Button("测试通知") { Task { await model.updateCheckinWatch("test") } }
                    Button("重新检测") { Task { await model.refreshCheckinWatch() } }
                }
            }
            Section("本机账号资料") {
                Text("邮箱独立保存在本机，登录失效也会保留。手动读取的完整登录凭据由系统钥匙串保护；没有自动收验证码、自动登录或自动更新 Cookie。")
                HStack { Button("导入账号…") { model.chooseImportFile() }; Button("加密导出账号…") { model.chooseExportFile() } }
            }
            Section("安全策略") { Text("手动保存的登录资料先写入本机钥匙串，再通过标准输入同步到该账号原有 GitHub Secret。不会写入公开账号配置、工作流或普通日志。资料刷新为只读，签到结果以实际签到运行记录为准。") }
        }.formStyle(.grouped).padding().environment(\.timeZone, TimeZone(identifier: "Asia/Taipei")!).navigationTitle("设置").task { await model.refreshCheckinWatch() }
    }
}

struct BusyOverlay: View {
    let message: String
    var body: some View { ZStack { Color.black.opacity(0.14).ignoresSafeArea(); VStack(spacing: 14) { ProgressView().controlSize(.large); Text(message).font(.headline) }.padding(.horizontal, 28).padding(.vertical, 22).background(.ultraThickMaterial, in: RoundedRectangle(cornerRadius: 18)).shadow(radius: 18) } }
}
