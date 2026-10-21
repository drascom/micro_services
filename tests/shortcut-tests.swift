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
        // Dictation chord: default never clashes with the assistant's; same or nested chords conflict.
        let shift = CGEventFlags.maskShift.rawValue, command = CGEventFlags.maskCommand.rawValue
        precondition(!Shortcut.dictationStandard.conflicts(with: .standard))
        precondition(Shortcut.standard.conflicts(with: .standard))
        precondition(Shortcut(modifiers: control, keyCode: nil, keyLabel: "").conflicts(with: .standard))
        precondition(Shortcut.standard.conflicts(with: letter) && letter.conflicts(with: .standard))
        precondition(!letter.conflicts(with: Shortcut(modifiers: control | option, keyCode: 1, keyLabel: "S")))
        Shortcut(modifiers: control | shift, keyCode: nil, keyLabel: "").save(to: defaults)
        let fallback = Shortcut.loadDictation(from: defaults)
        precondition(!fallback.conflicts(with: Shortcut.load(from: defaults)) && fallback.modifiers == option | command)
        Shortcut(modifiers: control, keyCode: nil, keyLabel: "").saveDictation(to: defaults)
        precondition(Shortcut.loadDictation(from: defaults).modifiers == option | command) // a clashing saved chord is ignored
        Shortcut.standard.save(to: defaults)
        let custom = Shortcut(modifiers: command | shift, keyCode: 2, keyLabel: "D")
        custom.saveDictation(to: defaults)
        precondition(Shortcut.loadDictation(from: defaults) == custom)
        print("Shortcut checks passed: modifier chord, key chord, release, reset, validation, persistence, dictation chord conflicts.")
    }
}
