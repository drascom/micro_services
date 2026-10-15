import AppKit

/// Startup/restart bookkeeping: one clear line per early exit or restart step in data/native-launcher.log,
/// and a single-instance check that waits for a previous instance to finish quitting instead of exiting silently.
enum LaunchGuard {
    /// data/native-launcher.log under the project root from the bundle's launcher.json (nil outside the app).
    static let logURL: URL? = {
        guard let url = Bundle.main.url(forResource: "launcher", withExtension: "json"),
              let value = (try? JSONSerialization.jsonObject(with: Data(contentsOf: url))) as? [String: Any],
              let root = value["root"] as? String else { return nil }
        return URL(fileURLWithPath: root).appendingPathComponent("data/native-launcher.log")
    }()
    static func log(_ line: String, to url: URL? = logURL) {
        guard let url else { return }
        let stamp = ISO8601DateFormatter().string(from: Date())
        let data = Data("[app \(stamp) pid \(getpid())] \(line)\n".utf8)
        if !FileManager.default.fileExists(atPath: url.path) { FileManager.default.createFile(atPath: url.path, contents: nil) }
        guard let handle = try? FileHandle(forWritingTo: url) else { return }
        _ = try? handle.seekToEnd(); try? handle.write(contentsOf: data); try? handle.close()
    }
    static func alive(_ pid: pid_t) -> Bool { pid > 0 && (kill(pid, 0) == 0 || errno == EPERM) }
    /// Polls until every pid has exited or the timeout passes; returns the ones still alive.
    static func waitForExit(_ pids: [pid_t], timeout: TimeInterval, isAlive: (pid_t) -> Bool = alive) -> [pid_t] {
        let deadline = Date().addingTimeInterval(timeout)
        var remaining = pids.filter(isAlive)
        while !remaining.isEmpty, Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
            remaining = remaining.filter(isAlive)
        }
        return remaining
    }
    /// Another instance (same bundle id) may still be quitting after a restart: wait for it rather than run twice.
    /// Returns false when one is still running after the wait; it is brought forward and this instance should exit.
    @MainActor static func claimSingleInstance(timeout: TimeInterval = 20) -> Bool {
        guard let id = Bundle.main.bundleIdentifier else { return true }
        let others = NSRunningApplication.runningApplications(withBundleIdentifier: id).filter { $0.processIdentifier != getpid() }
        if others.isEmpty { return true }
        let pids = others.map(\.processIdentifier)
        log("another instance is running (pid \(pids.map(String.init).joined(separator: ","))); waiting up to \(Int(timeout))s for it to quit")
        let remaining = waitForExit(pids, timeout: timeout)
        if remaining.isEmpty { log("previous instance exited; starting"); return true }
        log("previous instance still running (pid \(remaining.map(String.init).joined(separator: ","))); showing it and exiting")
        others.first { remaining.contains($0.processIdentifier) }?.activate()
        return false
    }
}
