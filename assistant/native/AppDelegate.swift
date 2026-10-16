import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuItemValidation {
    private let client: BackendClient
    private let model: RingModel
    private let admin: AdminModel
    private var adminWindow: NSWindow?
    private var pet: NSPanel?
    private var input: NSPanel?
    private var hotkey: HotkeyMonitor?
    private var statusItem: NSStatusItem?
    private var menu: NSMenu?
    private var restartRequested = false
    private var restartReopensAdmin = false
    private var relauncher: Process?
    private var terminating = false
    override init() {
        client = BackendClient()
        model = RingModel(backend: client, audio: AudioRecorder())
        model.captureFocus = { Paster.focusedTextTarget() }
        model.pasteInto = { target, text in (target as? Paster.Target).flatMap { Paster.paste(text, into: $0) } }
        model.copyText = { Paster.copy($0) }
        admin = AdminModel(backend: client, ring: model)
        super.init()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 240, height: 260), styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isOpaque = false; panel.backgroundColor = .clear; panel.hasShadow = false
        panel.level = .floating; panel.hidesOnDeactivate = false; panel.isMovableByWindowBackground = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.contentView = NSHostingView(rootView: RingView(model: model))
        panel.delegate = self
        if let screen = NSScreen.main?.visibleFrame { panel.setFrameOrigin(NSPoint(x: screen.maxX - 280, y: screen.minY + 65)) }
        panel.orderFrontRegardless(); pet = panel
        model.showInput = { [weak self] in self?.showInput() }
        model.toggleInput = { [weak self] in self?.toggleInput() }
        model.revealInput = { [weak self] in self?.revealInput() }
        hotkey = HotkeyMonitor(model: model); hotkey?.start()
        model.contextMenu = { [weak self] in self?.showContextMenu() }
        // Minimal context menu; settings, status and activity live in the Admin window.
        let menu = NSMenu()
        // Turkish-only titles; typing a request stays on double-click of the ring.
        for (title, symbol, action) in [("İsteği iptal et", "xmark.circle", #selector(cancelRequest)),
                                        ("Yeni konuşma", "bubble.left.and.bubble.right", #selector(reset)),
                                        ("Uzak model", "cloud", #selector(toggleRemote)),
                                        ("Yönetim…", "gearshape", #selector(openAdminMenu)),
                                        ("Yeniden başlat", "arrow.clockwise", #selector(restart)),
                                        ("Çıkış", "power", #selector(quit))] {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: ""); item.target = self
            MenuSymbol.apply(symbol, to: item)
            if action == #selector(openAdminMenu) || action == #selector(restart) { menu.addItem(.separator()) }
            menu.addItem(item)
        }
        admin.actions = AdminModel.Actions(
            changeShortcut: { [weak self] in self?.changeShortcut() },
            changeDictationShortcut: { [weak self] in self?.changeShortcut(dictation: true) },
            editInstructions: { [weak self] in self?.editInstructions() },
            openActivityLog: { [weak self] in self?.openActivityLog() },
            restart: { [weak self] in self?.restart() },
            setMenuBarIcon: { [weak self] visible in self?.setStatusIcon(visible) })
        self.menu = menu
        // System sleep locks the password vault (the backend drops its in-memory session key).
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(systemWillSleep), name: NSWorkspace.willSleepNotification, object: nil)
        updateStatusIcon()
        model.connect()
        secretPrompts()
        announcements()
        // Set only by a restart that happened while Admin was open; a normal launch never opens Admin.
        // The one-shot defaults flag is the primary signal (LaunchServices may drop --args); consume it at once.
        let defaults = UserDefaults.standard
        let flagged = defaults.bool(forKey: Self.reopenAdminKey)
        if flagged { defaults.removeObject(forKey: Self.reopenAdminKey); defaults.synchronize() }
        if flagged || CommandLine.arguments.contains(Self.openAdminArgument) {
            // Next runloop turn, after launch finishes, so the background-launched accessory app can take focus.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in self?.openAdmin(forceFront: true) }
        }
    }
    /// vault_save "prompt": the backend asks for a secret through this long-poll; it is typed here (secure field)
    /// and sent straight back over the token-protected loopback, never through the model.
    private func secretPrompts() {
        Task { [weak self] in
            while !Task.isCancelled {
                guard let client = self?.client else { return }
                do {
                    let data = try await client.admin("secret-prompt/wait")
                    guard let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let id = value["id"] as? String else { continue }
                    let secret = self?.askSecret(value["title"] as? String ?? "")
                    let body: [String: Any] = secret.map { ["id": id, "value": $0] } ?? ["id": id, "cancel": true]
                    if let json = try? JSONSerialization.data(withJSONObject: body) { _ = try? await client.admin("secret-prompt/answer", data: json) }
                } catch { try? await Task.sleep(for: .seconds(2)) } // Backend not up yet or restarting.
            }
        }
    }
    private func askSecret(_ title: String) -> String? {
        let alert = NSAlert()
        alert.messageText = "Ayhan • \(title)"
        alert.informativeText = "Kasaya kaydedilecek değer / Value to save in the vault"
        let field = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        alert.accessoryView = field
        alert.addButton(withTitle: "Kaydet / Save"); alert.addButton(withTitle: "İptal / Cancel")
        alert.window.initialFirstResponder = field
        NSApp.activate(ignoringOtherApps: true)
        let saved = alert.runModal() == .alertFirstButtonReturn
        let value = field.stringValue
        field.stringValue = ""
        return saved && !value.isEmpty ? value : nil
    }
    /// Proactive messages (update offer, update results): the backend answers only while nothing runs; the ring shows
    /// one when it is idle (waits up to a minute), then reports it shown so an offer is asked at most once a day.
    private func announcements() {
        Task { [weak self] in
            while !Task.isCancelled {
                guard let client = self?.client else { return }
                do {
                    let data = try await client.admin("announce/wait")
                    guard let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                          let id = value["id"] as? String, let text = value["text"] as? String else { continue }
                    if value["restart"] as? Bool == true {
                        // Update restart (the user's yes to the update covers it): wait for an idle ring, then the
                        // backend confirms nothing runs and the usual restart path (orange ring) takes over.
                        while self?.model.idleForRestart == false { try await Task.sleep(for: .seconds(1)) }
                        let reply = try await client.admin("announce/shown", ["id": id])
                        if (try? JSONSerialization.jsonObject(with: reply) as? [String: Any])?["ok"] as? Bool == true {
                            LaunchGuard.log("restart: requested by an update")
                            self?.restart(); return
                        }
                        try await Task.sleep(for: .seconds(2)); continue
                    }
                    var shown = false
                    for _ in 0..<60 {
                        if self?.model.announce(text) == true { shown = true; break }
                        try await Task.sleep(for: .seconds(1)) // Wait for an idle ring.
                    }
                    if shown { _ = try? await client.admin("announce/shown", ["id": id]) }
                } catch { try? await Task.sleep(for: .seconds(5)) } // Backend not up yet or restarting.
            }
        }
    }
    private func revealInput() {
        if input?.isVisible == true { return }
        showInput(activate: false)
    }
    @objc private func systemWillSleep() { Task { _ = try? await client.admin("admin/bitwarden/lock") } }
    private static let openAdminArgument = "--open-admin"
    private static let reopenAdminKey = "reopenAdminOnLaunch"
    private func showInput(activate: Bool = true) {
        if input == nil {
            // Keep the native title bar outside the hosted view so its close button receives clicks.
            let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 360, height: 230),
                                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            panel.title = "Ayhan"; panel.titlebarAppearsTransparent = true; panel.titleVisibility = .hidden
            panel.isOpaque = false; panel.backgroundColor = .clear; panel.level = .floating
            panel.hidesOnDeactivate = false; panel.isMovableByWindowBackground = true
            panel.isReleasedWhenClosed = false
            panel.contentView = MovableHostingView(rootView: InputView(model: model))
            input = panel
        }
        positionInput()
        if activate { input?.makeKeyAndOrderFront(nil); NSApp.activate() } else { input?.orderFrontRegardless() }
    }
    private func toggleInput() {
        if input?.isVisible == true { input?.orderOut(nil) } else { showInput() }
    }
    private func positionInput() {
        guard let pet, let input else { return }
        let screen = pet.screen?.visibleFrame ?? NSScreen.main?.visibleFrame ?? pet.frame
        let width = input.frame.width, height = input.frame.height
        let x = min(max(pet.frame.midX - width / 2, screen.minX), screen.maxX - width)
        // Move the ring down when necessary so the popup can stay directly above it.
        if pet.frame.maxY + height + 8 > screen.maxY {
            pet.setFrameOrigin(NSPoint(x: pet.frame.minX, y: max(screen.minY, screen.maxY - height - 8 - pet.frame.height)))
        }
        input.setFrameOrigin(NSPoint(x: x, y: pet.frame.maxY + 8))
    }
    @objc private func openAdminMenu() { openAdmin() }
    private func openAdmin(forceFront: Bool = false) {
        if adminWindow == nil {
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 820, height: 560),
                                  styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            window.title = "Yönetim"; window.isReleasedWhenClosed = false
            window.isRestorable = false // Never brought back by macOS with stale content; a restart reopens it fresh.
            window.contentView = NSHostingView(rootView: AdminView(admin: admin))
            window.delegate = self; window.center()
            adminWindow = window
        }
        if adminWindow?.isVisible != true { admin.startPolling() }
        if adminWindow?.isMiniaturized == true { adminWindow?.deminiaturize(nil) }
        if forceFront {
            // Launched by the restart helper: cooperative activation may refuse focus, so force it and order front regardless.
            NSRunningApplication.current.activate(options: [.activateAllWindows])
            NSApp.activate(ignoringOtherApps: true)
            adminWindow?.makeKeyAndOrderFront(nil); adminWindow?.orderFrontRegardless()
        } else {
            adminWindow?.makeKeyAndOrderFront(nil); NSApp.activate()
        }
    }
    func windowWillClose(_ notification: Notification) {
        if let closing = notification.object as? NSWindow, closing === adminWindow { admin.stopPolling() }
    }
    func windowDidMove(_ notification: Notification) {
        if let moved = notification.object as? NSWindow, moved === pet, input?.isVisible == true { positionInput() }
    }
    @objc private func cancelRequest() { model.cancel() }
    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        if item.action == #selector(cancelRequest) { return model.phase != .idle && model.phase != .starting }
        if item.action == #selector(toggleRemote) {
            // Reflects the current mode each time the menu opens (same setting as Admin "Yerel kullan").
            let remote = !model.offline
            item.state = remote ? .on : .off
            MenuSymbol.apply(remote ? "cloud" : "desktopcomputer", to: item)
            return !model.changingMode
        }
        return true
    }
    /// on = remote provider (local fallback), off = local Gemma only.
    @objc private func toggleRemote() { model.setOffline(!model.offline) }
    private func editInstructions() {
        openTextFile("instructions.md", initial: "Do not use emoji in your responses.\n")
    }
    private func openActivityLog() {
        openTextFile("data/activity.log", initial: "")
    }
    private func openTextFile(_ filename: String, initial: String) {
        do {
            guard let settingsURL = Bundle.main.url(forResource: "launcher", withExtension: "json") else {
                throw NSError(domain: "Assistant", code: 1, userInfo: [NSLocalizedDescriptionKey: "Launcher settings missing"])
            }
            let settings = try JSONDecoder().decode(BackendClient.Settings.self, from: Data(contentsOf: settingsURL))
            let url = URL(fileURLWithPath: settings.root).appendingPathComponent(filename)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            if !FileManager.default.fileExists(atPath: url.path) {
                // Seed from a tracked example (e.g. instructions.example.md) when one exists.
                let example = url.deletingPathExtension().appendingPathExtension("example").appendingPathExtension(url.pathExtension)
                if FileManager.default.fileExists(atPath: example.path) {
                    try FileManager.default.copyItem(at: example, to: url)
                } else {
                    try initial.write(to: url, atomically: true, encoding: .utf8)
                }
            }
            NSWorkspace.shared.open([url], withApplicationAt: URL(fileURLWithPath: "/System/Applications/TextEdit.app"), configuration: NSWorkspace.OpenConfiguration()) { _, error in
                if let error { Task { @MainActor in NSAlert(error: error).runModal() } }
            }
        } catch { NSAlert(error: error).runModal() }
    }
    private func showContextMenu() {
        guard let menu, let view = pet?.contentView, let event = NSApp.currentEvent else { return }
        NSMenu.popUpContextMenu(menu, with: event, for: view)
    }
    private func updateStatusIcon() {
        let visible = UserDefaults.standard.bool(forKey: "showMenuBarIcon")
        if visible, statusItem == nil {
            statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
            statusItem?.button?.image = NSImage(systemSymbolName: "circle.hexagongrid", accessibilityDescription: "Ayhan Assistant")
            statusItem?.menu = menu
        } else if !visible, let item = statusItem {
            NSStatusBar.system.removeStatusItem(item); statusItem = nil
        }
    }
    private func setStatusIcon(_ visible: Bool) {
        UserDefaults.standard.set(visible, forKey: "showMenuBarIcon")
        admin.menuBarIcon = visible
        updateStatusIcon()
    }
    private func changeShortcut(dictation: Bool = false) {
        hotkey?.suspended = true
        defer { hotkey?.suspended = false }
        let current = dictation ? Shortcut.loadDictation() : Shortcut.load()
        let other = dictation ? Shortcut.load() : Shortcut.loadDictation()
        let recorder = ShortcutCaptureView(shortcut: current)
        let alert = NSAlert()
        alert.messageText = dictation ? "Dikte kısayolunu değiştir" : "Kısayolu değiştir"
        alert.informativeText = "Tuşlara birlikte bas, bırak ve Tamam'a tıkla. En az bir Control, Option, Command veya Shift tuşu kullan."
        alert.accessoryView = recorder
        alert.addButton(withTitle: "Tamam"); alert.addButton(withTitle: "İptal")
        alert.window.initialFirstResponder = recorder
        NSApp.activate()
        guard alert.runModal() == .alertFirstButtonReturn, recorder.candidate != current else { return }
        // The two chords must differ and neither may be held inside the other.
        guard !recorder.candidate.conflicts(with: other) else {
            let refusal = NSAlert()
            refusal.messageText = "Kısayol çakışıyor"
            refusal.informativeText = "\(recorder.candidate.label) ↔ \(other.label)"
            refusal.runModal(); return
        }
        if dictation { recorder.candidate.saveDictation(); hotkey?.setDictationShortcut(recorder.candidate) }
        else { recorder.candidate.save(); hotkey?.setShortcut(recorder.candidate) }
        if !model.shortcutEnabled { hotkey?.start(requestPermission: true) }
    }
    @objc private func reset() { model.newConversation() }
    @objc private func restart() {
        guard !terminating else { LaunchGuard.log("restart ignored: already quitting"); return }
        // Capture Admin state now, before any termination work can close or hide it.
        restartReopensAdmin = adminWindow.map { $0.isVisible || $0.isMiniaturized } ?? false
        restartRequested = true; NSApp.terminate(nil)
    }
    @objc private func quit() { requestQuit(nil) }
    /// A second terminate: while the first waits for the backend makes AppKit exit at once, skipping the rest of
    /// the shutdown; repeated Quit/Restart clicks are therefore ignored until the first one finishes.
    @objc func requestQuit(_ sender: Any?) {
        guard !terminating else { LaunchGuard.log("quit ignored: already quitting"); return }
        NSApp.terminate(sender)
    }
    /// Canonical install path written at build time; falls back to the running bundle.
    private static func relaunchPath() -> String {
        if let path = Bundle.main.object(forInfoDictionaryKey: "PAInstallPath") as? String, path.hasSuffix(".app"),
           Bundle(path: path)?.executableURL.map({ FileManager.default.isExecutableFile(atPath: $0.path) }) == true {
            return path
        }
        return Bundle.main.bundleURL.path
    }
    /// Detached helper: waits (bounded) for this pid to exit, then opens the app; failures go to the launcher log.
    /// Started before the backend shutdown so even a forced immediate termination still relaunches.
    private func startRelauncher(reopenAdmin: Bool) throws {
        let helper = Process()
        helper.executableURL = URL(fileURLWithPath: "/bin/sh")
        let script = """
        i=0; while /bin/kill -0 "$1" 2>/dev/null && [ $i -lt 600 ]; do /bin/sleep 0.1; i=$((i+1)); done
        /usr/bin/open -n "$2" ${3:+--args "$3"} && exit 0
        /bin/sleep 1; /usr/bin/open -n "$2" ${3:+--args "$3"} && exit 0
        [ -n "$4" ] && echo "[restart] open failed twice: $2" >> "$4"; exit 1
        """
        let path = Self.relaunchPath()
        // Paths are arguments, never script text.
        helper.arguments = ["-c", script, "ayhan-restart", String(ProcessInfo.processInfo.processIdentifier), path,
                            reopenAdmin ? Self.openAdminArgument : "", LaunchGuard.logURL?.path ?? ""]
        helper.standardInput = FileHandle.nullDevice
        helper.standardOutput = FileHandle.nullDevice; helper.standardError = FileHandle.nullDevice
        try helper.run(); relauncher = helper
        LaunchGuard.log("restart: relauncher started for \(path)")
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        let reopenAdmin = restartRequested && restartReopensAdmin
        if restartRequested {
            if reopenAdmin { UserDefaults.standard.set(true, forKey: Self.reopenAdminKey); UserDefaults.standard.synchronize() }
            do { try startRelauncher(reopenAdmin: reopenAdmin) } catch {
                LaunchGuard.log("restart: relauncher failed: \(error.localizedDescription)")
                restartRequested = false; restartReopensAdmin = false
                UserDefaults.standard.removeObject(forKey: Self.reopenAdminKey); UserDefaults.standard.synchronize()
                NSAlert(error: error).runModal()
                return .terminateCancel
            }
        }
        terminating = true
        LaunchGuard.log(restartRequested ? "restarting" : "quitting")
        hotkey?.stop(); model.beginQuit() // Ring turns orange and stays so until exit.
        adminWindow?.close() // Closes with the old instance instead of lingering while the backend stops.
        Task {
            await model.backend.close()
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
    /// Also reached on a forced termination (system quit, second terminate:), where the async close never ran.
    func applicationWillTerminate(_ notification: Notification) { client.terminateNow() }
}
