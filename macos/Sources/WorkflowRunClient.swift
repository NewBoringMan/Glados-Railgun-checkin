import Foundation

extension GitHubClient {
    func triggerWorkflow(_ workflow: String, account: String, replaceUnidentifiedStatus: Bool = false) throws -> WorkflowRunReceipt {
        guard branch == "master", let receiptStore else { throw ManualAccountError.storage }
        var (receipt, shouldDispatch) = try receiptStore.prepareWorkflow(workflow, account: account, replaceUnidentifiedStatus: replaceUnidentifiedStatus)
        if !shouldDispatch {
            guard receipt.runID != nil else {
                throw workflow == "gladosStatus.yml" ? WorkflowReceiptError.statusDispatchUncertain : WorkflowReceiptError.missingRunID
            }
            return receipt
        }
        do {
            let input = try JSONSerialization.data(withJSONObject: ["ref": "master", "inputs": ["account": account]])
            let result = try ProcessRunner.run(gh, [
                "api", "--hostname", "github.com", "--method", "POST",
                "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2026-03-10",
                "repos/\(repo)/actions/workflows/\(workflow)/dispatches", "--input", "-"
            ], input: input, timeout: 45)
            receipt.dispatchAcknowledged = result.exitCode == 0
            // Retain the acknowledgement even if the response cannot be decoded.
            // No exception below can cause a second POST for this intent.
            try receiptStore.saveWorkflow(receipt)
            guard result.exitCode == 0 else { throw WorkflowReceiptError.dispatchUncertain }
            receipt.runID = try WorkflowRunRules.dispatchedID(response: Data(result.stdout.utf8), repository: repo)
            try receiptStore.saveWorkflow(receipt)
            return receipt
        } catch {
            receipt.queryState = .interrupted
            try receiptStore.saveWorkflow(receipt)
            let receiptError = (error as? WorkflowReceiptError) ?? .dispatchUncertain
            if workflow == "gladosStatus.yml", [.missingRunID, .dispatchUncertain].contains(receiptError) {
                throw WorkflowReceiptError.statusDispatchUncertain
            }
            throw receiptError
        }
    }

    // Exactly one dispatch above. Retries here are bounded GETs of that ID only.
    private func workflowGET(_ arguments: [String], stage: WorkflowQueryStage, deadline: Date) throws -> Data {
        // A measured metadata GET on the user's Mac took 14.42 seconds. The
        // old six-second limit aborted valid responses; logs may take longer
        // still because gh can download an archive or fall back to job logs.
        let attemptLimit: TimeInterval = stage == .logs ? 60 : 30
        for attempt in 0..<3 {
            let remaining = deadline.timeIntervalSinceNow
            guard remaining > 0 else { throw WorkflowReceiptError.queryInterrupted(stage) }
            do {
                let result = try ProcessRunner.run(gh, arguments, timeout: min(attemptLimit, remaining))
                if result.exitCode == 0 { return Data(result.stdout.utf8) }
                guard WorkflowRunRules.retryable(result.stderr) else { throw WorkflowReceiptError.protocolError }
            } catch let error as WorkflowReceiptError { throw error }
            catch { /* Process timeout is also a failed GET, never a dispatch. */ }
            if attempt < 2 {
                let remaining = deadline.timeIntervalSinceNow
                guard remaining > 0 else { break }
                Thread.sleep(forTimeInterval: min(1, remaining))
            }
        }
        throw WorkflowReceiptError.queryInterrupted(stage)
    }

    /// An ID supplied for an interrupted dispatch is checked before it becomes
    /// durable. Failed guesses cannot replace or poison an already bound ID.
    func bindExistingRun(_ id: Int, to original: WorkflowRunReceipt) throws -> WorkflowRunReceipt {
        guard original.runID == nil, let receiptStore else { throw WorkflowReceiptError.identity }
        var receipt = original; receipt.runID = id
        let deadline = Date().addingTimeInterval(180)
        let run = try WorkflowRunRules.verify(run: workflowGET(["api", "--hostname", "github.com", "repos/\(repo)/actions/runs/\(id)"], stage: .run, deadline: deadline), receipt: receipt)
        let jobs = try workflowGET(["api", "--hostname", "github.com", "repos/\(repo)/actions/runs/\(id)/jobs?per_page=100"], stage: .jobs, deadline: deadline)
        _ = try WorkflowRunRules.verify(jobs: jobs, receipt: receipt, completed: run.status == "completed", requireAccountJobs: true)
        receipt.dispatchAcknowledged = true; receipt.queryState = .pending
        try receiptStore.saveWorkflow(receipt)
        return receipt
    }

    func waitForRun(_ original: WorkflowRunReceipt, timeout: TimeInterval = 480) throws -> (String, String, String) {
        guard let receiptStore, let id = original.runID else { throw WorkflowReceiptError.missingRunID }
        var receipt = original
        let deadline = Date().addingTimeInterval(timeout)
        do {
            while Date() < deadline {
                receipt.queryStage = .run
                let run = try WorkflowRunRules.verify(run: workflowGET(["api", "--hostname", "github.com", "repos/\(repo)/actions/runs/\(id)"], stage: .run, deadline: deadline), receipt: receipt)
                receipt.queryStage = .jobs
                let passed = try WorkflowRunRules.verify(jobs: workflowGET(["api", "--hostname", "github.com", "repos/\(repo)/actions/runs/\(id)/jobs?per_page=100"], stage: .jobs, deadline: deadline), receipt: receipt, completed: run.status == "completed")
                if run.status == "completed" {
                    guard let conclusion = run.conclusion else { throw WorkflowReceiptError.protocolError }
                    guard conclusion != "success" || passed else { throw WorkflowReceiptError.identity }
                    let logs: String
                    if receipt.workflow == "gladosStatus.yml" {
                        // Keep the verified remote outcome even when logs are
                        // slow. This receipt stays unresolved until logs arrive,
                        // so restarting or refreshing resumes this exact ID.
                        receipt.conclusion = conclusion
                        receipt.queryState = .pending
                        receipt.queryStage = .logs
                        try receiptStore.saveWorkflow(receipt)
                        logs = String(decoding: try workflowGET(["run", "view", String(id), "--repo", "github.com/\(repo)", "--log"], stage: .logs, deadline: deadline), as: UTF8.self)
                    } else { logs = "" }
                    receipt.conclusion = conclusion
                    receipt.queryState = conclusion == "success" && passed ? .succeeded : .failed
                    receipt.queryStage = nil
                    try receiptStore.saveWorkflow(receipt)
                    return (conclusion, "https://github.com/\(repo)/actions/runs/\(id)", logs)
                }
                let remaining = deadline.timeIntervalSinceNow
                if remaining > 0 { Thread.sleep(forTimeInterval: min(3, remaining)) }
            }
            throw WorkflowReceiptError.interrupted
        } catch {
            receipt.queryState = (error as? WorkflowReceiptError) == .identity ? .rejected : .interrupted
            try receiptStore.saveWorkflow(receipt)
            throw error
        }
    }
}
