import AppKit
import Foundation

// ----- Configuration -----
// Values are read from config.json inside the app bundle (written by build.sh at
// install time). Hard-coded fallbacks keep the app usable if the file is missing.
let HOME    = NSHomeDirectory()
let LOGFILE = "/tmp/gemma-server.log"

func cfgString(_ obj: [String: Any]?, _ key: String, _ fallback: String) -> String {
    (obj?[key] as? String).map { $0.replacingOccurrences(of: "~", with: HOME) } ?? fallback
}
func cfgInt(_ obj: [String: Any]?, _ key: String, _ fallback: Int) -> Int {
    (obj?[key] as? Int) ?? fallback
}

let cfg: [String: Any]? = {
    guard let res = Bundle.main.resourcePath,
          let data = FileManager.default.contents(atPath: res + "/config.json"),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else { return nil }
    return obj
}()

let SERVER = cfgString(cfg, "server", "\(HOME)/.cache/gemma-server/llama.cpp/build/bin/llama-server")
let MODEL  = cfgString(cfg, "model",  "\(HOME)/.cache/gemma-server/models/gemma-4-12b-it-qat-q4_0.gguf")
let MMPROJ = cfgString(cfg, "mmproj", "\(HOME)/.cache/gemma-server/models/mmproj-gemma-4-12b-it-qat-q4_0.gguf")
let HOST   = cfgString(cfg, "host", "0.0.0.0")
let PORT   = cfgInt(cfg, "port", 8080)
let CTX    = cfgInt(cfg, "ctx", 32768)

enum ServerState { case stopped, starting, running }

final class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var process: Process?
    var state: ServerState = .stopped { didSet { updateUI() } }
    var healthTimer: Timer?
    var userStopped = false

    let statusLine = NSMenuItem(title: "Stopped", action: nil, keyEquivalent: "")
    let toggleItem = NSMenuItem(title: "Start Server", action: #selector(toggle), keyEquivalent: "s")
    let openUIItem = NSMenuItem(title: "Open Chat UI", action: #selector(openUI), keyEquivalent: "o")
    let copyItem   = NSMenuItem(title: "Copy LAN URL", action: #selector(copyURL), keyEquivalent: "c")

    func applicationDidFinishLaunching(_ note: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

        let menu = NSMenu()
        statusLine.isEnabled = false
        menu.addItem(statusLine)
        menu.addItem(.separator())
        for item in [toggleItem, openUIItem, copyItem] { item.target = self; menu.addItem(item) }
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit", action: #selector(quitApp), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        statusItem.menu = menu

        updateUI()
        // If a server is already up (started elsewhere), reflect that.
        checkHealth { ok in if ok { self.userStopped = false; self.state = .running } }
    }

    func applicationWillTerminate(_ note: Notification) {
        stopServer(userInitiated: false)
    }

    // ----- Actions -----
    @objc func toggle() {
        switch state {
        case .stopped:            startServer()
        case .starting, .running: stopServer(userInitiated: true)
        }
    }

    @objc func openUI() {
        if let url = URL(string: "http://127.0.0.1:\(PORT)") { NSWorkspace.shared.open(url) }
    }

    @objc func copyURL() {
        let ip = lanIP() ?? "127.0.0.1"
        let text = "http://\(ip):\(PORT)/v1"
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
    }

    @objc func quitApp() {
        stopServer(userInitiated: false)
        NSApp.terminate(nil)
    }

    // ----- Server lifecycle -----
    func startServer() {
        userStopped = false
        state = .starting
        // Clear any stray server holding the port, then launch shortly after.
        runSync("/usr/bin/pkill", ["-f", "llama-server.*--port \(PORT)"])
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in self?.launch() }
    }

    private func launch() {
        guard state == .starting else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: SERVER)
        p.arguments = [
            "-m", MODEL,
            "--mmproj", MMPROJ,
            "--alias", "gemma-4-12b",
            "-ngl", "999",
            "-c", "\(CTX)",
            "--jinja",
            "--host", HOST,
            "--port", "\(PORT)",
        ]
        FileManager.default.createFile(atPath: LOGFILE, contents: nil)
        if let handle = try? FileHandle(forWritingTo: URL(fileURLWithPath: LOGFILE)) {
            p.standardOutput = handle
            p.standardError = handle
        }
        p.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async { self?.serverExited() }
        }
        do {
            try p.run()
            process = p
            startHealthPolling()
        } catch {
            state = .stopped
            alert("Couldn't launch llama-server:\n\(error.localizedDescription)")
        }
    }

    func stopServer(userInitiated: Bool) {
        if userInitiated { userStopped = true }
        healthTimer?.invalidate(); healthTimer = nil
        if let p = process, p.isRunning { p.terminate() }
        // Fallback: also clear any externally-started server.
        runSync("/usr/bin/pkill", ["-f", "llama-server.*--port \(PORT)"])
        process = nil
        state = .stopped
    }

    private func serverExited() {
        process = nil
        healthTimer?.invalidate(); healthTimer = nil
        if !userStopped && state != .stopped {
            // Crashed/exited on its own.
            state = .stopped
            alert("llama-server stopped unexpectedly.\nSee \(LOGFILE) for details.")
        } else {
            state = .stopped
        }
    }

    // ----- Health polling -----
    private func startHealthPolling() {
        healthTimer?.invalidate()
        healthTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in
            self?.checkHealth { ok in
                guard let self = self else { return }
                if ok && self.state == .starting { self.state = .running }
            }
        }
    }

    private func checkHealth(_ completion: @escaping (Bool) -> Void) {
        guard let url = URL(string: "http://127.0.0.1:\(PORT)/health") else { completion(false); return }
        var req = URLRequest(url: url)
        req.timeoutInterval = 2.0
        URLSession.shared.dataTask(with: req) { data, resp, _ in
            let ok = (resp as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async { completion(ok) }
        }.resume()
    }

    // ----- UI -----
    private lazy var baseGlyph: NSImage = {
        if let dir = Bundle.main.resourcePath,
           let img = NSImage(contentsOfFile: dir + "/menubar.png") {
            img.isTemplate = true
            img.size = NSSize(width: 18, height: 18)
            return img
        }
        let fallback = NSImage(systemSymbolName: "bolt.fill", accessibilityDescription: "Gemma server")
                     ?? NSImage(size: NSSize(width: 18, height: 18))
        fallback.isTemplate = true
        return fallback
    }()

    // Paint the glyph in `color` ourselves (a coloured, non-template image). Relying on
    // NSStatusBarButton.contentTintColor with a template image is unreliable — the menu
    // bar forces template images to black/white and ignores the tint.
    private func tinted(_ base: NSImage, _ color: NSColor) -> NSImage {
        let size = NSSize(width: 18, height: 18)
        let img = NSImage(size: size)
        img.lockFocus()
        let rect = NSRect(origin: .zero, size: size)
        base.draw(in: rect)
        color.set()
        rect.fill(using: .sourceAtop)
        img.unlockFocus()
        img.isTemplate = false
        return img
    }

    func updateUI() {
        guard let button = statusItem?.button else { return }
        let tint: NSColor
        switch state {
        case .stopped:  tint = .systemGray
        case .starting: tint = .systemOrange
        case .running:  tint = .systemGreen
        }
        button.image = tinted(baseGlyph, tint)
        button.contentTintColor = nil

        switch state {
        case .stopped:
            statusLine.title = "Gemma — Stopped"
            toggleItem.title = "Start Server"
        case .starting:
            statusLine.title = "Gemma — Starting…"
            toggleItem.title = "Stop Server"
        case .running:
            statusLine.title = "Gemma — Running · :\(PORT) · ctx \(CTX)"
            toggleItem.title = "Stop Server"
        }
        let live = (state == .running)
        openUIItem.isEnabled = live
        copyItem.isEnabled = live
    }

    // ----- Helpers -----
    @discardableResult
    private func runSync(_ launchPath: String, _ args: [String]) -> Int32 {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: launchPath)
        p.arguments = args
        do { try p.run(); p.waitUntilExit(); return p.terminationStatus }
        catch { return -1 }
    }

    private func alert(_ message: String) {
        let a = NSAlert()
        a.messageText = "Gemma Server"
        a.informativeText = message
        a.alertStyle = .warning
        a.runModal()
    }

    private func lanIP() -> String? {
        var result: String?
        var ifaddr: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifaddr) == 0 else { return nil }
        defer { freeifaddrs(ifaddr) }
        var ptr = ifaddr
        while let cur = ptr {
            let iface = cur.pointee
            ptr = iface.ifa_next
            guard let addr = iface.ifa_addr, addr.pointee.sa_family == UInt8(AF_INET) else { continue }
            let name = String(cString: iface.ifa_name)
            guard name.hasPrefix("en") else { continue }
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            getnameinfo(addr, socklen_t(addr.pointee.sa_len), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST)
            let ip = String(cString: host)
            if !ip.hasPrefix("127.") && !ip.isEmpty { result = ip; break }
        }
        return result
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory) // menu-bar only, no Dock icon
app.run()
