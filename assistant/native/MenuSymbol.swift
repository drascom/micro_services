import AppKit

/// Ring context menu icons. macOS 27 hides menu item images by default (`preferredImageVisibility` = automatic),
/// so each item opts in explicitly.
enum MenuSymbol {
    static let names = ["xmark.circle", "bubble.left.and.bubble.right", "cloud", "desktopcomputer", "gearshape", "arrow.clockwise", "power"]
    static func image(_ name: String, _ label: String) -> NSImage? {
        let image = NSImage(systemSymbolName: name, accessibilityDescription: label)?
            .withSymbolConfiguration(.init(pointSize: 13, weight: .regular, scale: .small))
        image?.isTemplate = true
        return image
    }
    @MainActor static func apply(_ name: String, to item: NSMenuItem) {
        item.image = image(name, item.title)
        if #available(macOS 27.0, *) { item.preferredImageVisibility = .visible }
    }
}
