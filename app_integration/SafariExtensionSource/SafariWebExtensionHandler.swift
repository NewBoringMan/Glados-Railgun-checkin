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
              pageURL.host?.lowercased() == host.lowercased(),
              Self.allowed(host: pageURL.host ?? "") else {
            throw BridgeError("invalid_page_url")
        }
        guard let cookies = message["cookies"] as? [String: Any],
              let session = cookies["session"] as? String, !session.isEmpty,
              let signature = cookies["signature"] as? String, !signature.isEmpty,
              session.utf8.count <= 16 * 1024,
              signature.utf8.count <= 16 * 1024 else {
            throw BridgeError("missing_or_invalid_cookie")
        }
        guard JSONSerialization.isValidJSONObject(message) else {
            throw BridgeError("invalid_json")
        }
        var data = try JSONSerialization.data(withJSONObject: message, options: [])
        data.append(0x0A)
        self.port = port
        self.payload = data
    }

    private static func allowed(host: String) -> Bool {
        let value = host.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        return value == "glados.cloud" || value.hasSuffix(".glados.cloud") || value == "railgun.info" || value.hasSuffix(".railgun.info")
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
