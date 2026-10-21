// UTA's code-native vector mark, adapted from branding/uta/uta-logo-concept-v1.png.
// AppKit renders the same geometry at each macOS icon size; no resized screenshots.
import AppKit

let output = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
let teal = NSColor(srgbRed: 0x13 / 255.0, green: 0x7c / 255.0, blue: 0x77 / 255.0, alpha: 1)

func mark() {
    teal.setFill()
    teal.setStroke()
    // Coordinates use a top-left origin in a 512 × 512 square.
    let cable = NSBezierPath()
    cable.move(to: NSPoint(x: 112, y: 218))
    cable.line(to: NSPoint(x: 112, y: 305))
    cable.curve(to: NSPoint(x: 362, y: 305),
                controlPoint1: NSPoint(x: 112, y: 466),
                controlPoint2: NSPoint(x: 362, y: 466))
    cable.line(to: NSPoint(x: 362, y: 230))
    cable.lineWidth = 68
    cable.lineCapStyle = .round
    cable.stroke()
    NSBezierPath(roundedRect: NSRect(x: 55, y: 174, width: 114, height: 86),
                 xRadius: 15, yRadius: 15).fill()
    let plug = NSBezierPath(roundedRect: NSRect(x: 67, y: 92, width: 90, height: 70),
                            xRadius: 12, yRadius: 12)
    plug.fill()
    // Cut holes out of the mark so the sidebar remains transparent.
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current!.cgContext.setBlendMode(.clear)
    for x in [85.0, 122.0] {
        NSBezierPath(roundedRect: NSRect(x: x, y: 116, width: 18, height: 18),
                     xRadius: 3, yRadius: 3).fill()
    }
    NSGraphicsContext.restoreGraphicsState()
    for outer in [false, true] {
        let wave = NSBezierPath()
        wave.move(to: NSPoint(x: 342, y: outer ? 92 : 147))
        wave.curve(to: NSPoint(x: outer ? 472 : 420, y: outer ? 184 : 202),
                   controlPoint1: NSPoint(x: outer ? 401 : 378, y: outer ? 92 : 147),
                   controlPoint2: NSPoint(x: outer ? 450 : 407, y: outer ? 126 : 171))
        wave.lineWidth = 29
        wave.lineCapStyle = .round
        wave.stroke()
    }
}

func png(size: Int, appIcon: Bool) throws -> Data {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
                                 bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                 isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0,
                                 bitsPerPixel: 0)!
    let graphics = NSGraphicsContext(bitmapImageRep: bitmap)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = graphics
    let cg = graphics.cgContext
    cg.translateBy(x: 0, y: CGFloat(size))
    cg.scaleBy(x: CGFloat(size) / 512, y: -CGFloat(size) / 512)
    if appIcon {
        NSColor(srgbRed: 0.96, green: 0.98, blue: 0.98, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: 24, y: 24, width: 464, height: 464),
                     xRadius: 105, yRadius: 105).fill()
        cg.translateBy(x: 40, y: 32)
        cg.scaleBy(x: 0.82, y: 0.82)
    }
    // Render into a layer so transparent USB holes don't erase the icon tile.
    cg.beginTransparencyLayer(auxiliaryInfo: nil)
    mark()
    cg.endTransparencyLayer()
    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using: .png, properties: [:])!
}

try png(size: 512, appIcon: false).write(to: output.appendingPathComponent("UTAMark.png"))
let iconset = output.appendingPathComponent("UTA.iconset", isDirectory: true)
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)
for points in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let suffix = scale == 2 ? "@2x" : ""
        try png(size: points * scale, appIcon: true).write(
            to: iconset.appendingPathComponent("icon_\(points)x\(points)\(suffix).png"))
    }
}
