import AppKit

// Renders two assets:
//   /tmp/gemma-icon-1024.png   — full-colour app icon (lightning behind, "G" in front)
//   <app/dir>/menubar.png      — monochrome template glyph for the menu-bar status item

func savePNG(_ image: NSImage, to path: String) {
    guard let tiff = image.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff),
          let png = rep.representation(using: .png, properties: [:]) else { return }
    try? png.write(to: URL(fileURLWithPath: path))
}

// Draw a template NSImage tinted with `color` into `rect` (colours opaque pixels only).
func drawTinted(_ template: NSImage, in rect: NSRect, color: NSColor) {
    let img = template.copy() as! NSImage
    img.isTemplate = false
    img.lockFocus()
    color.set()
    NSRect(origin: .zero, size: img.size).fill(using: .sourceAtop)
    img.unlockFocus()
    img.draw(in: rect)
}

func boltImage(pointSize: CGFloat) -> NSImage {
    let cfg = NSImage.SymbolConfiguration(pointSize: pointSize, weight: .black)
    let base = NSImage(systemSymbolName: "bolt.fill", accessibilityDescription: nil)!
    return base.withSymbolConfiguration(cfg) ?? base
}

// ---------- App icon (1024) ----------
func renderAppIcon() -> NSImage {
    let S: CGFloat = 1024
    let canvas = NSImage(size: NSSize(width: S, height: S))
    canvas.lockFocus()
    let ctx = NSGraphicsContext.current!.cgContext

    // Rounded-square background with a violet→indigo gradient.
    let full = NSRect(x: 0, y: 0, width: S, height: S)
    let bg = NSBezierPath(roundedRect: full, xRadius: 229, yRadius: 229)
    bg.addClip()
    let top    = NSColor(srgbRed: 0.15, green: 0.86, blue: 0.79, alpha: 1)   // #26DBC9 turquoise
    let bottom = NSColor(srgbRed: 0.04, green: 0.47, blue: 0.46, alpha: 1)   // #0B7875 deep teal
    NSGradient(colors: [top, bottom])!.draw(in: full, angle: -90)

    // Lightning bolt — golden, glowing, slightly left of centre so the G overlaps it.
    let bolt = boltImage(pointSize: 760)
    let bw = bolt.size.width, bh = bolt.size.height
    let scale = (S * 0.86) / bh
    let drawW = bw * scale, drawH = bh * scale
    let boltRect = NSRect(x: (S - drawW) / 2 - 18, y: (S - drawH) / 2, width: drawW, height: drawH)

    ctx.saveGState()
    let glow = NSShadow()
    glow.shadowColor = NSColor(srgbRed: 1.0, green: 0.34, blue: 0.13, alpha: 0.9)
    glow.shadowBlurRadius = 70
    glow.shadowOffset = .zero
    glow.set()
    drawTinted(bolt, in: boltRect, color: NSColor(srgbRed: 1.0, green: 0.45, blue: 0.26, alpha: 1)) // #FF7342 coral
    ctx.restoreGState()

    // "G" in front — heavy, white, with a soft dark shadow for legibility over the bolt.
    let para = NSMutableParagraphStyle(); para.alignment = .center
    let drop = NSShadow()
    drop.shadowColor = NSColor.black.withAlphaComponent(0.45)
    drop.shadowBlurRadius = 26
    drop.shadowOffset = NSSize(width: 0, height: -10)
    let attrs: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: 660, weight: .heavy),
        .foregroundColor: NSColor.white,
        .paragraphStyle: para,
        .shadow: drop,
    ]
    let g = "G" as NSString
    let bounds = g.size(withAttributes: attrs)
    g.draw(in: NSRect(x: 0, y: (S - bounds.height) / 2, width: S, height: bounds.height), withAttributes: attrs)

    canvas.unlockFocus()
    return canvas
}

// ---------- Menu-bar template glyph ----------
// Plain black lightning bolt on transparent → tints cleanly to any state colour.
// (No "G": at 18px a knocked-out letter just muddies the shape.)
func renderMenuGlyph() -> NSImage {
    let S: CGFloat = 256
    let img = NSImage(size: NSSize(width: S, height: S))
    img.lockFocus()

    let bolt = boltImage(pointSize: 200)
    let bw = bolt.size.width, bh = bolt.size.height
    let scale = (S * 0.96) / bh
    let drawW = bw * scale, drawH = bh * scale
    let rect = NSRect(x: (S - drawW) / 2, y: (S - drawH) / 2, width: drawW, height: drawH)
    drawTinted(bolt, in: rect, color: .black)

    img.unlockFocus()
    img.isTemplate = true
    return img
}

let appDir = (CommandLine.arguments.count > 1) ? CommandLine.arguments[1]
           : FileManager.default.currentDirectoryPath
savePNG(renderAppIcon(), to: "/tmp/gemma-icon-1024.png")
savePNG(renderMenuGlyph(), to: appDir + "/menubar.png")
print("Rendered /tmp/gemma-icon-1024.png and \(appDir)/menubar.png")
