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
    private var relauncher: Process?
    override init() {
        client = BackendClient()
        model = RingModel(backend: client, audio: AudioRecorder())
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
        hotkey = HotkeyMonitor(model: model); hotkey?.start()
        model.contextMenu = { [weak self] in self?.showContextMenu() }
        // Minimal context menu; settings, status and activity live in the Admin window.
        let menu = NSMenu()
        for (title, action) in [("Mesaj yaz / Type a request…", #selector(openInput)),
                                ("İsteği iptal et / Cancel request", #selector(cancelRequest)),
                                ("Yeni konuşma / New conversation", #selector(reset)),
                                ("Yönetim / Admin…", #selector(openAdmin)),
                                ("Yeniden başlat / Restart Ayhan", #selector(restart)),
                                ("Çıkış / Quit Ayhan", #selector(quit))] {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: ""); item.target = self; menu.addItem(item)
        }
        admin.actions = AdminModel.Actions(
            changeShortcut: { [weak self] in self?.changeShortcut() },
            editInstructions: { [weak self] in self?.editInstructions() },
            openActivityLog: { [weak self] in self?.openActivityLog() },
            restart: { [weak self] in self?.restart() },
            setMenuBarIcon: { [weak self] visible in self?.setStatusIcon(visible) })
        self.menu = menu
        updateStatusIcon()
        model.connect()
    }
    private func showInput() {
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
        input?.makeKeyAndOrderFront(nil); NSApp.activate()
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
    @objc private func openAdmin() {
        if adminWindow == nil {
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 820, height: 560),
                                  styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            window.title = "Yönetim / Admin"; window.isReleasedWhenClosed = false
            window.contentView = NSHostingView(rootView: AdminView(admin: admin))
            window.delegate = self; window.center()
            adminWindow = window
        }
        if adminWindow?.isVisible != true { admin.startPolling() }
        adminWindow?.makeKeyAndOrderFront(nil); NSApp.activate()
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
        return true
    }
    @objc private func openInput() { showInput() }
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
                try initial.write(to: url, atomically: true, encoding: .utf8)
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
    private func changeShortcut() {
        hotkey?.suspended = true
        defer { hotkey?.suspended = false }
        let recorder = ShortcutCaptureView(shortcut: .load())
        let alert = NSAlert()
        alert.messageText = "Kısayolu değiştir / Change shortcut"
        alert.informativeText = "Tuşlara birlikte bas, bırak ve Tamam'a tıkla. En az bir Control, Option, Command veya Shift tuşu kullan.\nPress a chord, release it, then click OK. Include a modifier key."
        alert.accessoryView = recorder
        alert.addButton(withTitle: "Tamam / OK"); alert.addButton(withTitle: "İptal / Cancel")
        alert.window.initialFirstResponder = recorder
        NSApp.activate()
        if alert.runModal() == .alertFirstButtonReturn {
            recorder.candidate.save(); hotkey?.setShortcut(recorder.candidate)
            if !model.shortcutEnabled { hotkey?.start(requestPermission: true) }
        }
    }
    @objc private func reset() { model.newConversation() }
    @objc private func restart() { restartRequested = true; NSApp.terminate(nil) }
    @objc private func quit() { NSApp.terminate(nil) }
    /// Canonical install path written at build time; falls back to the running bundle.
    private static func relaunchPath() -> String {
        if let path = Bundle.main.object(forInfoDictionaryKey: "PAInstallPath") as? String, path.hasSuffix(".app"),
           Bundle(path: path)?.executableURL.map({ FileManager.default.isExecutableFile(atPath: $0.path) }) == true {
            return path
        }
        return Bundle.main.bundleURL.path
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        hotkey?.stop(); model.cancel()
        Task {
            await model.backend.close()
            if restartRequested {
                do {
                    let helper = Process()
                    helper.executableURL = URL(fileURLWithPath: "/bin/sh")
                    // Pass paths as arguments; wait until this app exits before reopening.
                    helper.arguments = ["-c", "while /bin/kill -0 \"$1\" 2>/dev/null; do /bin/sleep 0.1; done; exec /usr/bin/open -n \"$2\"",
                                        "ayhan-restart", String(ProcessInfo.processInfo.processIdentifier), Self.relaunchPath()]
                    helper.standardInput = FileHandle.nullDevice
                    helper.standardOutput = FileHandle.nullDevice; helper.standardError = FileHandle.nullDevice
                    try helper.run(); relauncher = helper
                } catch {
                    restartRequested = false
                    NSApp.reply(toApplicationShouldTerminate: false)
                    NSAlert(error: error).runModal()
                    hotkey?.start(); model.connect()
                    return
                }
            }
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}
