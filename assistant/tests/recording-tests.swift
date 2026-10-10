import Foundation

actor FakeBackend: AssistantBackend {
    var messages: [String] = []
    var unloads = 0
    func unloadLocal() async throws { unloads += 1 }
    var silent = false
    func setSilent(_ value: Bool) { silent = value }
    func connect() async throws {}
    var dictations: [String] = []
    func dictation(app: String, chars: Int) async { dictations.append("\(app):\(chars)") }
    func ask(_ text: String, offline: Bool) async throws -> BackendClient.Reply {
        messages.append(text)
        return BackendClient.Reply(text: "reply: " + text, provider: "test", fallback_reason: "")
    }
    func transcribe(_ audio: Data) async throws -> String {
        if silent { throw TranscriptionError.noSpeech }; return "voice"
    }
    func speak(_ text: String) async throws {}
    func stopSpeech() async {}
    func cancel() async {}
    func reset() async throws {}
    func close() async {}
}

@MainActor
final class FakeCapture: AudioCapture {
    var permission: CheckedContinuation<Void, Never>?
    var instant = false
    var recording = false
    var level: Double { 0 }
    func start() async throws {
        if !instant { await withCheckedContinuation { permission = $0 } }
        try Task.checkCancellation()
        recording = true
    }
    func allow() { permission?.resume(); permission = nil }
    func finish() throws -> Data { let data = recording ? Data([1]) : Data(); recording = false; return data }
    func cancel() { recording = false }
}

@main
struct RecordingChecks {
    @MainActor static func wait(_ condition: () -> Bool) async throws {
        for _ in 0..<150 {
            if condition() { return }
            try await Task.sleep(for: .milliseconds(5))
        }
        preconditionFailure("State did not settle")
    }
    @MainActor static func main() async throws {
        let backend = FakeBackend(), audio = FakeCapture()
        let model = RingModel(backend: backend, audio: audio)
        model.readAloud = false; model.connect()
        try await wait { model.phase == .idle }
        model.modifiers(true)
        try await wait { audio.permission != nil }
        precondition(model.phase == .preparing)
        model.modifiers(false)
        precondition(model.phase == .idle)
        audio.allow()
        try await Task.sleep(for: .milliseconds(15))
        precondition(!audio.recording && model.phase == .idle)

        // A typed request supersedes a pending microphone permission request.
        model.beginListening()
        try await wait { audio.permission != nil }
        model.input = "typed during permission"; model.send(); audio.allow()
        try await wait { model.response == "reply: typed during permission" && model.phase == .idle }
        precondition(!audio.recording)

        // A typed request also stops an already active recording without submitting its audio.
        audio.instant = true; model.beginListening()
        try await wait { model.phase == .listening }
        model.input = "typed during recording"; model.send()
        try await wait { model.response == "reply: typed during recording" && model.phase == .idle }
        precondition(!audio.recording)
        let messages = await backend.messages
        precondition(messages == ["typed during permission", "typed during recording"])
        var popupCount = 0
        model.showInput = { popupCount += 1 }
        let previousReply = model.response
        await backend.setSilent(true)
        model.modifiers(true)
        try await wait { model.phase == .listening }
        model.modifiers(false)
        try await wait { model.phase == .idle }
        precondition(popupCount == 0 && model.response == previousReply)
        // A new voice request must work without any intervening typed message.
        await backend.setSilent(false)
        model.modifiers(true)
        try await wait { model.phase == .listening }
        model.modifiers(false)
        try await wait { model.response == "reply: voice" && model.phase == .idle }
        precondition(popupCount == 0)
        let replyBeforeUnload = model.response
        model.setOffline(true); precondition(model.offline)
        model.setOffline(false)
        try await wait { !model.changingMode && model.phase == .idle }
        let unloads = await backend.unloads
        precondition(unloads == 1 && !model.offline && model.response == replyBeforeUnload)
        // Pasted into another app's focused field: dictation only, the assistant is not asked.
        final class Field {}
        var pasted: [String] = []
        model.captureFocus = { Field() }
        model.pasteInto = { _, text in pasted.append(text); return "com.example.editor" }
        let before = await backend.messages.count
        let previous = model.response
        model.modifiers(true)
        try await wait { model.phase == .listening }
        precondition(model.dictating)
        model.modifiers(false)
        try await wait { model.phase == .idle && pasted == ["voice"] }
        try await wait { !model.dictating }
        var dictations: [String] = []
        for _ in 0..<50 { dictations = await backend.dictations; if !dictations.isEmpty { break }; try await Task.sleep(for: .milliseconds(5)) }
        var sent = await backend.messages.count
        precondition(sent == before && dictations == ["com.example.editor:5"] && model.response == previous)
        // Paste failed (focus moved, no permission, ...): the transcript goes to the assistant as usual.
        model.pasteInto = { _, _ in nil }
        model.modifiers(true)
        try await wait { model.phase == .listening }
        model.modifiers(false)
        for _ in 0..<50 { if await backend.messages.count == before + 1 { break }; try await Task.sleep(for: .milliseconds(5)) }
        try await wait { model.phase == .idle }
        sent = await backend.messages.count
        dictations = await backend.dictations
        precondition(sent == before + 1 && dictations.count == 1)
        // No focused field: assistant flow, paste never attempted.
        model.captureFocus = { nil }
        model.pasteInto = { _, text in pasted.append(text); return "x" }
        model.modifiers(true)
        try await wait { model.phase == .listening }
        precondition(!model.dictating)
        model.modifiers(false)
        for _ in 0..<50 { if await backend.messages.count == before + 2 { break }; try await Task.sleep(for: .milliseconds(5)) }
        try await wait { model.phase == .idle }
        sent = await backend.messages.count
        precondition(sent == before + 2 && pasted == ["voice"])
        // Silent recording with a field focused: nothing pasted, nothing sent.
        model.captureFocus = { Field() }
        await backend.setSilent(true)
        model.modifiers(true)
        try await wait { model.phase == .listening }
        model.modifiers(false)
        try await wait { model.phase == .idle && !model.dictating }
        sent = await backend.messages.count
        precondition(pasted == ["voice"] && sent == before + 2)
        model.cancel()
        print("Recording checks passed: permission-release race, typed override during permission and recording, dictation vs assistant routing.")
    }
}
