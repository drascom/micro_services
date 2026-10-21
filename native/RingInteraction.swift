import AppKit
import SwiftUI

// Handle double-click and drag explicitly: SwiftUI hosting consumes background mouse events.
struct RingInteraction: NSViewRepresentable {
    let onDoubleClick: () -> Void
    let onRightClick: () -> Void
    var onClick: () -> Void = {}
    func makeNSView(context: Context) -> RingMouseView { let view = RingMouseView(); updateNSView(view, context: context); return view }
    func updateNSView(_ view: RingMouseView, context: Context) { view.onDoubleClick = onDoubleClick; view.onRightClick = onRightClick; view.onClick = onClick }
}

@MainActor
final class RingMouseView: NSView {
    var onDoubleClick: () -> Void = {}
    var onRightClick: () -> Void = {}
    var onClick: () -> Void = {}
    override func rightMouseDown(with event: NSEvent) { onRightClick() }
    private var downPoint = NSPoint.zero
    private var downOrigin = NSPoint.zero
    private var dragged = false
    override var acceptsFirstResponder: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) {
        downPoint = NSEvent.mouseLocation; downOrigin = window?.frame.origin ?? .zero; dragged = false
    }
    override func mouseDragged(with event: NSEvent) {
        let point = NSEvent.mouseLocation
        let dx = point.x - downPoint.x, dy = point.y - downPoint.y
        if hypot(dx, dy) > 4 { dragged = true }
        if dragged { window?.setFrameOrigin(NSPoint(x: downOrigin.x + dx, y: downOrigin.y + dy)) }
    }
    override func mouseUp(with event: NSEvent) {
        guard !dragged else { return }
        if event.clickCount == 2 { onDoubleClick() } else if event.clickCount == 1 { onClick() }
    }
}

@MainActor
final class MovableHostingView<Content: View>: NSHostingView<Content> {
    override var mouseDownCanMoveWindow: Bool { true }
}
