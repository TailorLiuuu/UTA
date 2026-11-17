import SwiftUI
import AppKit

@main struct RemoteDeviceTests {
    @MainActor static func main() async throws {
        let host = Host(id: String(repeating: "a", count: 32), name: "Test server", target: "synthetic-only")
        let model = RemoteDeviceModel()
        await model.perform("list", host: host)
        precondition(model.devices.count == 2 && model.selected.isEmpty, "Never silently choose the first device")
        model.selected = String(repeating: "b", count: 32)
        await model.perform("logs", host: host, extra: ["--id", model.selected, "--baud", "115200", "--seconds", "1"])
        try await Task.sleep(for: .milliseconds(150))
        precondition(!model.busy && model.output.contains("测试日志：中"), "Preserve UTF8 across serial frames")
        precondition(model.runID == String(repeating: "f", count: 32))
        let view = NSHostingView(rootView: RemoteDevicePanel(host: host, model: model).padding(20).frame(width: 940).preferredColorScheme(.light))
        view.frame = NSRect(x: 0, y: 0, width: 940, height: 900)
        view.layoutSubtreeIfNeeded()
        if let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) {
            view.cacheDisplay(in: view.bounds, to: bitmap)
            if let data = bitmap.representation(using: .png, properties: [:]) {
                try data.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
            }
        }
        print("Remote device list, explicit selection, streamed UTF8 logs, operation receipt and offscreen UI rendering passed")
    }
}
