import AppKit
import CoreGraphics

@MainActor
final class HotkeyMonitor {
    private var tap: CFMachPort?
    private var source: CFRunLoopSource?
    private var releaseWatchdog: Task<Void, Never>?
    private let model: RingModel
    private var tracker = ShortcutTracker(shortcut: .load())
    var suspended = false { didSet { tracker.reset(); model.modifiers(false) } }
    func setShortcut(_ shortcut: Shortcut) { tracker = ShortcutTracker(shortcut: shortcut); model.shortcutLabel = shortcut.label; model.modifiers(false) }
    init(model: RingModel) { self.model = model }
    func start(requestPermission: Bool = false) {
        stop()
        model.shortcutLabel = tracker.shortcut.label
        if requestPermission { _ = CGRequestListenEventAccess() }
        // C event-tap trampoline is required by CoreGraphics; delivery is marshalled to MainActor.
        let callback: CGEventTapCallBack = { _, type, event, pointer in
            guard let pointer else { return Unmanaged.passUnretained(event) }
            let address = UInt(bitPattern: pointer)
            let flags = event.flags.rawValue
            let rawType = type.rawValue
            let keyCode = UInt16(event.getIntegerValueField(.keyboardEventKeycode))
            let disabled = type == .tapDisabledByTimeout || type == .tapDisabledByUserInput
            Task { @MainActor in
                guard let pointer = UnsafeMutableRawPointer(bitPattern: address) else { return }
                let monitor = Unmanaged<HotkeyMonitor>.fromOpaque(pointer).takeUnretainedValue()
                if disabled, let tap = monitor.tap {
                    monitor.tracker.reset(); monitor.model.modifiers(false)
                    CGEvent.tapEnable(tap: tap, enable: true)
                }
                else if !monitor.suspended, let type = CGEventType(rawValue: rawType) {
                    monitor.model.modifiers(monitor.tracker.update(type: type, keyCode: keyCode, flags: flags))
                }
            }
            return Unmanaged.passUnretained(event)
        }
        tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly,
            eventsOfInterest: CGEventMask((1 << CGEventType.flagsChanged.rawValue) | (1 << CGEventType.keyDown.rawValue) | (1 << CGEventType.keyUp.rawValue)), callback: callback,
            userInfo: Unmanaged.passUnretained(self).toOpaque())
        if let tap {
            source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
            CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
            CGEvent.tapEnable(tap: tap, enable: true); model.shortcutEnabled = true
            releaseWatchdog = Task { [weak self] in
                while !Task.isCancelled {
                    // Permission dialogs can swallow key-up events. Physical state closes that gap.
                    if let self, !self.suspended {
                        let held = self.tracker.shortcut.matches(flags: CGEventSource.flagsState(.combinedSessionState).rawValue,
                            isKeyDown: { CGEventSource.keyState(.combinedSessionState, key: $0) })
                        if !held { self.tracker.reset() }
                        self.model.modifiers(held)
                    }
                    try? await Task.sleep(for: .milliseconds(75))
                }
            }
        } else { model.shortcutEnabled = false }
    }
    func stop() {
        releaseWatchdog?.cancel(); releaseWatchdog = nil
        tracker.reset(); model.modifiers(false); model.shortcutEnabled = false
        if let source { CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes) }
        if let tap { CFMachPortInvalidate(tap) }
        source = nil; tap = nil
    }
}
