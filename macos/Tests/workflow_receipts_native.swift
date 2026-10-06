import Foundation

extension ManualAccountsNativeTests {
    static func workflowReceiptFixtures(root: URL) throws {
        let repo = "NewBoringMan/Glados-Railgun-checkin"
        let id = 37_000_000_001
        try expect(WorkflowRunRules.dispatchedID(stdout: "Created workflow_dispatch event for master at https://github.com/\(repo)/actions/runs/\(id)\n", repository: repo) == id, "dispatch URL binds exact ID")
        try rejects("no recent-run fallback") { _ = try WorkflowRunRules.dispatchedID(stdout: "Dispatched", repository: repo) }
        try rejects("different repo rejected") { _ = try WorkflowRunRules.dispatchedID(stdout: "https://github.com/other/repo/actions/runs/1", repository: repo) }
        try rejects("ambiguous run URLs rejected") { _ = try WorkflowRunRules.dispatchedID(stdout: "https://github.com/\(repo)/actions/runs/1\nhttps://github.com/\(repo)/actions/runs/2", repository: repo) }
        try rejects("run URL query rejected") { _ = try WorkflowRunRules.dispatchedID(stdout: "https://github.com/\(repo)/actions/runs/1?account=other", repository: repo) }
        try rejects("non numeric ID rejected") { _ = try WorkflowRunRules.runID("1; command") }
        try expect(!WorkflowRunRules.retryable("HTTP 429 rate limit"), "rate limit not retried")
        try expect(!WorkflowRunRules.retryable("HTTP 401 bad credentials network error"), "auth error not retried")
        try expect(WorkflowRunRules.retryable("HTTP 503 connection timeout"), "temporary GET error retryable")

        var receipt = WorkflowRunReceipt(workflow: "gladosStatus.yml", account: firstKey, dispatchedAt: "2026-10-01T12:00:00Z", runID: id)
        let base: [String: Any] = ["id": id, "path": ".github/workflows/gladosStatus.yml", "head_branch": "master", "event": "workflow_dispatch", "created_at": "2026-10-01T12:00:01Z", "status": "completed", "conclusion": "success"]
        func data(_ object: [String: Any]) throws -> Data { try JSONSerialization.data(withJSONObject: object) }
        _ = try WorkflowRunRules.verify(run: data(base), receipt: receipt)
        try expect(true, "correct workflow metadata accepted")
        for (field, value) in [("id", true as Any), ("path", ".github/workflows/gladosAccounts.yml"), ("head_branch", "candidate"), ("event", "schedule"), ("created_at", "2026-10-01T11:00:00Z"), ("created_at", "2026-10-01T13:00:00Z")] {
            var wrong = base; wrong[field] = value
            try rejects("wrong metadata rejected: " + field) { _ = try WorkflowRunRules.verify(run: data(wrong), receipt: receipt) }
        }
        let matching: [String: Any] = ["name": "GLaDOS status " + firstKey, "status": "completed", "conclusion": "success"]
        let other: [String: Any] = ["name": "GLaDOS status " + secondKey, "status": "completed", "conclusion": "success"]
        let skipped: [String: Any] = ["name": "GLaDOS status " + secondKey, "status": "completed", "conclusion": "skipped"]
        try expect(WorkflowRunRules.verify(jobs: data(["total_count": 2, "jobs": [matching, skipped]]), receipt: receipt, completed: true), "only selected account job active")
        try rejects("different account job rejected") { _ = try WorkflowRunRules.verify(jobs: data(["total_count": 1, "jobs": [other]]), receipt: receipt, completed: true) }
        try rejects("additional active account job rejected") { _ = try WorkflowRunRules.verify(jobs: data(["total_count": 2, "jobs": [matching, other]]), receipt: receipt, completed: true) }
        for state in ["queued", "waiting", "pending", "requested"] {
            let undecided: [String: Any] = ["name": "GLaDOS status " + secondKey, "status": state, "conclusion": NSNull()]
            try expect(!WorkflowRunRules.verify(jobs: data(["total_count": 2, "jobs": [matching, undecided]]), receipt: receipt, completed: false), "unselected job awaiting condition keeps polling: " + state)
            try rejects("completed run cannot retain undecided extra job: " + state) { _ = try WorkflowRunRules.verify(jobs: data(["total_count": 2, "jobs": [matching, undecided]]), receipt: receipt, completed: true) }
            try rejects("manual run ID requires settled account ownership: " + state) { _ = try WorkflowRunRules.verify(jobs: data(["total_count": 2, "jobs": [matching, undecided]]), receipt: receipt, completed: false, requireAccountJobs: true) }
        }
        let wrongRunning: [String: Any] = ["name": "GLaDOS status " + secondKey, "status": "in_progress", "conclusion": NSNull()]
        try rejects("genuinely running other account is rejected immediately") { _ = try WorkflowRunRules.verify(jobs: data(["total_count": 2, "jobs": [matching, wrongRunning]]), receipt: receipt, completed: false) }
        try rejects("truncated jobs list rejected") { _ = try WorkflowRunRules.verify(jobs: data(["total_count": 3, "jobs": [matching, skipped]]), receipt: receipt, completed: true) }
        try expect(!WorkflowRunRules.verify(jobs: data(["total_count": 0, "jobs": []]), receipt: receipt, completed: false), "not yet planned jobs keep polling")
        try rejects("unowned manual ID not bound before jobs appear") { _ = try WorkflowRunRules.verify(jobs: data(["total_count": 0, "jobs": []]), receipt: receipt, completed: false, requireAccountJobs: true) }
        receipt.account = "all"
        try expect(WorkflowRunRules.verify(jobs: data(["total_count": 2, "jobs": [matching, other]]), receipt: receipt, completed: true), "all-account completed jobs accepted")
        try rejects("all-account task cannot contain skipped account") { _ = try WorkflowRunRules.verify(jobs: data(["total_count": 2, "jobs": [matching, skipped]]), receipt: receipt, completed: true) }

        let vault = FixtureVault()
        let directory = root.appendingPathComponent("WorkflowReceipts")
        let store = try LocalManualAccountStore(directory: directory, vault: vault)
        try store.saveCapture(session(), label: "fixture", enabled: true, autoExchange: false, pendingPublication: true)
        try expect(store.records()[firstKey]?.readState == "captured", "local read receipt durable")
        try store.recordPublication(key: firstKey, state: "acknowledged")
        let (intent, firstDispatch) = try store.prepareWorkflow("gladosStatus.yml", account: firstKey)
        try expect(firstDispatch && intent.runID == nil, "intent saved before first dispatch")
        let reopened = try LocalManualAccountStore(directory: directory, vault: vault)
        let (unknown, redispatch) = try reopened.prepareWorkflow("gladosStatus.yml", account: firstKey)
        try expect(!redispatch && unknown.id == intent.id, "restart with unknown dispatch never posts again")
        var bound = unknown; bound.runID = id; bound.dispatchAcknowledged = true; bound.queryState = .interrupted
        try reopened.saveWorkflow(bound)
        let (retry, shouldDispatch) = try store.prepareWorkflow("gladosStatus.yml", account: firstKey)
        try expect(!shouldDispatch && retry.runID == id, "GET interruption resumes same ID")
        try expect(store.records()[firstKey]?.publicationState == "acknowledged", "GET error preserves acknowledged publication")
        try expect(store.session(for: firstKey) == session(), "GET error preserves saved credentials")
        var substituted = retry; substituted.runID = id + 1
        try rejects("bound run ID cannot be changed") { try store.saveWorkflow(substituted) }
        let credential = retry.credentialID
        try store.saveCapture(session(), label: "fixture", enabled: true, autoExchange: false, pendingPublication: true)
        let (afterCapture, duplicateDispatch) = try store.prepareWorkflow("gladosStatus.yml", account: firstKey)
        try expect(!duplicateDispatch && afterCapture.credentialID == credential && store.records()[firstKey]?.credentialID != credential, "old receipt remains bound to old credential after capture")
        bound.queryState = .succeeded; bound.conclusion = "success"; try store.saveWorkflow(bound)
        let (next, newDispatch) = try store.prepareWorkflow("gladosStatus.yml", account: firstKey)
        try expect(newDispatch && next.id != bound.id && next.credentialID == nil, "resolved task permits new explicit query without claiming unpublished credential")
        let bytes = try String(contentsOf: directory.appendingPathComponent("accounts.json"), encoding: .utf8)
        try expect(!bytes.contains("fixture-session") && !bytes.contains("fixture-device"), "receipts contain no credential contents")
    }
}
