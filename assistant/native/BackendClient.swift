import Foundation

enum TranscriptionError: Error { case noSpeech }

protocol AssistantBackend: Sendable {
    func connect() async throws
    func ask(_ text: String, offline: Bool) async throws -> BackendClient.Reply
    func dictation(app: String, chars: Int) async
    func transcribe(_ audio: Data) async throws -> String
    func speak(_ text: String) async throws
    func stopSpeech() async
    func cancel() async
    func reset() async throws
    func unloadLocal() async throws
    func close() async
}

actor BackendClient: AssistantBackend {
    struct Settings: Decodable { let root: String; let node: String; let port: Int }
    struct Session: Decodable { let token: String; let max_record_seconds: Double }
    struct Reply: Decodable, Sendable {
        let text: String; let provider: String; let fallback_reason: String
    }
    private var process: Process?
    private var token = ""
    private var base = URL(string: "http://127.0.0.1:8765")!

    func connect() async throws {
        guard let settingsURL = Bundle.main.url(forResource: "launcher", withExtension: "json") else {
            throw NSError(domain: "Assistant", code: 1, userInfo: [NSLocalizedDescriptionKey: "Launcher settings missing"])
        }
        let settings = try JSONDecoder().decode(Settings.self, from: Data(contentsOf: settingsURL))
        base = URL(string: "http://127.0.0.1:\(settings.port)")!
        if let session = try? await session() { token = session.token; return }
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
        try child.run(); process = child; try log.close()
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
    private func post(_ route: String, body: Data, type: String = "application/json") async throws -> Data {
        var request = URLRequest(url: base.appendingPathComponent(route))
        request.httpMethod = "POST"; request.httpBody = body; request.timeoutInterval = 360
        request.setValue(token, forHTTPHeaderField: "X-Assistant-Token")
        request.setValue(type, forHTTPHeaderField: "Content-Type")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
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
    func cancel() async { _ = try? await post("cancel", body: Data("{}".utf8)) }
    func reset() async throws { _ = try await post("reset", body: Data("{}".utf8)) }
    func unloadLocal() async throws { _ = try await post("unload-local", body: Data("{}".utf8)) }
    /// Admin window calls (status, skill/extension files); not part of the ring's backend protocol.
    func admin(_ route: String, _ body: [String: String] = [:]) async throws -> Data {
        try await post(route, body: JSONSerialization.data(withJSONObject: body))
    }
    func admin(_ route: String, json body: [String: Any]) async throws -> Data {
        try await post(route, body: JSONSerialization.data(withJSONObject: body))
    }
    func close() async {
        if let process, process.isRunning {
            process.terminate()
            // Let the backend finish cancelling work and unloading its owned model.
            while process.isRunning { try? await Task.sleep(for: .milliseconds(50)) }
        }
        process = nil
    }
}
