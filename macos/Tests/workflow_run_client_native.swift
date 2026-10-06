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
            try expect(!call.isDispatch && call.arguments == ["api", "--hostname", "github.com", "repos/\(repo)/actions/runs/\(id)"], "failed query reads only the exact bound run on github.com")
            return CommandResult(stdout: "", stderr: "HTTP 403 synthetic rejection", exitCode: 1)
        }
        try rejects("failed GET preserves task for retry") { _ = try client.waitForRun(replacement, timeout: 1) }
        try expect(ProcessRunner.invocations.filter(\.isDispatch).count == 2, "GET failure never retries POST")
        let retry = try client.triggerWorkflow("gladosStatus.yml", account: firstKey)
        try expect(retry.runID == id && retry.queryState == .interrupted, "failed GET resumes original run ID")
        let callsBeforeRecovery = ProcessRunner.invocations.count
        ProcessRunner.handler = { call in
            try expect(!call.isDispatch, "recovery cannot dispatch")
            if call.arguments == ["api", "--hostname", "github.com", "repos/\(repo)/actions/runs/\(id)"] {
                let value: [String: Any] = ["id": id, "path": ".github/workflows/gladosStatus.yml", "head_branch": "master",
                    "event": "workflow_dispatch", "created_at": replacement.dispatchedAt, "status": "completed", "conclusion": "success"]
                return CommandResult(stdout: String(decoding: try JSONSerialization.data(withJSONObject: value), as: UTF8.self))
            }
            if call.arguments == ["api", "--hostname", "github.com", "repos/\(repo)/actions/runs/\(id)/jobs?per_page=100"] {
                let value: [String: Any] = ["total_count": 1, "jobs": [["name": "GLaDOS status " + firstKey, "status": "completed", "conclusion": "success"]]]
                return CommandResult(stdout: String(decoding: try JSONSerialization.data(withJSONObject: value), as: UTF8.self))
            }
            if call.arguments == ["run", "view", String(id), "--repo", "github.com/" + repo, "--log"] { return CommandResult(stdout: "fixture status logs") }
            throw FixtureCommandError.unexpectedCommand
        }
        let (conclusion, _, logs) = try client.waitForRun(retry, timeout: 1)
        try expect(conclusion == "success" && logs == "fixture status logs" && ProcessRunner.invocations.count == callsBeforeRecovery + 3, "recovery checks metadata and account jobs before reading exact run logs")
        try expect(ProcessRunner.invocations.filter(\.isDispatch).count == 2, "successful recovery also performs no POST")
        try workflowReadBudgetFixtures(root: root)
    }

    static func workflowReadBudgetFixtures(root: URL) throws {
        let repo = "NewBoringMan/Glados-Railgun-checkin"
        let id = 37_000_000_031
        let runArguments = ["api", "--hostname", "github.com", "repos/\(repo)/actions/runs/\(id)"]
        let jobsArguments = ["api", "--hostname", "github.com", "repos/\(repo)/actions/runs/\(id)/jobs?per_page=100"]
        let logArguments = ["run", "view", String(id), "--repo", "github.com/" + repo, "--log"]
        func json(_ value: [String: Any]) throws -> String {
            String(decoding: try JSONSerialization.data(withJSONObject: value), as: UTF8.self)
        }
        func metadata(_ receipt: WorkflowRunReceipt, conclusion: String) throws -> String {
            try json(["id": id, "path": ".github/workflows/gladosStatus.yml", "head_branch": "master",
                "event": "workflow_dispatch", "created_at": receipt.dispatchedAt, "status": "completed", "conclusion": conclusion])
        }
        func jobs(conclusion: String) throws -> String {
            try json(["total_count": 1, "jobs": [["name": "GLaDOS status " + firstKey, "status": "completed", "conclusion": conclusion]]])
        }
        defer { ProcessRunner.reset() }

        for conclusion in ["success", "failure"] {
            ProcessRunner.reset()
            let directory = root.appendingPathComponent("SlowQuery-" + conclusion)
            let vault = FixtureVault()
            let store = try LocalManualAccountStore(directory: directory, vault: vault)
            var (receipt, _) = try store.prepareWorkflow("gladosStatus.yml", account: firstKey)
            receipt.runID = id; receipt.dispatchAcknowledged = true
            try store.saveWorkflow(receipt)
            let original = receipt
            let client = GitHubClient(repo: repo, receiptStore: store)
            var beforeLog: [WorkflowRunReceipt] = []
            ProcessRunner.handler = { call in
                if call.arguments == runArguments {
                    // Model the measured 14.42-second request without delaying
                    // CI: the old six-second budget must fail this request.
                    guard call.timeout >= 14.42 else { throw FixtureCommandError.timeout }
                    return CommandResult(stdout: try metadata(original, conclusion: conclusion))
                }
                if call.arguments == jobsArguments { return CommandResult(stdout: try jobs(conclusion: conclusion)) }
                if call.arguments == logArguments {
                    let saved = try LocalManualAccountStore(directory: directory, vault: vault).workflowRecords()
                    if let current = saved.first(where: { $0.id == original.id }) { beforeLog.append(current) }
                    throw FixtureCommandError.timeout
                }
                throw FixtureCommandError.unexpectedCommand
            }
            do {
                _ = try client.waitForRun(original, timeout: 360)
                throw NativeTestFailure.failed("log timeout must remain visible")
            } catch let error as WorkflowReceiptError {
                try expect(error == .queryInterrupted(.logs), "log timeout identifies its stage")
            }
            let calls = ProcessRunner.invocations
            try expect(calls.count == 5 && calls[0].arguments == runArguments && calls[1].arguments == jobsArguments, "slow metadata is accepted before three log-only retries")
            try expect(calls[0].timeout >= 14.42 && calls[0].timeout <= 30 && calls[1].timeout > 6 && calls[1].timeout <= 30, "metadata and jobs use budgets above six seconds and at most thirty")
            try expect(calls.filter { $0.arguments == logArguments }.allSatisfy { $0.timeout > 30 && $0.timeout <= 60 }, "logs use their independent sixty-second maximum")
            try expect(beforeLog.count == 3 && beforeLog.allSatisfy { $0.conclusion == conclusion && $0.queryState == .pending && $0.queryStage == .logs && !$0.isResolved && $0.runID == id }, "verified terminal result is durable but unresolved before each log attempt")
            let reopened = try LocalManualAccountStore(directory: directory, vault: vault)
            guard let interrupted = try reopened.workflowRecords().first(where: { $0.id == original.id }) else {
                throw NativeTestFailure.failed("interrupted query receipt missing")
            }
            try expect(interrupted.conclusion == conclusion && interrupted.queryState == .interrupted && interrupted.queryStage == .logs && !interrupted.isResolved, "log failure preserves success or failure conclusion across restart")
            let restartedClient = GitHubClient(repo: repo, receiptStore: reopened)
            let resumed = try restartedClient.triggerWorkflow("gladosStatus.yml", account: firstKey, replaceUnidentifiedStatus: true)
            try expect(resumed.id == original.id && resumed.runID == id && ProcessRunner.invocations.count == calls.count, "explicit retry after restart reuses known ID without POST")
            ProcessRunner.handler = { call in
                if call.arguments == runArguments { return CommandResult(stdout: try metadata(original, conclusion: conclusion)) }
                if call.arguments == jobsArguments { return CommandResult(stdout: try jobs(conclusion: conclusion)) }
                if call.arguments == logArguments { return CommandResult(stdout: "fixture recovered status logs") }
                throw FixtureCommandError.unexpectedCommand
            }
            let (recoveredConclusion, _, logs) = try restartedClient.waitForRun(resumed, timeout: 360)
            let recovered = try reopened.workflowRecords().first { $0.id == original.id }
            try expect(recoveredConclusion == conclusion && logs == "fixture recovered status logs", "retry retrieves original task logs")
            try expect(recovered?.conclusion == conclusion && recovered?.isResolved == true && recovered?.queryStage == nil && recovered?.runID == id, "only retrieved logs resolve the receipt and clear its query stage")
            try expect(recovered?.queryState == (conclusion == "success" ? .succeeded : .failed), "failure conclusion is not promoted to success")
            try expect(ProcessRunner.invocations.filter(\.isDispatch).isEmpty, "slow query, log retries and restart recovery never dispatch")
        }

        let directory = root.appendingPathComponent("Query-Shared-Deadline")
        let store = try LocalManualAccountStore(directory: directory, vault: FixtureVault())
        var (receipt, _) = try store.prepareWorkflow("gladosStatus.yml", account: firstKey)
        receipt.runID = id; receipt.dispatchAcknowledged = true
        try store.saveWorkflow(receipt)
        let original = receipt
        let client = GitHubClient(repo: repo, receiptStore: store)
        let exhaustedBudgets: [TimeInterval] = [0, -1]
        for timeout in exhaustedBudgets {
            ProcessRunner.reset()
            try rejects("exhausted query deadline does not start a command") { _ = try client.waitForRun(original, timeout: timeout) }
            try expect(ProcessRunner.invocations.isEmpty, "nonpositive total budget issues no GET or POST")
        }
        ProcessRunner.reset()
        ProcessRunner.handler = { call in
            guard call.arguments == runArguments else { throw FixtureCommandError.unexpectedCommand }
            // Spend only this fixture's tiny remaining budget. Returning a
            // valid metadata response must not create a fresh jobs budget.
            Thread.sleep(forTimeInterval: max(0, call.timeout) + 0.02)
            return CommandResult(stdout: try metadata(original, conclusion: "success"))
        }
        try rejects("later query stage cannot renew an exhausted total budget") { _ = try client.waitForRun(original, timeout: 0.2) }
        try expect(ProcessRunner.invocations.count <= 1 && ProcessRunner.invocations.allSatisfy { $0.arguments == runArguments && $0.timeout > 0 && $0.timeout <= 0.2 }, "short deadline clamps the first timeout and prohibits a subsequent jobs call")
        let pending = try store.workflowRecords().first { $0.id == original.id }
        try expect(pending?.runID == id && pending?.conclusion == nil && pending?.isResolved == false, "metadata alone cannot persist an unverified terminal result")
    }
}
