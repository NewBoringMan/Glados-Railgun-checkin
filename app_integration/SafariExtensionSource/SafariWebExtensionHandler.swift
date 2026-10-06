import Foundation
import SafariServices
import Network

final class SafariWebExtensionHandler: NSObject, NSExtensionRequestHandling {
    func beginRequest(with context: NSExtensionContext) {
        guard
            let item = context.inputItems.first as? NSExtensionItem,
            let userInfo = item.userInfo,
            let rawMessage = userInfo[SFExtensionMessageKey] as? [String: Any]
        else {
            complete(context, ["ok": false, "reason": "invalid_native_message"])
            return
        }

        do {
            let validated = try CaptureMessage(rawMessage)
            NativeForwarder.forward(validated.payload, port: validated.port) { result in
                self.complete(context, result)
            }
        } catch {
            complete(context, ["ok": false, "reason": error.localizedDescription])
        }
    }

    private func complete(_ context: NSExtensionContext, _ responseMessage: [String: Any]) {
        let response = NSExtensionItem()
        response.userInfo = [SFExtensionMessageKey: responseMessage]
        context.completeRequest(returningItems: [response], completionHandler: nil)
    }
}

private struct CaptureMessage {
    let port: NWEndpoint.Port
    let payload: Data

    init(_ message: [String: Any]) throws {
        guard (message["type"] as? String) == "CAPTURE_ACCOUNT" else {
            throw BridgeError("unsupported_message")
        }
        guard let token = message["token"] as? String,
              token.range(of: "^[A-Fa-f0-9]{64}$", options: .regularExpression) != nil else {
            throw BridgeError("invalid_token")
        }
        guard let rawPort = message["port"] as? NSNumber,
              rawPort.doubleValue == Double(rawPort.intValue),
              rawPort.intValue >= 1024,
              rawPort.intValue <= 65535,
              let port = NWEndpoint.Port(rawValue: UInt16(rawPort.intValue)) else {
            throw BridgeError("invalid_port")
        }
        guard let host = message["host"] as? String, Self.allowed(host: host) else {
            throw BridgeError("invalid_host")
        }
        guard let pageText = message["pageUrl"] as? String,
              let pageURL = URL(string: pageText),
              pageURL.scheme == "https",
              pageURL.user == nil, pageURL.password == nil, pageURL.port == nil,
              pageURL.host?.lowercased() == host.lowercased(),
              Self.allowed(host: pageURL.host ?? "") else {
            throw BridgeError("invalid_page_url")
        }
        guard let userAgent = message["userAgent"] as? String,
              !userAgent.trimmingCharacters(in: .whitespaces).isEmpty,
              userAgent.utf8.count <= 2048,
              userAgent.unicodeScalars.allSatisfy({ $0.value >= 0x20 && $0.value <= 0x7E }) else {
            throw BridgeError("invalid_user_agent")
        }
        guard let rawCookies = message["cookies"] as? [[String: Any]],
              !rawCookies.isEmpty, rawCookies.count <= 256 else {
            throw BridgeError("missing_or_invalid_cookie")
        }
        var cookies: [[String: Any]] = []
        var values: [String: String] = [:]
        for cookie in rawCookies {
            guard let name = cookie["name"] as? String,
                  name.range(of: "^[!#$%&'*+\\-.^_`|~0-9A-Za-z:]+$", options: .regularExpression) != nil,
                  let value = cookie["value"] as? String, value.utf8.count <= 16 * 1024,
                  value.unicodeScalars.allSatisfy({ $0.value >= 0x21 && $0.value <= 0x7E && $0.value != 0x3B && $0.value != 0x2C }),
                  values[name] == nil,
                  let domain = cookie["domain"] as? String,
                  domain.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: ".")) == host.lowercased(),
                  let path = cookie["path"] as? String, path.hasPrefix("/"),
                  cookie["partitionKey"] == nil, cookie["partitioned"] as? Bool != true else {
                throw BridgeError("missing_or_invalid_cookie")
            }
            values[name] = value
            var normalized: [String: Any] = ["name": name, "value": value, "domain": domain, "path": path]
            for key in ["hostOnly", "secure", "httpOnly", "session"] {
                if let flag = cookie[key] as? Bool { normalized[key] = flag }
            }
            if let expiry = cookie["expirationDate"] as? NSNumber {
                guard expiry.doubleValue.isFinite else { throw BridgeError("invalid_cookie_expiry") }
                normalized["expirationDate"] = expiry
            }
            cookies.append(normalized)
        }
        let hasGld = !(values["gld:sess"] ?? "").isEmpty && !(values["gld:sess.sig"] ?? "").isEmpty
        let hasKoa = !(values["koa:sess"] ?? "").isEmpty && !(values["koa:sess.sig"] ?? "").isEmpty
        guard hasGld || hasKoa,
              (values["gld:sess"] == nil && values["gld:sess.sig"] == nil) || hasGld,
              values.map({ $0.key.utf8.count + $0.value.utf8.count + 3 }).reduce(0, +) <= 32 * 1024 else {
            throw BridgeError("missing_or_invalid_cookie")
        }
        var pageComponents = URLComponents(url: pageURL, resolvingAgainstBaseURL: false)
        pageComponents?.query = nil
        pageComponents?.fragment = nil
        guard let safePage = pageComponents?.url?.absoluteString else { throw BridgeError("invalid_page_url") }
        let forwarded: [String: Any] = [
            "type": "CAPTURE_ACCOUNT", "token": token, "port": rawPort.intValue,
            "host": host.lowercased(), "pageUrl": safePage,
            "userAgent": userAgent.trimmingCharacters(in: .whitespaces), "cookies": cookies,
        ]
        guard JSONSerialization.isValidJSONObject(forwarded) else {
            throw BridgeError("invalid_json")
        }
        var data = try JSONSerialization.data(withJSONObject: forwarded, options: [])
        guard data.count <= 64 * 1024 else { throw BridgeError("payload_too_large") }
        data.append(0x0A)
        self.port = port
        self.payload = data
    }

    private static func allowed(host: String) -> Bool {
        ["glados.cloud", "railgun.info"].contains(host.lowercased())
    }
}

private struct BridgeError: LocalizedError {
    let code: String
    init(_ code: String) { self.code = code }
    var errorDescription: String? { code }
}

private enum NativeForwarder {
    static func forward(_ payload: Data, port: NWEndpoint.Port, completion: @escaping ([String: Any]) -> Void) {
        let queue = DispatchQueue(label: "com.enoch.glados.safari.native-forwarder")
        let connection = NWConnection(host: "127.0.0.1", port: port, using: .tcp)
        let lock = NSLock()
        var completed = false

        func finish(_ value: [String: Any]) {
            lock.lock()
            defer { lock.unlock() }
            guard !completed else { return }
            completed = true
            connection.cancel()
            completion(value)
        }

        connection.stateUpdateHandler = { state in
            switch state {
            case .ready:
                connection.send(content: payload, completion: .contentProcessed { error in
                    if let error {
                        finish(["ok": false, "reason": "native_send_failed: \(error.localizedDescription)"])
                        return
                    }
                    connection.receive(minimumIncompleteLength: 1, maximumLength: 4096) { data, _, _, error in
                        if let error {
                            finish(["ok": false, "reason": "native_receive_failed: \(error.localizedDescription)"])
                            return
                        }
                        guard let data,
                              let response = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                            finish(["ok": false, "reason": "invalid_bridge_response"])
                            return
                        }
                        finish(response)
                    }
                })
            case .failed(let error):
                finish(["ok": false, "reason": "native_connect_failed: \(error.localizedDescription)"])
            case .cancelled:
                break
            default:
                break
            }
        }

        connection.start(queue: queue)
        queue.asyncAfter(deadline: .now() + 8) {
            finish(["ok": false, "reason": "native_bridge_timeout"])
        }
    }
}
