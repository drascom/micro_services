import AVFoundation
import Foundation

@MainActor
protocol AudioCapture {
    func start() async throws
    func finish() throws -> Data
    func cancel()
    var level: Double { get }
}

@MainActor
final class AudioRecorder: AudioCapture {
    private var recorder: AVAudioRecorder?
    private var file: URL?
    func start() async throws {
        let permitted = await AVCaptureDevice.requestAccess(for: .audio)
        try Task.checkCancellation()
        guard permitted else { throw NSError(domain: "Assistant", code: 1, userInfo: [NSLocalizedDescriptionKey: "Allow microphone access in System Settings to use voice"] ) }
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathExtension("wav")
        let recorder = try AVAudioRecorder(url: file, settings: [
            AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000.0,
            AVNumberOfChannelsKey: 1, AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsBigEndianKey: false, AVLinearPCMIsFloatKey: false
        ])
        recorder.isMeteringEnabled = true
        guard recorder.record() else { throw NSError(domain: "Assistant", code: 2, userInfo: [NSLocalizedDescriptionKey: "Could not start microphone recording"] ) }
        self.file = file; self.recorder = recorder
    }
    func finish() throws -> Data {
        recorder?.stop(); recorder = nil
        guard let file else { return Data() }
        defer { try? FileManager.default.removeItem(at: file); self.file = nil }
        return try Data(contentsOf: file)
    }
    func cancel() { _ = try? finish() }
    var level: Double {
        recorder?.updateMeters()
        return min(1, max(0, (Double(recorder?.averagePower(forChannel: 0) ?? -60) + 60) / 60))
    }
}
