import Foundation

extension ManualAccountsNativeTests {
    static func workflowReceiptFixtures(root: URL) throws {
        let repo = "NewBoringMan/Glados-Railgun-checkin"
        let id = 37_000_000_001
        let dispatch: [String: Any] = ["workflow_run_id": id,
            "run_url": "https://api.github.com/repos/\(repo)/actions/runs/\(id)",
            "html_url": "https://github.com/\(repo)/actions/runs/\(id)"]
        func data(_ object: [String: Any]) throws -> Data { try JSONSerialization.data(withJSONObject: object) }
        try expect(WorkflowRunRules.dispatchedID(response: data(dispatch), repository: repo) == id, "official JSON response binds exact ID")
        for output in ["", "Dispatched", "https://github.com/\(repo)/actions/runs/\(id)", "{}", "[]", "null", "{invalid}"] {
            try rejects("non-response output never infers a run ID: " + output) {
                _ = try WorkflowRunRules.dispatchedID(response: Data(output.utf8), repository: repo)
            }
        }
        for field in ["workflow_run_id", "run_url", "html_url"] {
            var missing = dispatch; missing.removeValue(forKey: field)
            try rejects("dispatch requires field: " + field) { _ = try WorkflowRunRules.dispatchedID(response: data(missing), repository: repo) }
        }
        let invalidIDs: [Any] = [true, String(id), 0, -1, 1.5, 1_000_000_000_000_000_000, NSNull()]
        for value in invalidIDs {
            var wrong = dispatch; wrong["workflow_run_id"] = value
            try rejects("dispatch ID must be a positive bounded JSON integer") { _ = try WorkflowRunRules.dispatchedID(response: data(wrong), repository: repo) }
        }
        for (field, value) in [
            ("run_url", "https://api.github.com/repos/\(repo)/actions/runs/\(id + 1)"),
            ("html_url", "https://github.com/\(repo)/actions/runs/\(id + 1)"),
            ("run_url", "https://api.github.com/repos/other/repo/actions/runs/\(id)"),
            ("html_url", "https://github.com/other/repo/actions/runs/\(id)"),
            ("html_url", "https://github.com/\(repo)-other/actions/runs/\(id)"),
            ("html_url", "https://github.com.example.test/\(repo)/actions/runs/\(id)"),
            ("run_url", "http://api.github.com/repos/\(repo)/actions/runs/\(id)"),
            ("html_url", "https://github.com:443/\(repo)/actions/runs/\(id)"),
            ("html_url", "https://user@github.com/\(repo)/actions/runs/\(id)"),
            ("html_url", "https://github.com/\(repo)/actions/runs/\(id)?account=other"),
            ("run_url", "https://api.github.com/repos/\(repo)/actions/runs/\(id)#fragment"),
            ("html_url", "https://github.com/\(repo)/actions/runs/\(id)/jobs/1")
        ] {
            var wrong = dispatch; wrong[field] = value
            try rejects("dispatch URLs must agree with ID and exact repository: " + value) {
                _ = try WorkflowRunRules.dispatchedID(response: data(wrong), repository: repo)
            }
        }
        try rejects("non numeric ID rejected") { _ = try WorkflowRunRules.runID("1; command") }
        try expect(!WorkflowRunRules.retryable("HTTP 429 rate limit"), "rate limit not retried")
        try expect(!WorkflowRunRules.retryable("HTTP 401 bad credentials network error"), "auth error not retried")
        try expect(WorkflowRunRules.retryable("HTTP 503 connection timeout"), "temporary GET error retryable")

        var receipt = WorkflowRunReceipt(workflow: "gladosStatus.yml", account: firstKey, dispatchedAt: "2026-10-01T12:00:00Z", runID: id)
        let base: [String: Any] = ["id": id, "path": ".github/workflows/gladosStatus.yml", "head_branch": "master", "event": "workflow_dispatch", "created_at": "2026-10-01T12:00:01Z", "status": "completed", "conclusion": "success"]
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
        try unidentifiedStatusReplacementFixtures(root: root)
    }

    static func unidentifiedStatusReplacementFixtures(root: URL) throws {
        let directory = root.appendingPathComponent("StatusReplacement")
        let vault = FixtureVault()
        let store = try LocalManualAccountStore(directory: directory, vault: vault)
        try store.saveCapture(session(), label: "fixture", enabled: true, autoExchange: false, pendingPublication: true)
        try store.recordPublication(key: firstKey, state: "acknowledged")
        let (original, _) = try store.prepareWorkflow("gladosStatus.yml", account: firstKey)
        let originalAccounts = try store.records()
        let legacyJSON = try JSONEncoder().encode(original)
        try expect(!String(decoding: legacyJSON, as: UTF8.self).contains("supersededBy"), "legacy receipt representation omits optional replacement field")
        try expect(JSONDecoder().decode(WorkflowRunReceipt.self, from: legacyJSON).supersededBy == nil, "old receipts decode without replacement field")
        let reopened = try LocalManualAccountStore(directory: directory, vault: vault)
        let (automatic, autoDispatch) = try reopened.prepareWorkflow("gladosStatus.yml", account: firstKey)
        try expect(!autoDispatch && automatic == original, "automatic refresh preserves unidentified status intent")
        let (replacement, shouldDispatch) = try reopened.prepareWorkflow("gladosStatus.yml", account: firstKey, replaceUnidentifiedStatus: true)
        try expect(shouldDispatch && replacement.id != original.id && replacement.runID == nil && !replacement.isResolved, "explicit refresh creates new unidentified status intent")
        let persisted = try LocalManualAccountStore(directory: directory, vault: vault).workflowRecords()
        let superseded = persisted.first { $0.id == original.id }
        try expect(persisted.count == 2 && superseded?.supersededBy == replacement.id && superseded?.isResolved == true, "replacement and retained old receipt survive reopen together")
        try expect(superseded?.workflow == original.workflow && superseded?.account == original.account && superseded?.dispatchedAt == original.dispatchedAt && superseded?.credentialID == original.credentialID, "replacement retains old receipt attribution")
        try expect(replacement.credentialID == original.credentialID && store.records() == originalAccounts && store.session(for: firstKey) == session(), "status replacement preserves account, credential and publication state")
        let (automaticAfter, shouldDispatchAfter) = try store.prepareWorkflow("gladosStatus.yml", account: firstKey)
        try expect(!shouldDispatchAfter && automaticAfter.id == replacement.id, "automatic refresh reuses replacement without another dispatch")
        try rejects("late stale save cannot resurrect superseded intent") { try store.saveWorkflow(original) }
        try expect(store.workflowRecords().first(where: { $0.id == original.id })?.supersededBy == replacement.id, "late stale save preserves replacement link")

        var known = replacement; known.runID = 37_000_000_010; known.dispatchAcknowledged = true
        try store.saveWorkflow(known)
        let (knownAgain, knownDispatch) = try store.prepareWorkflow("gladosStatus.yml", account: firstKey, replaceUnidentifiedStatus: true)
        try expect(!knownDispatch && knownAgain == known, "explicit refresh with known ID resumes same task")
        var invalid = known; invalid.supersededBy = UUID().uuidString.lowercased()
        try rejects("known-ID status receipt cannot be superseded") { try invalid.validate() }
        invalid = original; invalid.supersededBy = original.id
        try rejects("status replacement cannot point to itself") { try invalid.validate() }
        invalid = original; invalid.supersededBy = "not-a-uuid"
        try rejects("replacement must identify another receipt") { try invalid.validate() }

        let beforeCheckin = try Data(contentsOf: directory.appendingPathComponent("accounts.json"))
        try rejects("checkin replacement flag rejected before creating intent") {
            _ = try store.prepareWorkflow("gladosAccounts.yml", account: firstKey, replaceUnidentifiedStatus: true)
        }
        try expect(Data(contentsOf: directory.appendingPathComponent("accounts.json")) == beforeCheckin, "invalid checkin replacement creates no storage mutation")
        let (checkin, _) = try store.prepareWorkflow("gladosAccounts.yml", account: firstKey)
        let (checkinAgain, checkinDispatch) = try reopened.prepareWorkflow("gladosAccounts.yml", account: firstKey)
        try expect(!checkinDispatch && checkinAgain == checkin, "unknown checkin intent remains reusable without dispatch")
        try rejects("existing checkin cannot use status replacement escape") {
            _ = try store.prepareWorkflow("gladosAccounts.yml", account: firstKey, replaceUnidentifiedStatus: true)
        }
        invalid = checkin; invalid.supersededBy = replacement.id
        try rejects("checkin receipt cannot carry replacement metadata") { try invalid.validate() }
        try expect(store.workflowRecords().first(where: { $0.id == checkin.id }) == checkin, "rejected replacement preserves original checkin receipt")
    }
}
