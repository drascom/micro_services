import Foundation
import CoreGraphics

@main
struct ShortcutChecks {
    static func main() throws {
        let control = CGEventFlags.maskControl.rawValue, option = CGEventFlags.maskAlternate.rawValue
        var tracker = ShortcutTracker(shortcut: .standard)
        precondition(!tracker.update(type: .flagsChanged, keyCode: 0, flags: control))
        precondition(tracker.update(type: .flagsChanged, keyCode: 0, flags: control | option))
        precondition(!tracker.update(type: .flagsChanged, keyCode: 0, flags: option))
        let letter = Shortcut(modifiers: control | option, keyCode: 0, keyLabel: "A")
        tracker = ShortcutTracker(shortcut: letter)
        precondition(!tracker.update(type: .flagsChanged, keyCode: 0, flags: control | option))
        precondition(!tracker.update(type: .keyDown, keyCode: 1, flags: control | option))
        precondition(tracker.update(type: .keyDown, keyCode: 0, flags: control | option))
        precondition(!tracker.update(type: .keyUp, keyCode: 0, flags: control | option))
        _ = tracker.update(type: .keyDown, keyCode: 0, flags: control | option)
        tracker.reset()
        precondition(!tracker.update(type: .flagsChanged, keyCode: 0, flags: control | option))
        precondition(!Shortcut(modifiers: 0, keyCode: 0, keyLabel: "A").isValid)
        // Physical polling must notice releases even when no key-up event was delivered.
        precondition(!letter.matches(flags: control | option, isKeyDown: { _ in false }))
        precondition(!Shortcut.standard.matches(flags: 0, isKeyDown: { _ in true }))
        precondition(letter.matches(flags: control | option, isKeyDown: { $0 == 0 }))
        let suite = "assistant-shortcut-test-" + UUID().uuidString
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        precondition(Shortcut.load(from: defaults) == .standard)
        letter.save(to: defaults)
        precondition(Shortcut.load(from: defaults) == letter)
        defaults.set(Data("invalid".utf8), forKey: "voiceShortcut")
        precondition(Shortcut.load(from: defaults) == .standard)
        print("Shortcut checks passed: modifier chord, key chord, release, reset, validation, persistence.")
    }
}
