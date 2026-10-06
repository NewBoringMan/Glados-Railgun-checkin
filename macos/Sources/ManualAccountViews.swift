import SwiftUI
import Foundation

struct AccountTransferRequest: Identifiable {
    enum Kind { case importFile, exportFile }
    let id = UUID()
    let kind: Kind
    let url: URL
}

struct AccountEmailEditRequest: Identifiable {
    let key: String
    let email: String
    var id: String { key }
}

struct AccountEmailEditor: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let request: AccountEmailEditRequest
    @State private var email: String

    init(request: AccountEmailEditRequest) {
        self.request = request
        _email = State(initialValue: request.email)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("账号邮箱").font(.title2.bold())
            Text(request.key).font(.caption.monospaced()).foregroundStyle(.secondary)
            TextField("账号真实邮箱", text: $email).textFieldStyle(.roundedBorder)
            Text("邮箱只保存在这台 Mac，并随加密备份导出。补填邮箱不会登录网站或更新 Cookie；已核验的账号身份不会被另一邮箱覆盖。")
                .font(.callout).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("取消") { dismiss() }
                Button("保存邮箱") { model.saveEmail(request.key, email: email) }
                    .buttonStyle(.borderedProminent).disabled((try? ManualValidation.email(email)) == nil)
            }
        }.padding(24).frame(width: 490)
    }
}

struct AccountTransferView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let request: AccountTransferRequest
    @State private var password = ""
    @State private var confirmation = ""
    private var isExport: Bool { request.kind == .exportFile }
    private var ready: Bool {
        password.utf8.count >= 12 && password.utf8.count <= 1024 && (!isExport || password == confirmation) && model.busyMessage == nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label(isExport ? "加密导出账号" : "导入账号备份", systemImage: "lock.shield").font(.title2.bold())
            Text(request.url.lastPathComponent).font(.callout).foregroundStyle(.secondary)
            Text(isExport ? "完整登录凭据保存在加密文件中。请设置至少 12 个字符的密码，并单独保存；忘记密码无法恢复备份。"
                 : "使用导出时设置的密码解密。所有已记录账号都将跳过；即使备份中有更新的 Cookie，也不会覆盖现有账号。")
                .font(.callout).foregroundStyle(.secondary)
            SecureField(isExport ? "设置备份密码（至少 12 个字符）" : "输入备份密码", text: $password).textFieldStyle(.roundedBorder)
            if isExport { SecureField("再次输入密码", text: $confirmation).textFieldStyle(.roundedBorder) }
            Text(isExport ? "未在本机保存过的登录凭据无法从 GitHub Secret 读回；这些账号仍会导出邮箱和设置，并标记为待补登录信息。"
                 : "含完整登录凭据的新账号会加入原账号列表和签到任务；只有邮箱资料的新账号会显示为待补登录信息。原有账号设置与签到时间保持不变。")
                .font(.caption).foregroundStyle(.secondary)
            if let message = model.busyMessage { HStack { ProgressView().controlSize(.small); Text(message).font(.callout) } }
            HStack {
                Spacer()
                Button("取消") { password = ""; confirmation = ""; dismiss() }.disabled(model.busyMessage != nil)
                Button(isExport ? "加密导出" : "验证并导入") {
                    let value = password
                    Task {
                        if isExport { await model.exportAccounts(to: request.url, password: value) }
                        else { await model.importAccounts(from: request.url, password: value) }
                        if model.transferRequest == nil { password = ""; confirmation = "" }
                    }
                }.buttonStyle(.borderedProminent).disabled(!ready)
            }
        }.padding(24).frame(width: 560)
        .interactiveDismissDisabled(model.busyMessage != nil)
    }
}
