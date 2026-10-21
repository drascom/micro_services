import AppKit
import ApplicationServices

/// Voice dictation (dictation shortcut) into another app's focused text field; never sent to the assistant.
/// The target is captured when recording starts; pasting happens only if that same element still has focus.
@MainActor
enum Paster {
    final class Target { let element: AXUIElement; let bundle: String; init(_ e: AXUIElement, _ b: String) { element = e; bundle = b } }
    private static var prompted = false
    private static let editableRoles: Set<String> = ["AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"]

    /// The focused editable text element of another app, or nil. Without Accessibility, asks once per run.
    static func focusedTextTarget() -> Target? {
        guard AXIsProcessTrusted() else {
            if !prompted {
                prompted = true
                // "AXTrustedCheckOptionPrompt" = kAXTrustedCheckOptionPrompt (a C global, not Swift 6 concurrency-safe).
                _ = AXIsProcessTrustedWithOptions(["AXTrustedCheckOptionPrompt": true] as CFDictionary)
            }
            return nil
        }
        guard let element = focusedElement() else { return nil }
        var pid: pid_t = 0
        guard AXUIElementGetPid(element, &pid) == .success, pid != ProcessInfo.processInfo.processIdentifier,
              let app = NSRunningApplication(processIdentifier: pid) else { return nil }
        var role: CFTypeRef?
        let isTextRole = AXUIElementCopyAttributeValue(element, "AXRole" as CFString, &role) == .success && editableRoles.contains(role as? String ?? "")
        guard isTextRole || settable(element, "AXSelectedTextRange") || settable(element, "AXValue") else { return nil }
        return Target(element, app.bundleIdentifier ?? "pid \(pid)")
    }

    /// Pastes `text` if `target` still has focus; returns the target bundle id when pasted, nil otherwise.
    static func paste(_ text: String, into target: Target) -> String? {
        guard AXIsProcessTrusted(), let current = focusedElement(), CFEqual(current, target.element),
              let keys = commandV() else { return nil }
        let board = NSPasteboard.general
        let backup = snapshot(board)
        board.clearContents()
        guard board.setString(text, forType: .string) else { if !backup.isEmpty { board.writeObjects(backup) }; return nil }
        let ours = board.changeCount
        Task {
            try? await Task.sleep(for: .milliseconds(60)) // Let the pasteboard settle before ⌘V.
            keys.down.post(tap: .cghidEventTap); keys.up.post(tap: .cghidEventTap)
            try? await Task.sleep(for: .milliseconds(500)) // Give the target app time to read the pasteboard.
            // Restore only if nothing else replaced the clipboard meanwhile.
            if board.changeCount == ours { board.clearContents(); if !backup.isEmpty { board.writeObjects(backup) } }
        }
        return target.bundle
    }

    /// Not pasted (no field, focus moved, no permission): the text stays on the clipboard.
    static func copy(_ text: String) {
        let board = NSPasteboard.general
        board.clearContents(); board.setString(text, forType: .string)
    }

    private static func focusedElement() -> AXUIElement? {
        let system = AXUIElementCreateSystemWide()
        AXUIElementSetMessagingTimeout(system, 0.3) // Unresponsive apps must not stall the ring.
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(system, "AXFocusedUIElement" as CFString, &value) == .success,
              let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        return (value as! AXUIElement)
    }
    private static func settable(_ element: AXUIElement, _ attribute: String) -> Bool {
        var result: DarwinBoolean = false
        return AXUIElementIsAttributeSettable(element, attribute as CFString, &result) == .success && result.boolValue
    }
    /// Deep copy of every item and type, so copied images/files survive.
    private static func snapshot(_ board: NSPasteboard) -> [NSPasteboardItem] {
        (board.pasteboardItems ?? []).map { item in
            let copy = NSPasteboardItem()
            for type in item.types { if let data = item.data(forType: type) { copy.setData(data, forType: type) } }
            return copy
        }
    }
    private static func commandV() -> (down: CGEvent, up: CGEvent)? {
        let source = CGEventSource(stateID: .combinedSessionState)
        // Keep physically held modifiers out of the synthetic event.
        source?.setLocalEventsFilterDuringSuppressionState([.permitLocalMouseEvents, .permitLocalKeyboardEvents],
                                                           state: .eventSuppressionStateSuppressionInterval)
        let v: CGKeyCode = 9 // ANSI "v"
        guard let down = CGEvent(keyboardEventSource: source, virtualKey: v, keyDown: true),
              let up = CGEvent(keyboardEventSource: source, virtualKey: v, keyDown: false) else { return nil }
        down.flags = .maskCommand; up.flags = .maskCommand
        return (down, up)
    }
}
