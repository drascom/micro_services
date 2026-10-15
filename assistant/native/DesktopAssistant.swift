import AppKit

@main
struct DesktopAssistant {
    @MainActor static func main() {
        guard LaunchGuard.claimSingleInstance() else { exit(0) }
        LaunchGuard.log("launched from \(Bundle.main.bundleURL.path)")
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        ApplicationMenu.install()
        app.run()
    }
}
