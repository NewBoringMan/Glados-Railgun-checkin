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
    private func workflowGET(_ arguments: [String]) throws -> Data {
        let deadline = Date().addingTimeInterval(24)
        for attempt in 0..<3 {
            do {
                let result = try ProcessRunner.run(gh, arguments, timeout: min(6, max(1, deadline.timeIntervalSinceNow)))
                if result.exitCode == 0 { return Data(result.stdout.utf8) }
                guard WorkflowRunRules.retryable(result.stderr) else { throw WorkflowReceiptError.protocolError }
            } catch let error as WorkflowReceiptError { throw error }
            catch { /* Process timeout is also a failed GET, never a dispatch. */ }
            if attempt < 2 && Date() < deadline { Thread.sleep(forTimeInterval: 1) }
        }
        throw WorkflowReceiptError.interrupted
    }

    /// An ID supplied for an interrupted dispatch is checked before it becomes
    /// durable. Failed guesses cannot replace or poison an already bound ID.
    func bindExistingRun(_ id: Int, to original: WorkflowRunReceipt) throws -> WorkflowRunReceipt {
        guard original.runID == nil, let receiptStore else { throw WorkflowReceiptError.identity }
        var receipt = original; receipt.runID = id
        let run = try WorkflowRunRules.verify(run: workflowGET(["api", "repos/\(repo)/actions/runs/\(id)"]), receipt: receipt)
        let jobs = try workflowGET(["api", "repos/\(repo)/actions/runs/\(id)/jobs?per_page=100"])
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
                let run = try WorkflowRunRules.verify(run: workflowGET(["api", "repos/\(repo)/actions/runs/\(id)"]), receipt: receipt)
                let passed = try WorkflowRunRules.verify(jobs: workflowGET(["api", "repos/\(repo)/actions/runs/\(id)/jobs?per_page=100"]), receipt: receipt, completed: run.status == "completed")
                if run.status == "completed" {
                    guard let conclusion = run.conclusion else { throw WorkflowReceiptError.protocolError }
                    guard conclusion != "success" || passed else { throw WorkflowReceiptError.identity }
                    let logs: String
                    if receipt.workflow == "gladosStatus.yml" {
                        logs = String(decoding: try workflowGET(["run", "view", String(id), "--repo", repo, "--log"]), as: UTF8.self)
                    } else { logs = "" }
                    receipt.conclusion = conclusion
                    receipt.queryState = conclusion == "success" && passed ? .succeeded : .failed
                    try receiptStore.saveWorkflow(receipt)
                    return (conclusion, "https://github.com/\(repo)/actions/runs/\(id)", logs)
                }
                Thread.sleep(forTimeInterval: 3)
            }
            throw WorkflowReceiptError.interrupted
        } catch {
            receipt.queryState = (error as? WorkflowReceiptError) == .identity ? .rejected : .interrupted
            try receiptStore.saveWorkflow(receipt)
            throw error
        }
    }
}
