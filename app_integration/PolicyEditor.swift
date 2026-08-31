import SwiftUI
import Foundation
import AppKit

private let repository = "NewBoringMan/Glados-Railgun-checkin"
private let policyPath = ".github/glados/account_policies.json"
private let defaultBranch = "master"

struct AccountEntry: Decodable {
    let autoExchange: Bool
    let enabled: Bool
    let label: String
}

struct AccountsFile: Decodable {
    let accounts: [String: AccountEntry]
}

struct ExchangePlan: Codable, Hashable, Identifiable {
    let id: String
    let points: Int
    let days: Int
    let verified: Bool

    var costPerDay: Double { Double(points) / Double(days) }
}

struct CatalogFile: Decodable {
    let plans: [ExchangePlan]
}

struct AccountPolicies: Codable, Equatable {
    var version: Int = 1
    var defaultPolicy: String = "auto"
    var accounts: [String: String] = [:]

    enum CodingKeys: String, CodingKey {
        case version
        case defaultPolicy = "default"
        case accounts
    }
}

struct AccountRow: Identifiable, Hashable {
    let key: String
    let label: String
    let enabled: Bool
    let autoExchange: Bool
    var id: String { key }
}

struct PolicyOption: Identifiable, Hashable {
    let id: String
    let title: String
}

enum PolicyEditorError: LocalizedError {
    case ghMissing
    case commandFailed(String)
    case invalidData(String)

    var errorDescription: String? {
        switch self {
        case .ghMissing:
            return "未找到 GitHub CLI（gh）。请先在 Account Center 中确认 GitHub 已连接。"
        case .commandFailed(let message):
            return message
        case .invalidData(let message):
            return message
        }
    }
}

final class Shell {
    static let shared = Shell()

    private var ghURL: URL? {
        let candidates = [
            "/opt/homebrew/bin/gh",
            "/usr/local/bin/gh",
            "/usr/bin/gh"
        ]
        return candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }).map(URL.init(fileURLWithPath:))
    }

    func gh(_ arguments: [String], allowFailure: Bool = false) throws -> (stdout: String, stderr: String, status: Int32) {
        guard let ghURL else { throw PolicyEditorError.ghMissing }

        let process = Process()
        process.executableURL = ghURL
        process.arguments = arguments
        let output = Pipe()
        let error = Pipe()
        process.standardOutput = output
        process.standardError = error

        do {
            try process.run()
        } catch {
            throw PolicyEditorError.commandFailed("无法启动 GitHub CLI：\(error.localizedDescription)")
        }
        process.waitUntilExit()

        let outData = output.fileHandleForReading.readDataToEndOfFile()
        let errData = error.fileHandleForReading.readDataToEndOfFile()
        let stdout = String(data: outData, encoding: .utf8) ?? ""
        let stderr = String(data: errData, encoding: .utf8) ?? ""

        if process.terminationStatus != 0 && !allowFailure {
            let message = stderr.trimmingCharacters(in: .whitespacesAndNewlines)
            throw PolicyEditorError.commandFailed(message.isEmpty ? "GitHub 操作失败（退出码 \(process.terminationStatus)）" : message)
        }
        return (stdout, stderr, process.terminationStatus)
    }
}

@MainActor
final class PolicyEditorModel: ObservableObject {
    @Published var rows: [AccountRow] = []
    @Published var plans: [ExchangePlan] = []
    @Published var selections: [String: String] = [:]
    @Published var isBusy = false
    @Published var message = ""
    @Published var isError = false

    private var loadedPolicies = AccountPolicies()
    private var loadedPolicySHA: String? = nil

    var options: [PolicyOption] {
        var result: [PolicyOption] = []
        if let best = bestPlan {
            result.append(PolicyOption(id: "auto", title: "智能最优（当前 \(best.points) → \(best.days) 天）"))
        } else {
            result.append(PolicyOption(id: "auto", title: "智能最优"))
        }
        result += plans
            .filter(\.verified)
            .sorted { lhs, rhs in
                if lhs.points != rhs.points { return lhs.points < rhs.points }
                return lhs.days < rhs.days
            }
            .map { PolicyOption(id: $0.id, title: "\($0.points) 积分 → \($0.days) 天") }
        return result
    }

    var bestPlan: ExchangePlan? {
        plans
            .filter { $0.verified && $0.points > 0 && $0.days > 0 }
            .min {
                if $0.costPerDay != $1.costPerDay { return $0.costPerDay < $1.costPerDay }
                if $0.days != $1.days { return $0.days < $1.days }
                if $0.points != $1.points { return $0.points < $1.points }
                return $0.id < $1.id
            }
    }

    func load() async {
        isBusy = true
        message = "正在从 GitHub 读取账号和兑换策略…"
        isError = false
        defer { isBusy = false }

        do {
            let snapshot = try await Task.detached(priority: .userInitiated) { () -> (AccountsFile, CatalogFile, AccountPolicies, String?) in
                let decoder = JSONDecoder()
                let accountsRaw = try Shell.shared.gh([
                    "api", "repos/\(repository)/contents/.github/glados/accounts.json",
                    "-H", "Accept: application/vnd.github.raw+json"
                ]).stdout
                let catalogRaw = try Shell.shared.gh([
                    "api", "repos/\(repository)/contents/.github/glados/exchange_plans.json",
                    "-H", "Accept: application/vnd.github.raw+json"
                ]).stdout

                guard let accountsData = accountsRaw.data(using: .utf8),
                      let catalogData = catalogRaw.data(using: .utf8) else {
                    throw PolicyEditorError.invalidData("GitHub 返回的数据无法解码。")
                }
                let accounts = try decoder.decode(AccountsFile.self, from: accountsData)
                let catalog = try decoder.decode(CatalogFile.self, from: catalogData)

                let policyMetadata = try Shell.shared.gh([
                    "api", "repos/\(repository)/contents/\(policyPath)?ref=\(defaultBranch)",
                    "--jq", ".sha"
                ], allowFailure: true)

                var policies = AccountPolicies()
                var policySHA: String? = nil
                if policyMetadata.status == 0 {
                    let sha = policyMetadata.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
                    policySHA = sha.isEmpty ? nil : sha
                    let policyRaw = try Shell.shared.gh([
                        "api", "repos/\(repository)/contents/\(policyPath)?ref=\(defaultBranch)",
                        "-H", "Accept: application/vnd.github.raw+json"
                    ]).stdout
                    guard let policyData = policyRaw.data(using: .utf8) else {
                        throw PolicyEditorError.invalidData("账号兑换策略文件无法解码。")
                    }
                    policies = try decoder.decode(AccountPolicies.self, from: policyData)
                } else {
                    let err = policyMetadata.stderr.lowercased()
                    if !err.contains("was not found") && !err.contains("not found") && !err.contains("404") {
                        throw PolicyEditorError.commandFailed(policyMetadata.stderr.trimmingCharacters(in: .whitespacesAndNewlines))
                    }
                }
                return (accounts, catalog, policies, policySHA)
            }.value

            let validPlans = Set(snapshot.1.plans.filter(\.verified).map(\.id))
            let defaultPolicy = snapshot.2.defaultPolicy == "auto" || validPlans.contains(snapshot.2.defaultPolicy)
                ? snapshot.2.defaultPolicy : "auto"

            rows = snapshot.0.accounts.map { key, value in
                AccountRow(key: key.uppercased(), label: value.label, enabled: value.enabled, autoExchange: value.autoExchange)
            }.sorted { lhs, rhs in
                let l = lhs.label.localizedCaseInsensitiveCompare(rhs.label)
                return l == .orderedSame ? lhs.key < rhs.key : l == .orderedAscending
            }
            plans = snapshot.1.plans.filter(\.verified)
            loadedPolicies = snapshot.2
            loadedPolicySHA = snapshot.3

            let currentKeys = Set(rows.map(\.key))
            var normalizedPolicies = snapshot.2
            normalizedPolicies.defaultPolicy = defaultPolicy
            normalizedPolicies.accounts = normalizedPolicies.accounts.reduce(into: [:]) { partial, item in
                let key = item.key.uppercased()
                guard currentKeys.contains(key) else { return }
                partial[key] = validPlans.contains(item.value) || item.value == "auto" ? item.value : "auto"
            }

            selections = Dictionary(uniqueKeysWithValues: rows.map { row in
                (row.key, normalizedPolicies.accounts[row.key] ?? defaultPolicy)
            })
            message = "已读取 \(rows.count) 个账号。修改后点击“保存到 GitHub”。"
            isError = false
        } catch {
            message = error.localizedDescription
            isError = true
        }
    }

    func save() async {
        isBusy = true
        message = "正在同步兑换策略到 GitHub…"
        isError = false
        defer { isBusy = false }

        do {
            let validPlanIDs = Set(plans.filter(\.verified).map(\.id))
            let currentKeys = Set(rows.map(\.key))
            var policies = AccountPolicies(version: 1, defaultPolicy: "auto", accounts: [:])

            for (key, policy) in selections {
                guard currentKeys.contains(key) else { continue }
                guard policy == "auto" || validPlanIDs.contains(policy) else {
                    throw PolicyEditorError.invalidData("账号 \(key) 选择了不存在或未验证的兑换方案。")
                }
                if policy != "auto" {
                    policies.accounts[key] = policy
                }
            }

            if policies == loadedPolicies {
                message = "没有策略变更，无需提交。"
                isError = false
                return
            }

            let policiesToSave = policies
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
            var data = try encoder.encode(policiesToSave)
            data.append(0x0A)
            let base64 = data.base64EncodedString()
            let expectedSHA = loadedPolicySHA

            let verified = try await Task.detached(priority: .userInitiated) { () -> (AccountPolicies, String?) in
                var arguments = [
                    "api", "-X", "PUT", "repos/\(repository)/contents/\(policyPath)",
                    "-f", "message=chore: update account exchange policy",
                    "-f", "content=\(base64)",
                    "-f", "branch=\(defaultBranch)"
                ]
                if let expectedSHA, !expectedSHA.isEmpty {
                    arguments += ["-f", "sha=\(expectedSHA)"]
                }

                _ = try Shell.shared.gh(arguments)
                let readBack = try Shell.shared.gh([
                    "api", "repos/\(repository)/contents/\(policyPath)?ref=\(defaultBranch)",
                    "-H", "Accept: application/vnd.github.raw+json"
                ]).stdout
                guard let backData = readBack.data(using: .utf8) else {
                    throw PolicyEditorError.invalidData("GitHub 写入后无法读取校验。")
                }
                let decoded = try JSONDecoder().decode(AccountPolicies.self, from: backData)
                let sha = try Shell.shared.gh([
                    "api", "repos/\(repository)/contents/\(policyPath)?ref=\(defaultBranch)",
                    "--jq", ".sha"
                ]).stdout.trimmingCharacters(in: .whitespacesAndNewlines)
                return (decoded, sha.isEmpty ? nil : sha)
            }.value

            guard verified.0 == policiesToSave else {
                throw PolicyEditorError.invalidData("GitHub 写入后的策略与本地选择不一致，已停止并提示检查。")
            }

            loadedPolicies = verified.0
            loadedPolicySHA = verified.1
            let fixedCount = verified.0.accounts.count
            message = fixedCount == 0
                ? "保存成功：全部账号使用智能最优方案。"
                : "保存成功：\(fixedCount) 个账号使用固定兑换方案，其余账号使用智能最优。"
            isError = false
        } catch PolicyEditorError.commandFailed(let detail) {
            let lower = detail.lowercased()
            if lower.contains("sha") || lower.contains("conflict") || lower.contains("409") {
                message = "GitHub 上的兑换策略已被其他设备更新。请先点击“刷新”，确认最新配置后再保存。"
            } else {
                message = detail
            }
            isError = true
        } catch {
            message = error.localizedDescription
            isError = true
        }
    }
}

struct PolicyEditorContentView: View {
    @StateObject private var model = PolicyEditorModel()

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("账号兑换方案")
                        .font(.title2.bold())
                    Text("每个账号独立设置；策略直接保存在 GitHub，不依赖 Workflow 模板。Cookie 与 Secret 不会被读取。")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("刷新") { Task { await model.load() } }
                    .disabled(model.isBusy)
            }

            Divider()

            ScrollView {
                LazyVStack(spacing: 8) {
                    ForEach(model.rows) { row in
                        HStack(spacing: 12) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(row.label)
                                    .font(.body.weight(.medium))
                                Text(row.key)
                                    .font(.caption.monospaced())
                                    .foregroundStyle(.secondary)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)

                            if !row.autoExchange {
                                Text("自动兑换关")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }

                            Picker("兑换方案", selection: Binding(
                                get: { model.selections[row.key] ?? "auto" },
                                set: { model.selections[row.key] = $0 }
                            )) {
                                ForEach(model.options) { option in
                                    Text(option.title).tag(option.id)
                                }
                            }
                            .labelsHidden()
                            .frame(width: 250)
                            .disabled(model.isBusy)
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 9)
                        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 10))
                    }
                }
            }
            .frame(minHeight: 420)

            Divider()

            HStack(spacing: 10) {
                if model.isBusy {
                    ProgressView()
                        .controlSize(.small)
                }
                Text(model.message)
                    .font(.callout)
                    .foregroundStyle(model.isError ? Color.red : Color.secondary)
                    .lineLimit(2)
                Spacer()
                Button("关闭") { NSApp.keyWindow?.performClose(nil) }
                    .keyboardShortcut(.cancelAction)
                Button("保存到 GitHub") { Task { await model.save() } }
                    .keyboardShortcut(.defaultAction)
                    .disabled(model.isBusy || model.rows.isEmpty)
            }
        }
        .padding(18)
        .frame(width: 760, height: 620)
        .task { await model.load() }
    }
}

@MainActor
final class PolicyEditorWindowManager {
    static let shared = PolicyEditorWindowManager()
    private var window: NSWindow?

    func show() {
        if let window {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }

        let controller = NSHostingController(rootView: PolicyEditorContentView())
        let window = NSWindow(contentViewController: controller)
        window.title = "GLaDOS 账号兑换方案"
        window.styleMask = [.titled, .closable, .miniaturizable, .resizable]
        window.setContentSize(NSSize(width: 760, height: 620))
        window.minSize = NSSize(width: 720, height: 560)
        window.isReleasedWhenClosed = false
        window.center()
        self.window = window
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

@_cdecl("GLaDOSShowPolicyEditor")
public func GLaDOSShowPolicyEditor() {
    Task { @MainActor in
        PolicyEditorWindowManager.shared.show()
    }
}
