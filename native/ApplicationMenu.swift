import AppKit

@MainActor
enum ApplicationMenu {
    static func install() {
        let main = NSMenu()
        let application = NSMenu(title: "Ayhan")
        application.addItem(withTitle: "Çıkış / Quit Ayhan", action: Selector(("requestQuit:")), keyEquivalent: "q") // AppDelegate: ignored while already quitting
        let appItem = NSMenuItem()
        appItem.submenu = application
        main.addItem(appItem)

        // Nil targets let AppKit route commands to the focused field editor or selected response.
        let edit = NSMenu(title: "Düzen / Edit")
        for (title, action, key, modifiers) in [
            ("Geri al / Undo", Selector(("undo:")), "z", NSEvent.ModifierFlags.command),
            ("Yinele / Redo", Selector(("redo:")), "z", [.command, .shift]),
            ("Kes / Cut", #selector(NSText.cut(_:)), "x", .command),
            ("Kopyala / Copy", #selector(NSText.copy(_:)), "c", .command),
            ("Yapıştır / Paste", #selector(NSText.paste(_:)), "v", .command),
            ("Tümünü seç / Select All", #selector(NSText.selectAll(_:)), "a", .command)
        ] {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
            item.keyEquivalentModifierMask = modifiers
            edit.addItem(item)
        }
        let editItem = NSMenuItem()
        editItem.submenu = edit
        main.addItem(editItem)

        let window = NSMenu(title: "Pencere / Window")
        window.addItem(withTitle: "Kapat / Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        let windowItem = NSMenuItem()
        windowItem.submenu = window
        main.addItem(windowItem)
        NSApp.mainMenu = main
    }
}
