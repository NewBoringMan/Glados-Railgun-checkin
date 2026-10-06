import Foundation

enum WorkflowReceiptError: LocalizedError, Equatable {
    case identity, protocolError, interrupted, missingRunID, dispatchUncertain, statusDispatchUncertain
    var errorDescription: String? {
        switch self {
        case .identity: return "任务归属未通过核对。已保存的登录资料和发布回执保留；未重新派发任务。"
        case .protocolError: return "GitHub 任务资料不完整。请稍后重查已有任务；未重新派发。"
        case .interrupted: return "GitHub 查询暂未完成。可重查已有任务；本机登录资料和发布状态未改变。"
        case .missingRunID: return "此次派发未返回可核对的任务编号。请在运行记录中指定原任务编号；应用不会自动再次派发。"
        case .dispatchUncertain: return "此次任务派发结果尚不确定。回执已保存在本机，请重查原任务；应用不会自动再次派发。"
        case .statusDispatchUncertain: return "此次刷新尚未取得可核对的任务编号。可以再次手动刷新；本机账号和登录资料已保留。"
        }
    }
}

enum WorkflowQueryState: String, Codable, Sendable {
    case pending, interrupted, rejected, succeeded, failed
    var finished: Bool { self == .succeeded || self == .failed }
}

struct WorkflowRunReceipt: Codable, Equatable, Identifiable, Sendable {
    var id: String = UUID().uuidString.lowercased()
    var workflow: String
    var account: String
    var dispatchedAt: String = ManualValidation.timestamp()
    var credentialID: String?
    var runID: Int?
    var dispatchAcknowledged = false
    var queryState: WorkflowQueryState = .pending
    var conclusion: String?
    // Optional metadata keeps existing receipt files readable. The original
    // unknown query remains unknown; replacement does not claim it failed.
    var supersededBy: String?
    var isResolved: Bool { queryState.finished || supersededBy != nil }
    var retryTitle: String { workflow == "gladosStatus.yml" && runID == nil ? "重新刷新" : "重查已有任务" }
    var title: String { workflow == "gladosStatus.yml" ? "资料查询" : "手动签到" }
    var stateText: String {
        if supersededBy != nil { return "已由新的资料查询替代" }
        switch queryState {
        case .succeeded: return workflow == "gladosStatus.yml" ? "资料查询任务成功（非签到认证）" : "签到任务成功"
        case .failed: return "任务已完成，结果需要处理"
        case .rejected: return "任务归属未通过核对"
        case .interrupted: return "查询中断，可重查原任务"
        case .pending: return runID == nil ? "派发回执待核对" : "任务结果待查询"
        }
    }
    func validate() throws {
        guard UUID(uuidString: id) != nil, WorkflowRunRules.workflows.contains(workflow),
              account == "all" || (try? ManualValidation.key(account)) == account,
              ManualValidation.date(dispatchedAt) != nil,
              runID == nil || WorkflowRunRules.validID(runID!),
              conclusion == nil || WorkflowRunRules.conclusions.contains(conclusion!) else { throw WorkflowReceiptError.protocolError }
        if let credentialID {
            guard account != "all", credentialID.hasPrefix(account + "."), UUID(uuidString: String(credentialID.dropFirst(17))) != nil else { throw WorkflowReceiptError.protocolError }
        }
        if let supersededBy {
            guard workflow == "gladosStatus.yml", runID == nil, !queryState.finished,
                  conclusion == nil, supersededBy != id, UUID(uuidString: supersededBy) != nil else { throw WorkflowReceiptError.protocolError }
        }
    }
}

enum WorkflowRunRules {
    static let workflows: Set<String> = ["gladosStatus.yml", "gladosAccounts.yml"]
    static let conclusions: Set<String> = ["success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required", "stale", "startup_failure"]
    static let statuses: Set<String> = ["queued", "in_progress", "completed", "waiting", "pending", "requested"]
    static func validID(_ value: Int) -> Bool { value > 0 && value < 1_000_000_000_000_000_000 }
    static func runID(_ text: String) throws -> Int {
        guard text.range(of: "^[1-9][0-9]{0,17}$", options: .regularExpression) != nil, let id = Int(text), validID(id) else { throw WorkflowReceiptError.protocolError }
        return id
    }
    // Consume the documented REST response from this POST, never optional CLI
    // presentation text or a guessed recent run from another invocation.
    static func dispatchedID(response: Data, repository: String) throws -> Int {
        struct Dispatch: Decodable {
            let workflow_run_id: Int
            let run_url: String
            let html_url: String
        }
        guard !response.isEmpty else { throw WorkflowReceiptError.missingRunID }
        guard let value = try? JSONDecoder().decode(Dispatch.self, from: response), validID(value.workflow_run_id) else {
            throw WorkflowReceiptError.protocolError
        }
        let id = value.workflow_run_id
        for (text, host, path) in [
            (value.run_url, "api.github.com", "/repos/\(repository)/actions/runs/\(id)"),
            (value.html_url, "github.com", "/\(repository)/actions/runs/\(id)")
        ] {
            guard let url = URLComponents(string: text), url.scheme == "https", url.host == host,
                  url.port == nil, url.user == nil, url.password == nil, url.query == nil,
                  url.fragment == nil, url.path == path else { throw WorkflowReceiptError.identity }
        }
        return id
    }
    struct Run: Decodable {
        let id: Int; let path: String; let head_branch: String; let event: String
        let created_at: String; let status: String; let conclusion: String?
    }
    struct Job: Decodable { let name: String; let status: String; let conclusion: String? }
    struct Jobs: Decodable { let total_count: Int; let jobs: [Job] }
    static func verify(run data: Data, receipt: WorkflowRunReceipt) throws -> Run {
        try receipt.validate()
        guard let run = try? JSONDecoder().decode(Run.self, from: data),
              run.id == receipt.runID, run.path == ".github/workflows/\(receipt.workflow)",
              run.head_branch == "master", run.event == "workflow_dispatch",
              let created = ManualValidation.date(run.created_at), let dispatched = ManualValidation.date(receipt.dispatchedAt),
              created >= dispatched.addingTimeInterval(-1), created <= dispatched.addingTimeInterval(120),
              statuses.contains(run.status), run.conclusion == nil || conclusions.contains(run.conclusion!) else { throw WorkflowReceiptError.identity }
        return run
    }
    static func verify(jobs data: Data, receipt: WorkflowRunReceipt, completed: Bool, requireAccountJobs: Bool = false) throws -> Bool {
        guard let list = try? JSONDecoder().decode(Jobs.self, from: data), list.total_count == list.jobs.count,
              (0...100).contains(list.total_count) else { throw WorkflowReceiptError.protocolError }
        let prefix = receipt.workflow == "gladosStatus.yml" ? "GLaDOS status " : "GLaDOS account "
        let accountJobs = list.jobs.filter { $0.name.hasPrefix(prefix) }
        for job in accountJobs {
            guard (try? ManualValidation.key(String(job.name.dropFirst(prefix.count)))) != nil,
                  statuses.contains(job.status), job.conclusion == nil || conclusions.contains(job.conclusion!) else { throw WorkflowReceiptError.identity }
        }
        let active = accountJobs.filter { $0.conclusion != "skipped" }
        if active.isEmpty && !completed && !requireAccountJobs { return false }
        if receipt.account == "all" {
            guard !active.isEmpty, active.count == accountJobs.count, Set(active.map(\.name)).count == active.count else { throw WorkflowReceiptError.identity }
        } else {
            let target = active.filter { $0.name == prefix + receipt.account }
            let extra = active.filter { $0.name != prefix + receipt.account }
            guard target.count <= 1 else { throw WorkflowReceiptError.identity }
            // GitHub can publish queued jobs before evaluating their account
            // condition. Their eventual skipped conclusion is not yet known.
            if !completed, !extra.isEmpty,
               extra.allSatisfy({ ["queued", "waiting", "pending", "requested"].contains($0.status) && $0.conclusion == nil }) {
                if requireAccountJobs { throw WorkflowReceiptError.protocolError }
                return false
            }
            guard active.count == 1, active[0].name == prefix + receipt.account else { throw WorkflowReceiptError.identity }
        }
        return active.allSatisfy { $0.status == "completed" && $0.conclusion == "success" }
    }
    static func retryable(_ stderr: String) -> Bool {
        let text = stderr.lowercased()
        if ["401", "403", "429", "rate limit", "authentication", "bad credentials"].contains(where: text.contains) { return false }
        return ["timeout", "timed out", "connection", "network", "temporary", "tls", "eof", "502", "503", "504", "500", "no such host"].contains(where: text.contains)
    }
}
