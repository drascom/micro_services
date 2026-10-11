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
    /// Dictation (hold to paste into the focused field); never the assistant's chord.
    static let dictationStandard = Shortcut(modifiers: CGEventFlags.maskControl.rawValue | CGEventFlags.maskShift.rawValue, keyCode: nil, keyLabel: "")
    private static let dictationFallbacks = [dictationStandard,
        Shortcut(modifiers: CGEventFlags.maskAlternate.rawValue | CGEventFlags.maskCommand.rawValue, keyCode: nil, keyLabel: ""),
        Shortcut(modifiers: CGEventFlags.maskControl.rawValue | CGEventFlags.maskCommand.rawValue, keyCode: nil, keyLabel: "")]
    /// Same chord, or one held inside the other (its modifiers and key are all part of the other).
    func conflicts(with other: Shortcut) -> Bool {
        func within(_ a: Shortcut, _ b: Shortcut) -> Bool { a.modifiers & b.modifiers == a.modifiers && (a.keyCode == nil || a.keyCode == b.keyCode) }
        return within(self, other) || within(other, self)
    }
    private static func stored(_ key: String, _ defaults: UserDefaults) -> Shortcut? {
        guard let data = defaults.data(forKey: key), let shortcut = try? JSONDecoder().decode(Shortcut.self, from: data), shortcut.isValid else { return nil }
        return shortcut
    }
    static func load(from defaults: UserDefaults = .standard) -> Shortcut { stored("voiceShortcut", defaults) ?? .standard }
    func save(to defaults: UserDefaults = .standard) { if isValid, let data = try? JSONEncoder().encode(self) { defaults.set(data, forKey: "voiceShortcut") } }
    /// The saved dictation chord, or the first default that does not clash with the assistant's.
    static func loadDictation(from defaults: UserDefaults = .standard) -> Shortcut {
        let assistant = load(from: defaults)
        if let saved = stored("dictationShortcut", defaults), !saved.conflicts(with: assistant) { return saved }
        return dictationFallbacks.first { !$0.conflicts(with: assistant) } ?? dictationStandard
    }
    func saveDictation(to defaults: UserDefaults = .standard) { if isValid, let data = try? JSONEncoder().encode(self) { defaults.set(data, forKey: "dictationShortcut") } }
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
