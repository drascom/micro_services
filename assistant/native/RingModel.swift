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
    let backend: any AssistantBackend
    let audio: any AudioCapture
    private var recordingTask: Task<Void, Never>?
    private var requestTask: Task<Void, Never>?
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
        else if phase == .preparing {
            discardRecording(); phase = .idle; status = "Hold \(shortcutLabel) to speak"
        }
    }
    func beginListening() {
        guard ready, phase == .idle || phase == .error || phase == .speaking || phase == .noSpeech else { return }
        feedbackTask?.cancel(); requestTask?.cancel()
        phase = .preparing; status = "Preparing microphone…"
        recordingTask = Task {
            await backend.stopSpeech()
            do {
                try Task.checkCancellation()
                try await audio.start()
                guard !Task.isCancelled, phase == .preparing else { audio.cancel(); return }
                phase = .listening; status = "Listening… release the keys"
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
            } catch { if !Task.isCancelled { fail(error) } }
        }
    }
    private func discardRecording() {
        recordingTask?.cancel(); meterTask?.cancel(); ceilingTask?.cancel()
        audio.cancel(); level = 0
    }
    func finishListening() {
        guard phase == .listening else { return }
        recordingTask?.cancel(); meterTask?.cancel(); ceilingTask?.cancel(); level = 0
        do {
            let wav = try audio.finish()
            guard !wav.isEmpty else { phase = .idle; status = "Hold \(shortcutLabel) to speak"; return }
            phase = .thinking; status = "Transcribing on this Mac…"
            requestTask = Task {
                do { let text = try await backend.transcribe(wav); guard !Task.isCancelled else { return }
                    guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { noSpeech(); return }
                    transcript = text; try await reply(to: text) }
                catch TranscriptionError.noSpeech { if !Task.isCancelled { noSpeech() } }
                catch { if !Task.isCancelled { fail(error) } }
            }
        } catch { fail(error) }
    }
    private func noSpeech() {
        feedbackTask?.cancel()
        phase = .noSpeech; status = "Ses algılanmadı / No speech detected"
        feedbackTask = Task {
            try? await Task.sleep(for: .milliseconds(450))
            guard !Task.isCancelled, phase == .noSpeech else { return }
            phase = .idle; status = "Hold \(shortcutLabel) to speak"
        }
    }
    func send() {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard ready, !text.isEmpty, phase != .starting, phase != .thinking else { return }
        feedbackTask?.cancel(); holdTask?.cancel(); discardRecording()
        input = ""; transcript = text; phase = .thinking; status = "Thinking…"
        requestTask?.cancel()
        requestTask = Task { do { await backend.stopSpeech(); try await reply(to: text) }
            catch { if !Task.isCancelled { fail(error) } } }
    }
    private func reply(to text: String) async throws {
        phase = .thinking; status = offline ? "Thinking locally…" : "Thinking with Pi…"
        let result = try await backend.ask(text, offline: offline)
        guard !Task.isCancelled else { return }
        response = result.text; provider = result.provider + (result.fallback_reason.isEmpty ? "" : " • " + result.fallback_reason)
        if readAloud {
            phase = .speaking; status = "Speaking…"
            try await backend.speak(result.text)
        }
        guard !Task.isCancelled else { return }
        phase = .idle; status = "\(shortcutLabel) • click to type"
    }
    func setOffline(_ value: Bool) {
        guard ready, !changingMode else { return }
        offline = value
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
        feedbackTask?.cancel(); holdTask?.cancel(); recordingTask?.cancel(); requestTask?.cancel(); meterTask?.cancel(); ceilingTask?.cancel()
        audio.cancel(); level = 0; phase = .idle; status = "Cancelled"
        if notifyBackend { Task { await backend.cancel(); await backend.stopSpeech() } }
    }
    func newConversation() {
        cancel(); response = ""; transcript = ""; provider = ""
        Task { do { try await backend.reset(); status = "New conversation" } catch { fail(error) } }
    }
    func fail(_ error: Error) { phase = .error; status = error.localizedDescription; showInput() }
    func close() { cancel(); Task { await backend.close() } }
}
