import AppKit

/// Ring context menu: every symbol resolves on this macOS, and the image is laid out (macOS 27 hides it unless opted in).
@main @MainActor struct MenuChecks {
    static func main() {
        _ = NSApplication.shared
        for name in MenuSymbol.names { precondition(MenuSymbol.image(name, name) != nil, "missing symbol \(name)") }
        let title = "Yeni konuşma başlat uzun başlık"
        let plain = NSMenu(); plain.addItem(NSMenuItem(title: title, action: #selector(NSApplication.terminate(_:)), keyEquivalent: ""))
        let iconic = NSMenu(); let item = NSMenuItem(title: title, action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        MenuSymbol.apply("power", to: item); iconic.addItem(item)
        precondition(item.image != nil, "image not set")
        precondition(iconic.size.width > plain.size.width, "menu image hidden: \(iconic.size) vs \(plain.size)")
        print("menu checks passed")
    }
}
