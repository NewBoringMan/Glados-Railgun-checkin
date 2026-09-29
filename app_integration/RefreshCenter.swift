import Foundation
import SwiftUI
import AppKit
import UniformTypeIdentifiers

private struct RefreshAccount: Decodable, Identifiable {
    let key: String
    let label: String
    let email: String
    let identity_kind: String
    let enabled: Bool
    let auto_exchange: Bool
    let policy: String
    let points: Double?
    let last_status_ok: Bool
    var id: String { key }
    var title: String { email.isEmpty ? label : email }
    var policyTitle: String {
        switch policy {
        case "plan100": return "100 积分 → 10 天"
        case "plan200": return "200 积分 → 30 天"
        case "plan500": return "500 积分 → 100 天"
        default: return "智能最优"
        }
    }
}

private struct MailAuthorization: Decodable {
    let configured: Bool
    let authorized: Bool
    let reason: String
}

private struct RefreshSnapshot: Decodable {
    let accounts: [RefreshAccount]
    let repository_source: String
    let mail_running: Bool
    let mail_reason: String
    let mailbox: String
    let gmail: MailAuthorization
    let confirmed_count: Int
    let schedule_enabled: Bool
    let automation_ready: Bool
    let automation_reason: String
    let last_live_gate: String
}

@MainActor
private final class RefreshModel: ObservableObject {
    @Published var snapshot: RefreshSnapshot?
    @Published var busy = false
    @Published var message = ""
    @Published var error = false
    @Published var mailbox = ""
    @Published var consentURL: URL?
    @Published var filter = ""
    @Published var selectedTab = 0
    @Published var editingAccount: RefreshAccount?
    private var process: Process?
    private var generation = UUID()

    var accounts: [RefreshAccount] {
        guard let all = snapshot?.accounts else { return [] }
        let q = filter.trimmingCharacters(in: .whitespacesAndNewlines)
        return q.isEmpty ? all : all.filter { $0.title.localizedCaseInsensitiveContains(q) || $0.key.localizedCaseInsensitiveContains(q) || $0.label.localizedCaseInsensitiveContains(q) }
    }

    func cancel() {
        generation = UUID()
        process?.terminate()
        process = nil
        busy = false
        consentURL = nil
        message = "操作已取消。不会删除已保存邮箱或授权。"
    }

    func load() { run(["action": "snapshot"]) }

    func run(_ request: [String: Any], reloadAfter: Bool = false) {
        guard !busy else { return }
        guard let resource = Bundle.main.resourceURL?.appendingPathComponent("LoginRefresh/login_refresh_app.py"),
              FileManager.default.fileExists(atPath: resource.path) else {
            error = true; message = "应用内登录维护模块缺失，请检查安装完整性。"; return
        }
        let python = Bundle.main.object(forInfoDictionaryKey: "GLaDOSRefreshPython") as? String ?? "/opt/homebrew/bin/python3"
        guard FileManager.default.isExecutableFile(atPath: python),
              let inputData = try? JSONSerialization.data(withJSONObject: request) else {
            error = true; message = "现有 Python 运行环境不可用，未自动安装其他软件。"; return
        }
        let child = Process()
        let output = Pipe(), input = Pipe()
        child.executableURL = URL(fileURLWithPath: python)
        child.arguments = ["-I", "-B", resource.path]
        // The entry point adds its own sealed module directory explicitly. Ignore
        // PYTHONPATH and user site packages; never log request/stdout payloads.
        child.standardOutput = output
        child.standardError = FileHandle.nullDevice
        child.standardInput = input
        child.environment = ["PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": NSHomeDirectory(),
                             "PYTHONDONTWRITEBYTECODE": "1", "LANG": "en_US.UTF-8"]
        let token = UUID(); generation = token
        process = child; busy = true; error = false; consentURL = nil
        message = request["action"] as? String == "snapshot" ? "读取账号与已保存邮箱…" : "正在处理…"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            do {
                try child.run()
                input.fileHandleForWriting.write(inputData + Data([10]))
                try? input.fileHandleForWriting.close()
                var pending = Data()
                var received = false
                while true {
                    let part = output.fileHandleForReading.availableData
                    if part.isEmpty { break }
                    pending.append(part)
                    guard pending.count <= 2 * 1024 * 1024 else { child.terminate(); break }
                    while let index = pending.firstIndex(of: 10) {
                        let line = pending.prefix(upTo: index)
                        pending.removeSubrange(...index)
                        if let value = try? JSONSerialization.jsonObject(with: line) as? [String: Any] {
                            received = true
                            DispatchQueue.main.async { [weak self] in
                                guard let self, self.generation == token else { return }
                                self.consume(value)
                            }
                        }
                    }
                }
                child.waitUntilExit()
                DispatchQueue.main.async { [weak self] in
                    guard let self, self.generation == token else { return }
                    self.process = nil; self.busy = false
                    if !received { self.error = true; self.message = "模块未返回有效结果；账号和兑换设置未被改动。" }
                    if reloadAfter && !self.error { self.load() }
                }
            } catch {
                DispatchQueue.main.async { [weak self] in
                    guard let self, self.generation == token else { return }
                    self.process = nil; self.busy = false; self.error = true
                    self.message = "无法启动应用内部维护模块，请检查运行环境。"
                }
            }
        }
        let timeout: Double = ["refresh_one", "refresh_all"].contains(request["action"] as? String ?? "") ? 900 : 380
        DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { [weak self, weak child] in
            guard let self, self.generation == token, child?.isRunning == true else { return }
            self.cancel(); self.error = true; self.message = "本次操作超时，已停止等待。"
        }
    }

    private func consume(_ value: [String: Any]) {
        error = (value["ok"] as? Bool) != true
        if value["event"] as? String == "snapshot",
           let data = try? JSONSerialization.data(withJSONObject: value),
           let decoded = try? JSONDecoder().decode(RefreshSnapshot.self, from: data) {
            snapshot = decoded; mailbox = decoded.mailbox
            let pending = decoded.accounts.filter { $0.identity_kind == "pending" }.count
            message = "\(decoded.accounts.count) 个账号 · 已确认邮箱 \(decoded.confirmed_count) 个 · 待登录核实 \(pending) 个。邮箱只保存在本机。"
            if decoded.repository_source != "github" { message += " 当前显示上次保存的账号列表。" }
        } else if let text = value["message"] as? String { message = text }
        if value["event"] as? String == "consent_url", let text = value["url"] as? String,
           let url = URL(string: text), url.scheme == "https", url.host == "accounts.google.com" {
            consentURL = url
        }
        if value["event"] as? String == "authorized" { consentURL = nil }
    }

    func chooseClientFile() {
        let panel = NSOpenPanel()
        panel.title = "导入 Google 桌面授权配置"
        panel.message = "选择 Google Cloud 下载的桌面应用 OAuth JSON 文件。配置将保存到系统钥匙串，不上传到 GitHub。"
        panel.allowedContentTypes = [.json]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.begin { [weak self] result in
            guard result == .OK, let file = panel.url else { return }
            Task { @MainActor in
                guard let self else { return }
                guard let attributes = try? FileManager.default.attributesOfItem(atPath: file.path),
                      let size = attributes[.size] as? NSNumber, size.intValue <= 65536,
                      let data = try? Data(contentsOf: file),
                      let config = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    self.error = true; self.message = "无法读取所选 JSON 配置文件。"; return
                }
                self.run(["action": "import_client", "config": config], reloadAfter: true)
            }
        }
    }

    func openConsent() {
        // A direct, explicit user gesture, not an automatic browser launch. No focus
        // change is performed by the unattended worker or on opening this window.
        guard let url = consentURL else { return }
        NSWorkspace.shared.open(url)
    }
}

private final class EmailFieldModel: ObservableObject {
    @Published var value: String
    init(_ value: String) { self.value = value }
}

private struct EmailEditView: View {
    let account: RefreshAccount
    let save: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @StateObject private var field: EmailFieldModel
    init(account: RefreshAccount, save: @escaping (String) -> Void) {
        self.account = account; self.save = save; _field = StateObject(wrappedValue: EmailFieldModel(account.email))
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("保存账号邮箱").font(.title2.bold())
            Text(account.label).font(.headline)
            Text(account.key).font(.caption.monospaced()).foregroundStyle(.secondary)
            TextField("完整邮箱地址", text: $field.value).textFieldStyle(.roundedBorder).disabled(account.identity_kind == "confirmed")
            Text("仅保存到这台 Mac。新填写的邮箱标记为待登录核实；在验证真实账号身份前，不会据此覆盖 Cookie 或 Secret。")
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack { Spacer(); Button("取消") { dismiss() }; Button("保存") { save(field.value); dismiss() }.keyboardShortcut(.defaultAction) }
        }.padding(22).frame(width: 460)
    }
}

private struct RefreshCenterView: View {
    @StateObject private var model = RefreshModel()
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("登录维护").font(.title2.bold())
                    Text("邮箱长期保存 · 收码设置 · 登录依赖检查").foregroundStyle(.secondary)
                }
                Spacer()
                Button("刷新") { model.load() }.disabled(model.busy)
            }
            Picker("页面", selection: $model.selectedTab) {
                Text("账号邮箱").tag(0); Text("收码设置").tag(1); Text("运行状态").tag(2)
            }.pickerStyle(.segmented)
            Divider()
            if model.selectedTab == 0 { accountPage }
            else if model.selectedTab == 1 { settingsPage }
            else { statusPage }
            Spacer(minLength: 0)
            Divider()
            HStack(spacing: 10) {
                if model.busy { ProgressView().controlSize(.small) }
                Text(model.message).font(.callout).foregroundStyle(model.error ? Color.red : Color.secondary)
                    .lineLimit(3).frame(maxWidth: .infinity, alignment: .leading)
                if model.busy { Button("取消等待") { model.cancel() } }
                Button("关闭") { NSApp.keyWindow?.performClose(nil) }
            }
        }
        .padding(20).frame(minWidth: 800, idealWidth: 900, minHeight: 580, idealHeight: 660)
        .task { model.load() }
        .sheet(item: $model.editingAccount) { account in
            EmailEditView(account: account) { value in model.run(["action":"save_email", "key":account.key,"email":value],reloadAfter:true) }
        }
    }

    private var accountPage: some View {
        VStack(alignment: .leading, spacing: 10) {
            TextField("按邮箱、备注或账号代号查找", text: $model.filter).textFieldStyle(.roundedBorder)
            ScrollView {
                LazyVStack(spacing: 7) {
                    ForEach(model.accounts) { account in
                        HStack(spacing: 12) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(account.title).font(.body.weight(.medium)).textSelection(.enabled)
                                Text("\(account.label) · \(account.key)").font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                                if account.identity_kind == "missing" { Text("尚未保存邮箱").font(.caption).foregroundStyle(.orange) }
                                else if account.identity_kind == "pending" { Text("邮箱已保存 · 待登录核实").font(.caption).foregroundStyle(.secondary) }
                                else { Text("邮箱已确认 · 登录失效也不会清空").font(.caption).foregroundStyle(.secondary) }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                            VStack(alignment: .trailing, spacing: 4) {
                                Text(account.policyTitle).font(.callout)
                                Text(account.auto_exchange ? "自动兑换开" : "自动兑换关").font(.caption).foregroundStyle(.secondary)
                            }
                            VStack(spacing: 6) {
                                Button(account.email.isEmpty ? "填写邮箱" : "查看邮箱") { model.editingAccount = account }
                                    .disabled(model.busy)
                                Button("登录并验证") { model.run(["action":"refresh_one", "key":account.key], reloadAfter:true) }
                                    .disabled(model.busy || account.email.isEmpty || model.snapshot?.gmail.authorized != true || model.snapshot?.mail_running != true)
                                    .help("使用此账号已保存邮箱收取新验证码；核对真实身份后更新原 Secret，并执行云端只读验证。")
                            }
                        }.padding(12).background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 9))
                    }
                }
            }
        }
    }

    private var settingsPage: some View {
        VStack(alignment: .leading, spacing: 18) {
            GroupBox("1 · 收取转发验证码的 Gmail 邮箱") {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Mail 负责本机收信和转发；此邮箱负责集中接收。请保持 Mail 运行。")
                        .font(.callout).foregroundStyle(.secondary)
                    HStack {
                        TextField("接收验证码的 Gmail 地址", text: $model.mailbox).textFieldStyle(.roundedBorder)
                        Button("保存邮箱") { model.run(["action":"configure_mailbox","mailbox":model.mailbox],reloadAfter:true) }.disabled(model.busy)
                    }
                }.padding(8)
            }
            GroupBox("2 · 授权 Account Center 只读收码") {
                VStack(alignment: .leading, spacing: 12) {
                    Text("首次使用需要 Google 桌面应用 OAuth 配置和一次邮箱授权。ChatGPT 的 Gmail 连接不会自动授权给本机 APP。")
                        .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    HStack {
                        Button("导入 Google 配置…") { model.chooseClientFile() }.disabled(model.busy)
                        Button("开始邮箱授权") { model.run(["action":"authorize_mailbox"], reloadAfter: true) }
                            .disabled(model.busy || model.snapshot?.gmail.configured != true || model.mailbox.isEmpty)
                        Button("检查授权") { model.run(["action":"check_mailbox"]) }
                            .disabled(model.busy || model.snapshot?.gmail.authorized != true)
                    }
                    if model.consentURL != nil {
                        Button("打开授权网页") { model.openConsent() }.buttonStyle(.borderedProminent)
                        Text("只在你点击后打开。授权完成会自动返回本窗口状态；不需要复制令牌。")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Text(model.snapshot?.gmail.authorized == true ? "已有邮箱授权；实际可用性可通过“检查授权”确认。" : "尚未完成本机邮箱授权。")
                        .font(.callout)
                }.padding(8)
            }
            Text("授权范围仅 Gmail 只读。凭据保存在系统钥匙串；不会将邮箱、验证码或 Cookie 写入公开 GitHub 仓库。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var statusPage: some View {
        VStack(alignment: .leading, spacing: 18) {
            statusLine("Mail", model.snapshot?.mail_running == true ? "正在运行" : "未运行或无法确认")
            Text("Mail 打开不等于全部邮箱已联网。关闭、休眠或转发延迟时，发码流程必须暂停；过期验证码不会重新使用。")
                .font(.callout).foregroundStyle(.secondary)
            statusLine("收码邮箱", model.snapshot?.gmail.authorized == true ? "已有授权" : "需要首次授权")
            statusLine("GitHub 账号目录", model.snapshot?.repository_source == "github" ? "刚从 GitHub 读取" : "上次保存的目录")
            Divider()
            Text(model.snapshot?.schedule_enabled == true ? "每月串行维护已启用" : "每月串行维护未启用").font(.headline)
            Text(model.snapshot?.automation_reason ?? "需要先完成单账号真实登录与云端验证。")
                .foregroundStyle(.secondary)
            Text(model.snapshot?.last_live_gate ?? "网站要求人机验证时，只能由你在正常页面完成。")
                .font(.callout).foregroundStyle(.secondary)
            HStack {
                Button("继续本轮待办") { model.run(["action":"refresh_all"], reloadAfter:true) }
                    .disabled(model.busy || model.snapshot?.automation_ready != true)
                if model.snapshot?.schedule_enabled == true {
                    Button("停用每月维护") { model.run(["action":"disable_monthly"], reloadAfter:true) }.disabled(model.busy)
                } else {
                    Button("启用每月维护") { model.run(["action":"enable_monthly"], reloadAfter:true) }
                        .disabled(model.busy || model.snapshot?.automation_ready != true)
                }
                Button("打开官方登录页") { NSWorkspace.shared.open(URL(string:"https://glados.cloud/login")!) }
            }
            Text("每次仅处理一个账号；后台定期续接队列，同一月份已完成的账号不重复登录。失败分轮冷却，最多三次；人机验证立即暂停。Mail 关闭时暂停，当前版本不会强行重开窗口。")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal:false, vertical:true)
            Text("现有 GitHub 自动签到和每账号兑换方案保持原样。本窗口不会为了测试触发兑换。")
                .font(.callout)
            Spacer()
        }
    }

    private func statusLine(_ name: String, _ value: String) -> some View {
        HStack { Text(name).fontWeight(.medium); Spacer(); Text(value).foregroundStyle(.secondary) }
    }
}

@MainActor
private var refreshController: NSWindowController?

@_cdecl("GLaDOSShowRefreshCenter")
public func GLaDOSShowRefreshCenter() {
    DispatchQueue.main.async {
        if let controller = refreshController {
            controller.showWindow(nil); controller.window?.makeKeyAndOrderFront(nil); return
        }
        let view = NSHostingController(rootView: RefreshCenterView())
        let window = NSWindow(contentViewController: view)
        window.title = "GLaDOS 登录维护"
        window.styleMask = [.titled, .closable, .miniaturizable, .resizable]
        window.setContentSize(NSSize(width: 900, height: 660))
        window.isReleasedWhenClosed = false
        window.center()
        let controller = NSWindowController(window: window)
        refreshController = controller
        controller.showWindow(nil)
        // Invoked only by the user's menu/button action; never during app startup.
        window.makeKeyAndOrderFront(nil)
    }
}
