import Foundation
import os

enum TranscriptionError: Error { case noSpeech }

protocol AssistantBackend: Sendable {
    func connect() async throws
    func ask(_ text: String, offline: Bool) async throws -> BackendClient.Reply
    func dictation(app: String, chars: Int) async
    func transcribe(_ audio: Data) async throws -> String
    func speak(_ text: String) async throws
    func stopSpeech() async
    func pauseSpeech() async
    func resumeSpeech() async
    func cancel() async
    func reset() async throws
    func unloadLocal() async throws
    /// Local/remote choice for automated Reminders tasks (follows the ring's mode).
    func mode(offline: Bool) async
    func close() async
}

actor BackendClient: AssistantBackend {
    struct Settings: Decodable { let root: String; let node: String; let port: Int }
    struct Session: Decodable { let token: String; let max_record_seconds: Double }
    struct Reply: Decodable, Sendable {
        let text: String; let provider: String; let fallback_reason: String
        /// Barge-in: the text was added to the request still generating; that request's reply answers both.
        var steered = false
        init(text: String, provider: String, fallback_reason: String, steered: Bool = false) {
            self.text = text; self.provider = provider; self.fallback_reason = fallback_reason; self.steered = steered
        }
        private enum Keys: String, CodingKey { case text, provider, fallback_reason, steered }
        init(from decoder: Decoder) throws {
            let values = try decoder.container(keyedBy: Keys.self)
            steered = try values.decodeIfPresent(Bool.self, forKey: .steered) ?? false
            text = steered ? "" : try values.decode(String.self, forKey: .text)
            provider = try values.decodeIfPresent(String.self, forKey: .provider) ?? ""
            fallback_reason = try values.decodeIfPresent(String.self, forKey: .fallback_reason) ?? ""
        }
    }
    private var process: Process?
    /// The owned backend's pid, readable synchronously for applicationWillTerminate (0 = none).
    private nonisolated let childPID = OSAllocatedUnfairLock(initialState: pid_t(0))
    private var token = ""
    private var base = URL(string: "http://127.0.0.1:8765")!
    /// The in-flight (or finished) startup; requests made before it completes wait on it.
    private var connecting: Task<Void, Error>?

    init() {}
    /// Tests: talk to a given base URL with a preset token (no launcher/startup).
    init(base: URL, token: String) { self.base = base; self.token = token }

    func connect() async throws {
        let task = Task { try await self.start() }
        connecting = task
        try await task.value
    }
    private func start() async throws {
        guard let settingsURL = Bundle.main.url(forResource: "launcher", withExtension: "json") else {
            throw NSError(domain: "Assistant", code: 1, userInfo: [NSLocalizedDescriptionKey: "Launcher settings missing"])
        }
        let settings = try JSONDecoder().decode(Settings.self, from: Data(contentsOf: settingsURL))
        base = URL(string: "http://127.0.0.1:\(settings.port)")!
        if let session = try? await session() { token = session.token; return }
        // A previous backend may still be releasing the port while it shuts down: retry a few times, logging each try.
        var lastError: Error = NSError(domain: "Assistant", code: 3, userInfo: [NSLocalizedDescriptionKey: "Assistant engine did not start"])
        for attempt in 1...3 {
            do { try await launch(settings); return } catch {
                lastError = error
                guard (error as NSError).code == 2 else { LaunchGuard.log("backend start failed: \(error.localizedDescription)"); throw error }
            }
            LaunchGuard.log("backend start attempt \(attempt) failed: \(lastError.localizedDescription)")
            if let session = try? await session() { token = session.token; return }
            try await Task.sleep(for: .seconds(2))
        }
        throw lastError
    }
    private func launch(_ settings: Settings) async throws {
        let child = Process()
        child.executableURL = URL(fileURLWithPath: settings.node)
        child.arguments = ["src/main.ts", "serve"]
        child.currentDirectoryURL = URL(fileURLWithPath: settings.root)
        let logURL = URL(fileURLWithPath: settings.root).appendingPathComponent("data/native-launcher.log")
        try FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logURL.path) { FileManager.default.createFile(atPath: logURL.path, contents: nil) }
        let log = try FileHandle(forWritingTo: logURL)
        try log.seekToEnd()
        child.standardOutput = log; child.standardError = log; child.standardInput = FileHandle.nullDevice
        try child.run(); process = child; childPID.withLock { $0 = child.processIdentifier }; try log.close()
        let deadline = ContinuousClock.now.advanced(by: .seconds(15))
        while ContinuousClock.now < deadline {
            if let session = try? await session() { token = session.token; return }
            if !child.isRunning { throw NSError(domain: "Assistant", code: 2, userInfo: [NSLocalizedDescriptionKey: "Assistant engine exited. See data/native-launcher.log"] ) }
            // Bounded readiness retry for the child HTTP server; cancellation ends startup.
            try await Task.sleep(for: .milliseconds(150))
        }
        throw NSError(domain: "Assistant", code: 3, userInfo: [NSLocalizedDescriptionKey: "Assistant engine did not start"])
    }
    private func session() async throws -> Session {
        var request = URLRequest(url: base.appendingPathComponent("session"))
        request.timeoutInterval = 2
        let (data, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.cannotConnectToHost) }
        return try JSONDecoder().decode(Session.self, from: data)
    }
    /// Requests issued before the token exists (e.g. Admin reopened at launch) wait for startup, bounded.
    private func ready() async {
        let deadline = ContinuousClock.now.advanced(by: .seconds(20))
        while token.isEmpty, ContinuousClock.now < deadline {
            if let connecting { _ = try? await connecting.value; break }
            // connect() not called yet; bounded wait for it to begin.
            try? await Task.sleep(for: .milliseconds(100))
        }
        if token.isEmpty, let session = try? await session() { token = session.token }
    }
    private func post(_ route: String, body: Data, type: String = "application/json") async throws -> Data {
        if token.isEmpty { await ready() }
        let (data, status) = try await send(route, body: body, type: type)
        if status == 403, Self.errorText(data) == "Invalid session", let session = try? await session() {
            // Backend restarted with a new token: refresh once and retry once.
            token = session.token
            let (retried, retriedStatus) = try await send(route, body: body, type: type)
            return try check(route, retried, retriedStatus)
        }
        return try check(route, data, status)
    }
    private static func errorText(_ data: Data) -> String? {
        ((try? JSONSerialization.jsonObject(with: data)) as? [String: Any])?["error"] as? String
    }
    private func send(_ route: String, body: Data, type: String) async throws -> (Data, Int) {
        var request = URLRequest(url: base.appendingPathComponent(route))
        request.httpMethod = "POST"; request.httpBody = body; request.timeoutInterval = 360
        request.setValue(token, forHTTPHeaderField: "X-Assistant-Token")
        request.setValue(type, forHTTPHeaderField: "Content-Type")
        let (data, response) = try await URLSession.shared.data(for: request)
        return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
    }
    private func check(_ route: String, _ data: Data, _ status: Int) throws -> Data {
        guard status == 200 else {
            let value = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            if route == "transcribe", value?["code"] as? String == "NO_SPEECH" { throw TranscriptionError.noSpeech }
            throw NSError(domain: "Assistant", code: 4, userInfo: [NSLocalizedDescriptionKey: value?["error"] as? String ?? "Assistant request failed"])
        }
        return data
    }
    func ask(_ text: String, offline: Bool) async throws -> Reply {
        let data = try await post("chat", body: JSONSerialization.data(withJSONObject: ["text": text, "offline": offline]))
        return try JSONDecoder().decode(Reply.self, from: data)
    }
    func transcribe(_ audio: Data) async throws -> String {
        let data = try await post("transcribe", body: audio, type: "audio/wav")
        return ((try JSONSerialization.jsonObject(with: data)) as? [String: String])?["text"] ?? ""
    }
    func speak(_ text: String) async throws { _ = try await post("speak", body: JSONSerialization.data(withJSONObject: ["text": text])) }
    /// Activity log only (app id + length); the dictated text never leaves the host.
    func dictation(app: String, chars: Int) async {
        _ = try? await post("dictation", body: JSONSerialization.data(withJSONObject: ["app": app, "text_chars": chars] as [String: Any]))
    }
    func stopSpeech() async { _ = try? await post("stop-speech", body: Data("{}".utf8)) }
    func pauseSpeech() async { _ = try? await post("pause-speech", body: Data("{}".utf8)) }
    func resumeSpeech() async { _ = try? await post("resume-speech", body: Data("{}".utf8)) }
    func cancel() async { _ = try? await post("cancel", body: Data("{}".utf8)) }
    func reset() async throws { _ = try await post("reset", body: Data("{}".utf8)) }
    func unloadLocal() async throws { _ = try await post("unload-local", body: Data("{}".utf8)) }
    func mode(offline: Bool) async { _ = try? await post("mode", body: JSONSerialization.data(withJSONObject: ["offline": offline])) }
    /// Admin window calls (status, skill/extension files); not part of the ring's backend protocol.
    func admin(_ route: String, _ body: [String: String] = [:]) async throws -> Data {
        try await post(route, body: JSONSerialization.data(withJSONObject: body))
    }
    func admin(_ route: String, data body: Data) async throws -> Data { try await post(route, body: body) }
    func admin(_ route: String, json body: [String: Any]) async throws -> Data {
        try await post(route, body: JSONSerialization.data(withJSONObject: body))
    }
    func close() async {
        if let process, process.isRunning {
            process.terminate()
            // Let the backend finish cancelling work and unloading its owned model (bounded: a hung backend
            // must not keep this instance alive, which would block the next launch).
            let deadline = ContinuousClock.now.advanced(by: .seconds(20))
            while process.isRunning, ContinuousClock.now < deadline { try? await Task.sleep(for: .milliseconds(50)) }
            if process.isRunning { LaunchGuard.log("backend pid \(process.processIdentifier) still stopping after 20s; quitting anyway") }
        }
        process = nil; childPID.withLock { $0 = 0 }
    }
    /// Synchronous SIGTERM for a forced termination; the backend shuts itself down gracefully on SIGTERM.
    nonisolated func terminateNow() {
        let pid = childPID.withLock { $0 }
        if pid > 0, kill(pid, SIGTERM) == 0 { LaunchGuard.log("forced termination: sent SIGTERM to backend pid \(pid)") }
    }
}
