import Foundation

/// Stub backend: GET /session returns the current token; POSTs with any other token get 403 "Invalid session".
final class StubProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var current = "fresh"
    nonisolated(unsafe) static var posts = 0
    nonisolated(unsafe) static var rejectAll = false
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "stub.test" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let url = request.url!
        var code = 200; var body: [String: Any] = ["ok": true]
        if url.path == "/session" { body = ["token": Self.current, "max_record_seconds": 30] }
        else {
            Self.posts += 1
            if request.value(forHTTPHeaderField: "X-Assistant-Token") != Self.current || Self.rejectAll { code = 403; body = ["error": "Invalid session"] }
        }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: url, statusCode: code, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: try! JSONSerialization.data(withJSONObject: body))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

@main
struct BackendTests {
    static func expect(_ ok: Bool, _ label: String) { if !ok { print("FAIL: \(label)"); exit(1) } }
    static func main() async {
        URLProtocol.registerClass(StubProtocol.self)
        let base = URL(string: "http://stub.test")!
        // Stale token (backend restarted): refreshed once, request retried once.
        let client = BackendClient(base: base, token: "stale")
        expect((try? await client.admin("status")) != nil, "stale token refreshes and retries")
        expect(StubProtocol.posts == 2, "exactly one retry")
        // Backend keeps rejecting: one retry only, then the error surfaces (no loop).
        StubProtocol.posts = 0; StubProtocol.rejectAll = true
        let stuck = BackendClient(base: base, token: "bad")
        do { _ = try await stuck.admin("status"); expect(false, "rejection surfaces") }
        catch { expect(error.localizedDescription == "Invalid session", "error text kept") }
        expect(StubProtocol.posts == 2, "no loop")
        // Barge-in reply: {"steered":true} carries no text; a normal reply is not steered.
        let steered = try? JSONDecoder().decode(BackendClient.Reply.self, from: Data(#"{"steered":true}"#.utf8))
        expect(steered?.steered == true && steered?.text == "", "steered reply decodes")
        let normal = try? JSONDecoder().decode(BackendClient.Reply.self, from: Data(#"{"text":"hi","provider":"p","fallback_reason":""}"#.utf8))
        expect(normal?.steered == false && normal?.text == "hi", "normal reply decodes")
        print("backend tests passed")
    }
}
