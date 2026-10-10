import Foundation
import Observation

@MainActor @Observable
final class RingModel {
    enum Phase: String { case starting, idle, preparing, listening, thinking, speaking, noSpeech, error }
    var phase: Phase = .starting
    var status = "Starting…"
    var input = ""
    var response = ""
    var transcript = ""
    var provider = ""
    var changingMode = false
    var offline = false
    var readAloud = true
    var shortcutLabel = Shortcut.load().label
    var shortcutEnabled = false
    var level = 0.0
    var showInput: () -> Void = {}
    var toggleInput: () -> Void = {}
    var contextMenu: () -> Void = {}
    /// Focused editable text field of another app at recording start (opaque; nil = none).
    var captureFocus: () -> AnyObject? = { nil }
    /// Pastes the transcript into the captured target if it still has focus; returns the app id when pasted.
    /// Pasted = dictation (the assistant is not asked); not pasted = the transcript goes to the assistant.
    var pasteInto: (AnyObject, String) -> String? = { _, _ in nil }
    private var focusTarget: AnyObject?
    /// A paste target was captured: the ring shows dictation instead of the assistant states.
    var dictating: Bool { focusTarget != nil }
    let backend: any AssistantBackend
    let audio: any AudioCapture
    private var recordingTask: Task<Void, Never>?
    private var transcribeTask: Task<Void, Never>?
    private var requestTask: Task<Void, Never>?
    /// Assistant work (ask + read aloud) runs independently of recording; voice requests queue behind it.
    private var jobTask: Task<Void, Never>?
    private var pending: [String] = []
    private var jobPhase: Phase?
    private var jobStatus = ""
    private var jobError: Error?
    /// Recording/transcription owns the ring until it ends; the assistant state is shown again afterwards.
    private var capturing = false
    private var speechPaused = false
    private var speechGate: Task<Void, Never>?
    private var holdTask: Task<Void, Never>?
    private var meterTask: Task<Void, Never>?
    private var ceilingTask: Task<Void, Never>?
    private var feedbackTask: Task<Void, Never>?
    private var chordDown = false
    private var ready = false

    init(backend: any AssistantBackend, audio: any AudioCapture) { self.backend = backend; self.audio = audio }
    func connect() {
        Task { do { try await backend.connect(); ready = true; phase = .idle; status = "\(shortcutLabel) • click to type" }
            catch { fail(error) } }
    }
    func modifiers(_ both: Bool) {
        guard both != chordDown else { return }; chordDown = both
        holdTask?.cancel()
        if both {
            holdTask = Task {
                // Intentional hold threshold avoids activation on accidental modifier taps.
                try? await Task.sleep(for: .milliseconds(350))
                guard !Task.isCancelled, chordDown else { return }; beginListening()
            }
        } else if phase == .listening { finishListening() }
        else if phase == .preparing { discardRecording(); restore() }
    }
    func beginListening() {
        guard ready, !capturing, !changingMode, phase != .starting else { return }
        feedbackTask?.cancel()
        focusTarget = captureFocus() // The user's text cursor at the moment they start speaking.
        capturing = true; phase = .preparing; status = "Preparing microphone…"
        holdSpeech(true)
        recordingTask = Task {
            do {
                try Task.checkCancellation()
                try await audio.start()
                guard !Task.isCancelled, phase == .preparing else { audio.cancel(); return }
                phase = .listening; status = focusTarget == nil ? "Listening… release the keys" : "Dikte / Dictation… release the keys"
                meterTask = Task {
                    while !Task.isCancelled {
                        level = audio.level
                        try? await Task.sleep(for: .milliseconds(60)) // Intentional animation sample cadence.
                    }
                }
                ceilingTask = Task {
                    try? await Task.sleep(for: .seconds(300)) // Intended recording safety ceiling.
                    if !Task.isCancelled, phase == .listening { finishListening() }
                }
            } catch { if !Task.isCancelled { discardRecording(); fail(error) } }
        }
    }
    /// Pauses (or continues) reply audio while the microphone is open; the request itself keeps running.
    private func holdSpeech(_ pause: Bool) {
        guard pause != speechPaused else { return }; speechPaused = pause
        let previous = speechGate
        speechGate = Task { await previous?.value; if pause { await backend.pauseSpeech() } else { await backend.resumeSpeech() } }
    }
    private func discardRecording() {
        recordingTask?.cancel(); transcribeTask?.cancel(); meterTask?.cancel(); ceilingTask?.cancel()
        audio.cancel(); level = 0; focusTarget = nil; capturing = false; holdSpeech(false)
    }
    /// Recording finished: show the running assistant job (or idle) again.
    private func restore() {
        capturing = false
        if let error = jobError { jobError = nil; fail(error) }
        else if let jobPhase { phase = jobPhase; status = jobStatus }
        else { phase = .idle; status = "\(shortcutLabel) • click to type" }
    }
    func finishListening() {
        guard phase == .listening else { return }
        recordingTask?.cancel(); meterTask?.cancel(); ceilingTask?.cancel(); level = 0
        do {
            let wav = try audio.finish()
            holdSpeech(false)
            guard !wav.isEmpty else { focusTarget = nil; restore(); return }
            phase = .thinking; status = focusTarget == nil ? "Transcribing on this Mac…" : "Yazılıyor / Typing…"
            transcribeTask = Task {
                do { let text = try await backend.transcribe(wav); guard !Task.isCancelled else { return }
                    guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { focusTarget = nil; noSpeech(); return }
                    // Dictation: typed where the user's cursor was (field still focused); otherwise ask the assistant.
                    let pasted = focusTarget.flatMap { pasteInto($0, text) }; focusTarget = nil
                    if let pasted { await dictated(into: pasted, chars: text.utf16.count); return }
                    restore(); enqueue(text) }
                catch TranscriptionError.noSpeech { if !Task.isCancelled { focusTarget = nil; noSpeech() } }
                catch { if !Task.isCancelled { focusTarget = nil; capturing = false; fail(error) } }
            }
        } catch { discardRecording(); fail(error) }
    }
    private func dictated(into app: String, chars: Int) async {
        restore(); if jobPhase == nil, phase == .idle { status = "Yapıştırıldı / Pasted • \(shortcutLabel)" }
        await backend.dictation(app: app, chars: chars)
    }
    private func noSpeech() {
        feedbackTask?.cancel(); capturing = false
        phase = .noSpeech; status = "Ses algılanmadı / No speech detected"
        feedbackTask = Task {
            try? await Task.sleep(for: .milliseconds(450))
            guard !Task.isCancelled, phase == .noSpeech, !capturing else { return }
            restore()
        }
    }
    func send() {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard ready, !text.isEmpty, phase != .starting, phase != .thinking, !changingMode else { return }
        feedbackTask?.cancel(); holdTask?.cancel(); discardRecording()
        input = ""; restore(); enqueue(text)
    }
    /// Runs requests one after another; a new one never interrupts the running one.
    private func enqueue(_ text: String) {
        pending.append(text)
        guard jobTask == nil else { return }
        jobTask = Task {
            while !pending.isEmpty, !Task.isCancelled {
                let next = pending.removeFirst()
                do { try await reply(to: next) }
                catch { if Task.isCancelled { return }; jobPhase = nil; if capturing { jobError = error } else { fail(error) } }
            }
            if !Task.isCancelled { jobTask = nil }
        }
    }
    private func showJob(_ value: Phase?, _ text: String) {
        jobPhase = value; jobStatus = text
        guard !capturing, phase != .noSpeech else { return }
        phase = value ?? .idle; status = text
    }
    private func reply(to text: String) async throws {
        transcript = text
        showJob(.thinking, offline ? "Thinking locally…" : "Thinking with Pi…")
        let result = try await backend.ask(text, offline: offline)
        guard !Task.isCancelled else { return }
        response = result.text; provider = result.provider + (result.fallback_reason.isEmpty ? "" : " • " + result.fallback_reason)
        if readAloud {
            showJob(.speaking, "Speaking…")
            try await backend.speak(result.text)
        }
        guard !Task.isCancelled else { return }
        showJob(nil, "\(shortcutLabel) • click to type")
    }
    func setOffline(_ value: Bool) {
        guard ready, !changingMode else { return }
        offline = value
        Task { await backend.mode(offline: value) }
        guard !value else { return } // Local mode loads lazily on the next request.
        cancel(notifyBackend: false)
        changingMode = true; phase = .thinking; status = "Yerel model boşaltılıyor / Unloading local model…"
        requestTask = Task {
            defer { changingMode = false }
            do {
                await backend.stopSpeech()
                try await backend.unloadLocal()
                guard !Task.isCancelled else { return }
                phase = .idle; status = "Hold \(shortcutLabel) to speak"
            } catch { if !Task.isCancelled { fail(error) } }
        }
    }
    func cancel(notifyBackend: Bool = true) {
        feedbackTask?.cancel(); holdTask?.cancel(); recordingTask?.cancel(); transcribeTask?.cancel(); requestTask?.cancel(); meterTask?.cancel(); ceilingTask?.cancel()
        jobTask?.cancel(); jobTask = nil; pending = []; jobPhase = nil; jobError = nil; capturing = false; speechPaused = false
        audio.cancel(); level = 0; focusTarget = nil; phase = .idle; status = "Cancelled"
        let gate = speechGate
        if notifyBackend { Task { await gate?.value; await backend.cancel(); await backend.stopSpeech() } }
    }
    func newConversation() {
        cancel(); response = ""; transcript = ""; provider = ""
        Task { do { try await backend.reset(); status = "New conversation" } catch { fail(error) } }
    }
    func fail(_ error: Error) { focusTarget = nil; phase = .error; status = error.localizedDescription; showInput() }
    func close() { cancel(); Task { await backend.close() } }
}
