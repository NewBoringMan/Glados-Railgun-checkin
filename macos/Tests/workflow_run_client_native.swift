import Foundation

// These types replace only process execution and the otherwise unrelated App
// client in the native test target. WorkflowRunClient.swift itself is compiled
// unchanged. No fixture launches a process, reads gh auth, or makes a request.
struct CommandResult {
    private let output: String
    let stderr: String
    let exitCode: Int32
    private let beforeOutputRead: (() -> Void)?
    var stdout: String { beforeOutputRead?(); return output }

    init(stdout: String, stderr: String = "", exitCode: Int32 = 0, beforeOutputRead: (() -> Void)? = nil) {
        output = stdout; self.stderr = stderr; self.exitCode = exitCode
        self.beforeOutputRead = beforeOutputRead
    }
}

enum FixtureCommandError: Error { case timeout, unexpectedCommand }

enum ProcessRunner {
    struct Invocation {
        let executable: URL
        let arguments: [String]
        let input: Data?
        let timeout: TimeInterval
        var isDispatch: Bool { arguments.first == "api" && arguments.contains("POST") }
    }
    static var invocations: [Invocation] = []
    static var handler: ((Invocation) throws -> CommandResult)?

    static func reset() { invocations = []; handler = nil }
    static func run(_ executable: URL, _ arguments: [String], input: Data? = nil,
                    environment: [String: String]? = nil, timeout: TimeInterval = 180) throws -> CommandResult {
        let invocation = Invocation(executable: executable, arguments: arguments, input: input, timeout: timeout)
        invocations.append(invocation)
        guard let handler else { throw FixtureCommandError.unexpectedCommand }
        return try handler(invocation)
    }
}

final class GitHubClient {
    let repo: String
    let branch: String
    let gh = URL(fileURLWithPath: "/fixture-only/gh-never-executed")
    let receiptStore: LocalManualAccountStore?
    init(repo: String, branch: String = "master", receiptStore: LocalManualAccountStore?) {
        self.repo = repo; self.branch = branch; self.receiptStore = receiptStore
    }
}

extension ManualAccountsNativeTests {
    static func workflowRunClientFixtures(root: URL) throws {
        let repo = "NewBoringMan/Glados-Railgun-checkin"
        let id = 37_000_000_021
        func response() throws -> String {
            let value: [String: Any] = ["workflow_run_id": id,
                "run_url": "https://api.github.com/repos/\(repo)/actions/runs/\(id)",
                "html_url": "https://github.com/\(repo)/actions/runs/\(id)"]
            return String(decoding: try JSONSerialization.data(withJSONObject: value), as: UTF8.self)
        }
        func assertDispatch(_ call: ProcessRunner.Invocation, workflow: String, account: String) throws {
            try expect(call.isDispatch && call.arguments.contains("repos/\(repo)/actions/workflows/\(workflow)/dispatches"), "dispatch uses exact workflow REST endpoint")
            try expect(call.arguments.contains("X-GitHub-Api-Version: 2026-03-10") || call.arguments.contains("X-GitHub-Api-Version:2026-03-10"), "dispatch pins API version with structured response")
            try expect(call.arguments.contains("--input") && call.arguments.contains("-"), "dispatch payload travels on stdin")
            guard let input = call.input,
                  let payload = try JSONSerialization.jsonObject(with: input) as? [String: Any],
                  let inputs = payload["inputs"] as? [String: Any] else {
                throw NativeTestFailure.failed("dispatch has JSON stdin")
            }
            try expect(Set(payload.keys) == ["ref", "inputs"] && payload["ref"] as? String == "master", "dispatch targets master with exact body fields")
            try expect(Set(inputs.keys) == ["account"] && inputs["account"] as? String == account, "dispatch pins selected account input")
        }
        defer { ProcessRunner.reset() }

        // Every outcome is exercised for both workflows. An unidentified task
        // survives client/store recreation and never triggers an automatic POST.
        for workflow in ["gladosStatus.yml", "gladosAccounts.yml"] {
            for outcome in ["success", "empty", "nonzero", "timeout"] {
                ProcessRunner.reset()
                let directory = root.appendingPathComponent("Client-\(workflow)-\(outcome)")
                let vault = FixtureVault()
                let store = try LocalManualAccountStore(directory: directory, vault: vault)
                try store.saveCapture(session(), label: "fixture", enabled: true, autoExchange: false, pendingPublication: true)
                try store.recordPublication(key: firstKey, state: "acknowledged")
                let accountBefore = try store.records()
                let client = GitHubClient(repo: repo, receiptStore: store)
                var observedAckBeforeParsing = false
                var observedOutputRead = false
                ProcessRunner.handler = { call in
                    try assertDispatch(call, workflow: workflow, account: firstKey)
                    let before = try LocalManualAccountStore(directory: directory, vault: vault).workflowRecords()
                    try expect(before.count == 1 && before[0].runID == nil && !before[0].dispatchAcknowledged, "intent is durable before any POST leaves client")
                    if outcome == "timeout" { throw FixtureCommandError.timeout }
                    let output: String
                    if outcome == "empty" { output = "" } else { output = try response() }
                    return CommandResult(stdout: output,
                        stderr: outcome == "nonzero" ? "HTTP 403 synthetic rejection" : "",
                        exitCode: outcome == "nonzero" ? 1 : 0,
                        beforeOutputRead: {
                            observedOutputRead = true
                            let receipts = try? LocalManualAccountStore(directory: directory, vault: vault).workflowRecords()
                            observedAckBeforeParsing = receipts?.count == 1 && receipts?.first?.dispatchAcknowledged == true && receipts?.first?.runID == nil
                        })
                }
                if outcome == "success" {
                    let receipt = try client.triggerWorkflow(workflow, account: firstKey)
                    try expect(receipt.runID == id && receipt.dispatchAcknowledged, "successful REST response binds exact run")
                } else {
                    try rejects("dispatch failure remains visible: " + outcome) { _ = try client.triggerWorkflow(workflow, account: firstKey) }
                }
                try expect(ProcessRunner.invocations.count == 1 && ProcessRunner.invocations.filter(\.isDispatch).count == 1, "one POST only for outcome: " + outcome)
                let receipts = try store.workflowRecords()
                try expect(receipts.count == 1, "one durable receipt for one dispatch")
                if outcome == "success" || outcome == "empty" {
                    try expect(observedOutputRead && observedAckBeforeParsing, "exit-zero acknowledgement is persisted before stdout parsing")
                    try expect(receipts[0].dispatchAcknowledged, "acknowledged dispatch remains recorded when response is empty")
                } else {
                    try expect(!receipts[0].dispatchAcknowledged && receipts[0].runID == nil, "nonzero or timeout cannot claim dispatch acknowledgement or bind response")
                }
                if outcome != "success" {
                    try expect(receipts[0].runID == nil && !receipts[0].isResolved, "uncertain dispatch remains recoverable without invented ID")
                }
                let restartedStore = try LocalManualAccountStore(directory: directory, vault: vault)
                let restarted = GitHubClient(repo: repo, receiptStore: restartedStore)
                if outcome == "success" {
                    try expect(restarted.triggerWorkflow(workflow, account: firstKey).runID == id, "restart reuses bound task")
                } else {
                    try rejects("restart does not repeat unidentified dispatch") { _ = try restarted.triggerWorkflow(workflow, account: firstKey) }
                }
                try expect(ProcessRunner.invocations.count == 1, "restart performs no second POST or run-list guess")
                try expect(restartedStore.records() == accountBefore && restartedStore.session(for: firstKey) == session(), "dispatch outcomes preserve local credentials and publication receipt")
                if workflow == "gladosAccounts.yml" {
                    try rejects("checkin cannot request status replacement") {
                        _ = try restarted.triggerWorkflow(workflow, account: firstKey, replaceUnidentifiedStatus: true)
                    }
                    try expect(ProcessRunner.invocations.count == 1, "checkin replacement request does not dispatch")
                }
            }
        }

        ProcessRunner.reset()
        let directory = root.appendingPathComponent("Client-Explicit-Status-Replacement")
        let vault = FixtureVault()
        let store = try LocalManualAccountStore(directory: directory, vault: vault)
        let client = GitHubClient(repo: repo, receiptStore: store)
        ProcessRunner.handler = { call in
            try assertDispatch(call, workflow: "gladosStatus.yml", account: firstKey)
            return CommandResult(stdout: "")
        }
        try rejects("initial empty status response leaves receipt") { _ = try client.triggerWorkflow("gladosStatus.yml", account: firstKey) }
        let original = try store.workflowRecords()[0]
        ProcessRunner.handler = { call in
            try assertDispatch(call, workflow: "gladosStatus.yml", account: firstKey)
            let saved = try LocalManualAccountStore(directory: directory, vault: vault).workflowRecords()
            let old = saved.first { $0.id == original.id }
            try expect(saved.count == 2 && old?.isResolved == true && old?.supersededBy != nil && saved.contains(where: { $0.id == old?.supersededBy && $0.runID == nil }), "old replacement link and new intent are durable before explicit status POST")
            return CommandResult(stdout: try response())
        }
        let replacement = try client.triggerWorkflow("gladosStatus.yml", account: firstKey, replaceUnidentifiedStatus: true)
        try expect(replacement.id != original.id && replacement.runID == id && ProcessRunner.invocations.count == 2, "explicit refresh can replace old unidentified status with one new POST")
        try expect(store.workflowRecords().first(where: { $0.id == original.id })?.supersededBy == replacement.id, "old unidentified status stays linked to replacement")
        try expect(client.triggerWorkflow("gladosStatus.yml", account: firstKey, replaceUnidentifiedStatus: true).id == replacement.id && ProcessRunner.invocations.count == 2, "explicit refresh with known ID never dispatches again")

        // Polling failures and recovery only GET the server-returned ID.
        ProcessRunner.handler = { call in
            try expect(!call.isDispatch && call.arguments == ["api", "repos/\(repo)/actions/runs/\(id)"], "failed query reads only the exact bound run")
            return CommandResult(stdout: "", stderr: "HTTP 403 synthetic rejection", exitCode: 1)
        }
        try rejects("failed GET preserves task for retry") { _ = try client.waitForRun(replacement, timeout: 1) }
        try expect(ProcessRunner.invocations.filter(\.isDispatch).count == 2, "GET failure never retries POST")
        let retry = try client.triggerWorkflow("gladosStatus.yml", account: firstKey)
        try expect(retry.runID == id && retry.queryState == .interrupted, "failed GET resumes original run ID")
        let callsBeforeRecovery = ProcessRunner.invocations.count
        ProcessRunner.handler = { call in
            try expect(!call.isDispatch, "recovery cannot dispatch")
            if call.arguments == ["api", "repos/\(repo)/actions/runs/\(id)"] {
                let value: [String: Any] = ["id": id, "path": ".github/workflows/gladosStatus.yml", "head_branch": "master",
                    "event": "workflow_dispatch", "created_at": replacement.dispatchedAt, "status": "completed", "conclusion": "success"]
                return CommandResult(stdout: String(decoding: try JSONSerialization.data(withJSONObject: value), as: UTF8.self))
            }
            if call.arguments == ["api", "repos/\(repo)/actions/runs/\(id)/jobs?per_page=100"] {
                let value: [String: Any] = ["total_count": 1, "jobs": [["name": "GLaDOS status " + firstKey, "status": "completed", "conclusion": "success"]]]
                return CommandResult(stdout: String(decoding: try JSONSerialization.data(withJSONObject: value), as: UTF8.self))
            }
            if call.arguments == ["run", "view", String(id), "--repo", repo, "--log"] { return CommandResult(stdout: "fixture status logs") }
            throw FixtureCommandError.unexpectedCommand
        }
        let (conclusion, _, logs) = try client.waitForRun(retry, timeout: 1)
        try expect(conclusion == "success" && logs == "fixture status logs" && ProcessRunner.invocations.count == callsBeforeRecovery + 3, "recovery checks metadata and account jobs before reading exact run logs")
        try expect(ProcessRunner.invocations.filter(\.isDispatch).count == 2, "successful recovery also performs no POST")
    }
}
