import AppKit
import CoreGraphics

struct Shortcut: Codable, Equatable {
    static let modifierMask = CGEventFlags.maskControl.rawValue | CGEventFlags.maskAlternate.rawValue |
        CGEventFlags.maskCommand.rawValue | CGEventFlags.maskShift.rawValue | CGEventFlags.maskSecondaryFn.rawValue
    static let standard = Shortcut(modifiers: CGEventFlags.maskControl.rawValue | CGEventFlags.maskAlternate.rawValue, keyCode: nil, keyLabel: "")
    var modifiers: UInt64
    var keyCode: UInt16?
    var keyLabel: String
    var isValid: Bool { modifiers != 0 && modifiers & ~Self.modifierMask == 0 }
    var label: String {
        var symbols = ""
        for (flag, symbol) in [(CGEventFlags.maskControl, "⌃"), (.maskAlternate, "⌥"), (.maskShift, "⇧"), (.maskCommand, "⌘"), (.maskSecondaryFn, "fn")] {
            if modifiers & flag.rawValue != 0 { symbols += symbol }
        }
        return symbols + (keyCode == nil ? "" : " " + keyLabel)
    }
    func matches(flags: UInt64, isKeyDown: (UInt16) -> Bool) -> Bool {
        flags & Self.modifierMask == modifiers && (keyCode.map(isKeyDown) ?? true)
    }
    static func load(from defaults: UserDefaults = .standard) -> Shortcut {
        guard let data = defaults.data(forKey: "voiceShortcut"), let shortcut = try? JSONDecoder().decode(Shortcut.self, from: data), shortcut.isValid else { return .standard }
        return shortcut
    }
    func save(to defaults: UserDefaults = .standard) { if isValid, let data = try? JSONEncoder().encode(self) { defaults.set(data, forKey: "voiceShortcut") } }
}

struct ShortcutTracker {
    var shortcut: Shortcut
    private var keys: Set<UInt16> = []
    mutating func update(type: CGEventType, keyCode: UInt16, flags: UInt64) -> Bool {
        if type == .keyDown { keys.insert(keyCode) }
        if type == .keyUp { keys.remove(keyCode) }
        return shortcut.matches(flags: flags, isKeyDown: { keys.contains($0) })
    }
    mutating func reset() { keys.removeAll() }
}
