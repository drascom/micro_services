import Foundation
import Observation

@MainActor @Observable
final class RingModel {
    enum Phase: String { case starting, idle, preparing, listening, thinking, speaking, noSpeech, copied, error, quitting }
    /// Once quitting, the ring stays `.quitting` until the process exits; later state updates cannot override it.
    var phase: Phase { get { quitting ? .quitting : current } set { current = newValue } }
    private var current: Phase = .starting
    private(set) var quitting = false
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
    /// Shows the reply panel without taking focus from the user's app (proactive messages).
    var revealInput: () -> Void = {}
    var contextMenu: () -> Void = {}
    /// Focused editable text field of another app at recording start (opaque; nil = none).
    var dictationLabel = Shortcut.loadDictation().label
    /// Focused editable text field of another app at dictation start (opaque; nil = none).
    var captureFocus: () -> AnyObject? = { nil }
    /// Pastes the transcript into the captured target if it still has focus; returns the app id when pasted.
    var pasteInto: (AnyObject, String) -> String? = { _, _ in nil }
    /// Dictation that could not be pasted stays on the clipboard.
    var copyText: (String) -> Void = { _ in }
    private var focusTarget: AnyObject?
    /// The open recording is dictation (dictation shortcut): pasted only, never sent to the assistant.
    private(set) var dictating = false
    let backend: any AssistantBackend
    let audio: any AudioCapture
    private var recordingTask: Task<Void, Never>?
    private var transcribeTask: Task<Void, Never>?
    private var requestTask: Task<Void, Never>?
    /// Assistant requests (ask + read aloud) run beside recording. A new request never waits in a queue:
    /// the backend steers it into a request that is still generating (or replaces that one); a reply being read aloud stops.
    private struct Job { var task: Task<Void, Never>?; var asking = true; var speaking = false; var muted = false }
    private var jobs: [Int: Job] = [:]
    private var lastJob = 0
    private var shownJob = 0
    private var jobPhase: Phase?
    private var jobStatus = ""
    private var jobError: Error?
    /// Recording/transcription owns the ring until it ends; the assistant state is shown again afterwards.
    private var capturing = false
    private var speechPaused = false
    private var speechGate: Task<Void, Never>?
    private var holdTask: Task<Void, Never>?
    private var holdDictation = false
    private var meterTask: Task<Void, Never>?
    private var ceilingTask: Task<Void, Never>?
    private var feedbackTask: Task<Void, Never>?
    private var chordDown = false
    private var dictationDown = false
    private var ready = false

    init(backend: any AssistantBackend, audio: any AudioCapture) { self.backend = backend; self.audio = audio }
    private var idleStatus: String { "\(shortcutLabel) • double-click to type" }
    func connect() {
        Task { do { try await backend.connect(); ready = true; phase = .idle; status = idleStatus }
            catch { fail(error) } }
    }
    /// Assistant shortcut held / released.
    func modifiers(_ held: Bool) {
        guard held != chordDown else { return }; chordDown = held
        hold(held, dictation: false)
    }
    /// Dictation shortcut held / released.
    func dictationKeys(_ held: Bool) {
        guard held != dictationDown else { return }; dictationDown = held
        hold(held, dictation: true)
    }
    private func hold(_ held: Bool, dictation: Bool) {
        if held {
            holdTask?.cancel(); holdDictation = dictation
            holdTask = Task {
                // Intentional hold threshold avoids activation on accidental modifier taps.
                try? await Task.sleep(for: .milliseconds(350))
                guard !Task.isCancelled, dictation ? dictationDown : chordDown else { return }; beginListening(dictation: dictation)
            }
            return
        }
        if holdDictation == dictation { holdTask?.cancel() }
        guard capturing, dictating == dictation else { return }
        if phase == .listening { finishListening() }
        else if phase == .preparing { discardRecording(); restore() }
    }
    func beginListening(dictation: Bool = false) {
        guard ready, !capturing, !changingMode, phase != .starting else { return }
        feedbackTask?.cancel()
        dictating = dictation
        focusTarget = dictation ? captureFocus() : nil // The user's text cursor at the moment they start speaking.
        capturing = true; phase = .preparing; status = "Preparing microphone…"
        // Either shortcut stops the assistant's voice at once (stopped, not paused). Dictation also keeps the
        // running request quiet: its reply is shown, not read aloud.
        if dictation { for id in jobs.keys { jobs[id]?.muted = true } }
        stopSpeech(); holdSpeech(true)
        recordingTask = Task {
            do {
                try Task.checkCancellation()
                try await audio.start()
                guard !Task.isCancelled, phase == .preparing else { audio.cancel(); return }
                phase = .listening; status = dictation ? "Dikte / Dictation… release the keys" : "Listening… release the keys"
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
    private func gate(_ action: @escaping @Sendable (any AssistantBackend) async -> Void) {
        let previous = speechGate, backend = backend
        speechGate = Task { await previous?.value; await action(backend) }
    }
    /// Ends the reply being read aloud; its job is over (it does not continue later).
    private func stopSpeech() {
        for (id, job) in jobs where job.speaking { job.task?.cancel(); jobs[id] = nil }
        gate { await $0.stopSpeech() }
        refreshJob()
    }
    /// Holds a reply that starts while the microphone is open (it would be recorded), then lets it play.
    private func holdSpeech(_ pause: Bool) {
        guard pause != speechPaused else { return }; speechPaused = pause
        gate { if pause { await $0.pauseSpeech() } else { await $0.resumeSpeech() } }
    }
    private func discardRecording() {
        recordingTask?.cancel(); transcribeTask?.cancel(); meterTask?.cancel(); ceilingTask?.cancel()
        audio.cancel(); level = 0; focusTarget = nil; dictating = false; capturing = false; holdSpeech(false)
    }
    /// Recording finished: show the running assistant job (or idle) again.
    private func restore() {
        capturing = false; dictating = false; focusTarget = nil
        if let error = jobError { jobError = nil; fail(error) }
        else if let jobPhase { phase = jobPhase; status = jobStatus }
        else { phase = .idle; status = idleStatus }
    }
    func finishListening() {
        guard phase == .listening else { return }
        recordingTask?.cancel(); meterTask?.cancel(); ceilingTask?.cancel(); level = 0
        do {
            let wav = try audio.finish()
            guard !wav.isEmpty else { holdSpeech(false); restore(); return }
            let dictation = dictating
            phase = .thinking; status = dictation ? "Yazılıyor / Typing…" : "Transcribing on this Mac…"
            transcribeTask = Task {
                do { let text = try await backend.transcribe(wav); guard !Task.isCancelled else { return }
                    guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { holdSpeech(false); noSpeech(); return }
                    if dictation { holdSpeech(false); await dictated(text); return }
                    restore(); submit(text); holdSpeech(false) }
                catch TranscriptionError.noSpeech { if !Task.isCancelled { holdSpeech(false); noSpeech() } }
                catch { if !Task.isCancelled { holdSpeech(false); capturing = false; dictating = false; fail(error) } }
            }
        } catch { discardRecording(); fail(error) }
    }
    /// Dictation: typed where the user's cursor was (field still focused); otherwise left on the clipboard.
    private func dictated(_ text: String) async {
        let pasted = focusTarget.flatMap { pasteInto($0, text) }
        restore()
        if let pasted {
            if jobPhase == nil, phase == .idle { status = "Yapıştırıldı / Pasted • \(dictationLabel)" }
            await backend.dictation(app: pasted, chars: text.utf16.count); return
        }
        copyText(text)
        feedbackTask?.cancel()
        phase = .copied; status = "Panoya kopyalandı / Copied to clipboard"
        feedbackTask = Task {
            try? await Task.sleep(for: .milliseconds(1200)) // Short ring notice.
            guard !Task.isCancelled, phase == .copied, !capturing else { return }
            restore()
        }
    }
    private func noSpeech() {
        feedbackTask?.cancel(); capturing = false; dictating = false; focusTarget = nil
        phase = .noSpeech; status = "Ses algılanmadı / No speech detected"
        feedbackTask = Task {
            try? await Task.sleep(for: .milliseconds(450))
            guard !Task.isCancelled, phase == .noSpeech, !capturing else { return }
            restore()
        }
    }
    func send() {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard ready, !text.isEmpty, phase != .starting, !changingMode else { return }
        feedbackTask?.cancel(); holdTask?.cancel(); discardRecording()
        input = ""; restore(); submit(text)
    }
    /// A new assistant request starts now: a reply being read aloud stops; one still generating gets
    /// this text steered into it by the backend (or is replaced by it). Nothing waits in a queue.
    private func submit(_ text: String) {
        stopSpeech()
        lastJob += 1; let id = lastJob
        jobs[id] = Job()
        transcript = text
        refreshJob()
        jobs[id]?.task = Task { await run(id, text) }
    }
    private func run(_ id: Int, _ text: String) async {
        do {
            let result = try await backend.ask(text, offline: offline, readAloud: readAloud)
            guard !Task.isCancelled, jobs[id] != nil else { return }
            // Steered into the running request: that request's reply answers both.
            guard !result.steered, id > shownJob else { end(id); return }
            shownJob = id
            response = result.text; provider = result.provider + (result.fallback_reason.isEmpty ? "" : " • " + result.fallback_reason)
            jobs[id]?.asking = false
            if readAloud, jobs[id]?.muted == false {
                jobs[id]?.speaking = true; refreshJob()
                try await backend.speak(result.text)
            }
            guard !Task.isCancelled else { return }
            end(id)
        } catch {
            guard !Task.isCancelled, jobs[id] != nil else { return }
            let replaced = jobs.count > 1 // A newer request took this one over; it reports for both.
            end(id)
            guard !replaced else { return }
            if capturing { jobError = error } else { fail(error) }
        }
    }
    private func end(_ id: Int) { jobs[id] = nil; refreshJob() }
    private func refreshJob() {
        if jobs.values.contains(where: { $0.speaking }) { showJob(.speaking, "Speaking…") }
        else if jobs.values.contains(where: { $0.asking }) { showJob(.thinking, offline ? "Thinking locally…" : "Thinking with Pi…") }
        else { showJob(nil, idleStatus) }
    }
    private func showJob(_ value: Phase?, _ text: String) {
        jobPhase = value; jobStatus = text
        guard !capturing, phase != .noSpeech, phase != .copied else { return }
        phase = value ?? .idle; status = text
    }
    /// An update restart may start: nothing runs, records or speaks.
    var idleForRestart: Bool { ready && phase == .idle && jobs.isEmpty && !capturing && !changingMode }
    /// Proactive message (update offer/result): only at an idle moment, never over a request, recording or speech.
    /// Shown in the panel without focus and read aloud when enabled; false = not now (the backend offers it again later).
    func announce(_ text: String) -> Bool {
        guard ready, phase == .idle, jobs.isEmpty, !capturing, !changingMode, !text.isEmpty else { return false }
        response = text; provider = "Ayhan"; revealInput()
        guard readAloud else { return true }
        lastJob += 1; let id = lastJob
        jobs[id] = Job(asking: false, speaking: true); shownJob = id; refreshJob()
        jobs[id]?.task = Task { [weak self] in
            try? await self?.backend.speak(text)
            guard let self, !Task.isCancelled, self.jobs[id] != nil else { return }
            self.end(id)
        }
        return true
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
        for job in jobs.values { job.task?.cancel() }
        jobs = [:]; jobPhase = nil; jobStatus = ""; jobError = nil; capturing = false; dictating = false; speechPaused = false
        audio.cancel(); level = 0; focusTarget = nil; phase = .idle; status = "Cancelled"
        if notifyBackend { gate { await $0.cancel(); await $0.stopSpeech() } }
    }
    func newConversation() {
        cancel(); response = ""; transcript = ""; provider = ""
        Task { do { try await backend.reset(); status = "New conversation" } catch { fail(error) } }
    }
    /// Quit/Restart started: cancel everything and keep the ring in the quitting state.
    func beginQuit() { cancel(); quitting = true }
    func fail(_ error: Error) { guard !quitting else { return }; focusTarget = nil; dictating = false; phase = .error; status = error.localizedDescription; showInput() }
    func close() { cancel(); Task { await backend.close() } }
}
