import Foundation

struct CheckinWatchStatus: Decodable, Sendable {
    let ok: Bool
    let enabled: Bool
    let authorization: String
    let lastCheckedAt: String?
    let pendingNotifications: Int?
    let error: String?
    var authorizationText: String {
        switch authorization {
        case "authorized", "provisional": return "已允许"
        case "denied": return "已拒绝（可在系统设置中修改）"
        case "not_determined", "notDetermined": return "尚未请求"
        default: return "暂不可用"
        }
    }
    static func request(_ action: String, enabled: Bool? = nil) throws -> CheckinWatchStatus {
        guard ["status", "set-enabled", "authorize", "test"].contains(action) else { throw ManualAccountError.storage }
        let executable = Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/GLaDOSAccountCenter")
        let input = try enabled.map { try JSONSerialization.data(withJSONObject: ["enabled": $0]) }
        let result = try ProcessRunner.run(executable, ["--checkin-watch", action], input: input, timeout: action == "authorize" ? 125 : 25)
        guard result.stdout.utf8.count <= 32768,
              let status = try? JSONDecoder().decode(CheckinWatchStatus.self, from: Data(result.stdout.utf8)),
              ["authorized", "provisional", "denied", "not_determined", "notDetermined", "unavailable", "unknown"].contains(status.authorization),
              status.lastCheckedAt == nil || ManualValidation.date(status.lastCheckedAt!) != nil else {
            throw AppError.message("签到失败通知组件暂不可用；原签到计划未改变。")
        }
        return status
    }
}
