// Internal executable of GLaDOS Account Center, not a second .app.
// Status/delivery never request consent, open a window, activate an application,
// or read credentials. Permission is requested only by the main UI's user button.
import Foundation
import UserNotifications

@main
struct RefreshNotificationMain {
    static func output(_ value: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject:value, options:[.sortedKeys]) else { return }
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([10]))
    }

    static func settings(_ center: UNUserNotificationCenter) async -> UNNotificationSettings {
        await withCheckedContinuation { continuation in
            center.getNotificationSettings { continuation.resume(returning:$0) }
        }
    }

    static func authorization(_ status: UNAuthorizationStatus) -> String {
        switch status {
        case .authorized:return "authorized"
        case .provisional:return "provisional"
        case .denied:return "denied"
        case .notDetermined:return "not_determined"
        @unknown default:return "unavailable"
        }
    }

    static func main() async {
        guard CommandLine.arguments.count == 1,
              Bundle.main.bundleIdentifier == "com.enoch.glados-account-center" else {
            output(["ok":false,"reason":"installed_bundle_required"]);return
        }
        do {
            var bytes = Data()
            while bytes.count <= 4096 {
                let part = try FileHandle.standardInput.read(upToCount:min(1024,4097-bytes.count)) ?? Data()
                if part.isEmpty { break }
                bytes.append(part)
            }
            guard bytes.count <= 4096,
                  let request = try JSONSerialization.jsonObject(with:bytes) as? [String:Any],
                  let op = request["op"] as? String,
                  ["status","send"].contains(op) else {
                output(["ok":false,"reason":"invalid_notice"]);return
            }
            let center = UNUserNotificationCenter.current()
            let allowed = authorization(await settings(center).authorizationStatus)
            if op == "status" {
                guard Set(request.keys) == ["op"] else {
                    output(["ok":false,"reason":"invalid_notice"]);return
                }
                output(["ok":true,"authorization":allowed]);return
            }
            guard Set(request.keys) == ["op","id","kind","count"],
                  let identifier = request["id"] as? String,
                  identifier.range(of:"^glados-refresh-[a-f0-9]{32}$",options:.regularExpression) != nil,
                  let kind = request["kind"] as? String,
                  let number = request["count"] as? NSNumber,
                  CFGetTypeID(number) != CFBooleanGetTypeID(),
                  number.doubleValue == Double(number.intValue), (1...500).contains(number.intValue) else {
                output(["ok":false,"reason":"invalid_notice"]);return
            }
            let body:String
            switch kind {
            case "account_manual":body = "有 \(number.intValue) 个账号需要人工处理。请打开 Account Center → 登录维护查看。"
            case "shared_dependency":body = "收码邮箱、Mail 或凭据访问需要处理。队列已暂停，不会反复向全部账号发码。"
            case "verification_pending":body = "账号登录后的云端验证尚未确认。已保留恢复记录，不会重复登录或盲目覆盖。"
            case "test":body = "登录维护通知测试。这条通知没有发起登录、签到或兑换。"
            default:output(["ok":false,"reason":"invalid_notice"]);return
            }
            guard ["authorized","provisional"].contains(allowed) else {
                output(["ok":false,"reason":allowed == "denied" ? "notification_denied" : "notification_permission_required"]);return
            }
            let content = UNMutableNotificationContent()
            content.title = "GLaDOS 登录维护"
            content.body = body
            content.threadIdentifier = "glados-login-maintenance"
            // No account email, code, Cookie, URL or raw server error is displayed.
            let requestObject = UNNotificationRequest(identifier:identifier,content:content,
                trigger:UNTimeIntervalNotificationTrigger(timeInterval:1,repeats:false))
            let queued:Bool = await withCheckedContinuation { continuation in
                center.add(requestObject) { error in continuation.resume(returning:error == nil) }
            }
            output(queued ? ["ok":true,"accepted_by_system":true]
                          : ["ok":false,"reason":"notification_service_unavailable"])
        } catch {
            output(["ok":false,"reason":"invalid_notice"])
        }
    }
}
