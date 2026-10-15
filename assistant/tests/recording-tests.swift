import Foundation

actor FakeBackend: AssistantBackend {
    var messages: [String] = []
    var unloads = 0
    func unloadLocal() async throws { unloads += 1 }
    func mode(offline: Bool) async {}
    var silent = false
    func setSilent(_ value: Bool) { silent = value }
    func connect() async throws {}
    var dictations: [String] = []
    func dictation(app: String, chars: Int) async { dictations.append("\(app):\(chars)") }
    var holdNext = false
    var held: CheckedContinuation<Void, Never>?
    func setHoldNext() { holdNext = true }
    func release() { held?.resume(); held = nil }
    var isHeld: Bool { held != nil }
    var speech: [String] = []
    func pauseSpeech() async { speech.append("pause") }
    func resumeSpeech() async { speech.append("resume") }
    var cancels = 0
    /// Like the server: a request while another still generates is steered into it.
    var steered: [String] = []
    func ask(_ text: String, offline: Bool, readAloud: Bool) async throws -> BackendClient.Reply {
        messages.append(text)
        if held != nil { steered.append(text); return BackendClient.Reply(text: "", provider: "", fallback_reason: "", steered: true) }
        if holdNext { holdNext = false; await withCheckedContinuation { held = $0 } }
        return BackendClient.Reply(text: "reply: " + ([text] + steered).joined(separator: " + "), provider: "test", fallback_reason: "")
    }
    func clearSteered() { steered = [] }
    func transcribe(_ audio: Data) async throws -> String {
        if silent { throw TranscriptionError.noSpeech }; return "voice"
    }
    var spoken: [String] = []
    var holdSpeaking = false
    var speaking: CheckedContinuation<Void, Never>?
    func setHoldSpeaking(_ value: Bool) { holdSpeaking = value }
    var isSpeaking: Bool { speaking != nil }
    func speak(_ text: String) async throws {
        spoken.append(text)
        if holdSpeaking { await withCheckedContinuation { speaking = $0 } }
    }
    var stops = 0
    func stopSpeech() async { stops += 1; speaking?.resume(); speaking = nil }
    func cancel() async { cancels += 1 }
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
    @MainActor static func wait(_ tries: Int = 150, _ condition: () -> Bool) async throws {
        for _ in 0..<tries {
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
        // Dictation shortcut: pasted into the focused field only; the assistant is never asked.
        final class Field {}
        var pasted: [String] = [], copied: [String] = [], captures = 0
        model.captureFocus = { captures += 1; return Field() }
        model.pasteInto = { _, text in pasted.append(text); return "com.example.editor" }
        model.copyText = { copied.append($0) }
        let before = await backend.messages.count
        let previous = model.response
        model.dictationKeys(true)
        try await wait { model.phase == .listening }
        precondition(model.dictating)
        model.dictationKeys(false)
        try await wait { model.phase == .idle && pasted == ["voice"] && !model.dictating }
        var dictations: [String] = []
        for _ in 0..<50 { dictations = await backend.dictations; if !dictations.isEmpty { break }; try await Task.sleep(for: .milliseconds(5)) }
        var sent = await backend.messages.count
        precondition(sent == before && dictations == ["com.example.editor:5"] && model.response == previous)
        // Paste failed (focus moved, no permission): left on the clipboard with a short ring notice; still never asked.
        model.pasteInto = { _, _ in nil }
        model.dictationKeys(true)
        try await wait { model.phase == .listening }
        model.dictationKeys(false)
        try await wait { model.phase == .copied && copied == ["voice"] }
        try await wait(400) { model.phase == .idle }
        // No focused field: copied, paste never attempted.
        model.captureFocus = { nil }
        model.pasteInto = { _, text in pasted.append(text); return "x" }
        model.dictationKeys(true)
        try await wait { model.phase == .listening }
        model.dictationKeys(false)
        try await wait { copied.count == 2 && model.phase == .copied }
        try await wait(400) { model.phase == .idle }
        sent = await backend.messages.count
        precondition(sent == before && pasted == ["voice"])
        // Assistant shortcut: always the assistant, never a paste (the focused field is not even looked at).
        captures = 0
        model.captureFocus = { captures += 1; return Field() }
        model.modifiers(true)
        try await wait { model.phase == .listening }
        precondition(!model.dictating)
        model.modifiers(false)
        for _ in 0..<50 { if await backend.messages.count == before + 1 { break }; try await Task.sleep(for: .milliseconds(5)) }
        try await wait { model.phase == .idle }
        precondition(captures == 0 && pasted == ["voice"] && copied.count == 2)
        // Silent dictation: nothing pasted, copied or sent.
        await backend.setSilent(true)
        model.dictationKeys(true)
        try await wait { model.phase == .listening }
        model.dictationKeys(false)
        try await wait { model.phase == .idle && !model.dictating }
        sent = await backend.messages.count
        precondition(pasted == ["voice"] && copied.count == 2 && sent == before + 1)
        await backend.setSilent(false)

        // Dictation while a request still generates: the request keeps running; its reply is shown, not read aloud.
        model.readAloud = true
        model.captureFocus = { Field() }
        model.pasteInto = { _, text in pasted.append(text); return "com.example.editor" }
        await backend.setHoldNext()
        model.input = "long job"; model.send()
        for _ in 0..<100 { if await backend.isHeld { break }; try await Task.sleep(for: .milliseconds(5)) }
        precondition(model.phase == .thinking)
        model.dictationKeys(true)
        try await wait { model.phase == .listening }
        model.dictationKeys(false)
        try await wait { pasted.count == 2 && model.phase == .thinking }
        var held = await backend.isHeld
        precondition(held && model.transcript == "long job")
        await backend.release()
        try await wait { model.response == "reply: long job" && model.phase == .idle }
        var spoken = await backend.spoken
        precondition(spoken.isEmpty)

        // Assistant shortcut while a request still generates: the new text is steered into it; one reply, spoken.
        await backend.setHoldNext()
        let jobStart = await backend.messages.count
        model.input = "weather today"; model.send()
        for _ in 0..<100 { if await backend.isHeld { break }; try await Task.sleep(for: .milliseconds(5)) }
        model.modifiers(true)
        try await wait { model.phase == .listening }
        model.modifiers(false)
        for _ in 0..<100 { if await backend.messages.count == jobStart + 2 { break }; try await Task.sleep(for: .milliseconds(5)) }
        try await wait { model.phase == .thinking }
        held = await backend.isHeld
        precondition(held)
        await backend.release()
        try await wait { model.response == "reply: weather today + voice" && model.phase == .idle }
        spoken = await backend.spoken
        precondition(spoken == ["reply: weather today + voice"])
        await backend.clearSteered()

        // Reply being read aloud + assistant shortcut: the voice stops at the press; the new request runs at once.
        await backend.setHoldSpeaking(true)
        model.input = "talk"; model.send()
        for _ in 0..<100 { if await backend.isSpeaking { break }; try await Task.sleep(for: .milliseconds(5)) }
        precondition(model.phase == .speaking)
        let stopsBefore = await backend.stops
        await backend.setHoldSpeaking(false)
        model.modifiers(true)
        for _ in 0..<100 { if await !backend.isSpeaking { break }; try await Task.sleep(for: .milliseconds(5)) }
        let stops = await backend.stops
        precondition(stops > stopsBefore)
        try await wait { model.phase == .listening }
        model.modifiers(false)
        try await wait { model.response == "reply: voice" && model.phase == .idle }
        spoken = await backend.spoken
        precondition(spoken.suffix(2) == ["reply: talk", "reply: voice"])
        let cancels = await backend.cancels
        precondition(cancels == 0)
        model.cancel()
        // Quit/Restart: the ring turns to the quitting state at once and no later update overrides it.
        model.input = "late"; model.send()
        model.beginQuit()
        precondition(model.phase == .quitting && model.quitting)
        model.phase = .idle; model.send(); model.beginListening(); model.fail(CancellationError())
        try await Task.sleep(for: .milliseconds(50))
        precondition(model.phase == .quitting)
        print("Recording checks passed: permission-release race, typed override, dictation shortcut never asks (paste/clipboard), assistant shortcut never pastes, dictation mutes a running reply, steer while generating, barge-in stops speech, quitting state sticks.")
    }
}
