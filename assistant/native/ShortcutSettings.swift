import AppKit

@MainActor
final class ShortcutCaptureView: NSView {
    var candidate: Shortcut
    private var previousFlags: UInt64 = 0
    private let display = NSTextField(labelWithString: "")
    init(shortcut: Shortcut) {
        candidate = shortcut
        super.init(frame: NSRect(x: 0, y: 0, width: 340, height: 90))
        display.frame = NSRect(x: 10, y: 22, width: 320, height: 42)
        display.font = .monospacedSystemFont(ofSize: 26, weight: .medium)
        display.alignment = .center; addSubview(display); refresh()
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) unavailable") }
    override var acceptsFirstResponder: Bool { true }
    override func mouseDown(with event: NSEvent) { window?.makeFirstResponder(self) }
    override func flagsChanged(with event: NSEvent) {
        let flags = UInt64(event.modifierFlags.rawValue) & Shortcut.modifierMask
        let addedModifier = flags & ~previousFlags != 0
        previousFlags = flags
        if addedModifier { candidate = Shortcut(modifiers: flags, keyCode: nil, keyLabel: ""); refresh() }
    }
    override func keyDown(with event: NSEvent) {
        let flags = UInt64(event.modifierFlags.rawValue) & Shortcut.modifierMask
        guard flags != 0, !event.isARepeat else { return }
        let special: [UInt16: String] = [36: "↩", 48: "⇥", 49: "Space", 51: "⌫", 53: "Esc", 123: "←", 124: "→", 125: "↓", 126: "↑"]
        let label = special[event.keyCode] ?? event.charactersIgnoringModifiers?.uppercased() ?? "Key \(event.keyCode)"
        candidate = Shortcut(modifiers: flags, keyCode: event.keyCode, keyLabel: label)
        refresh()
    }
    override func keyUp(with event: NSEvent) {} // Retain the captured chord after release.
    private func refresh() { display.stringValue = candidate.label }
}
