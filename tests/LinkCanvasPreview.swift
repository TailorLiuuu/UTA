// Isolated UI fixture: no controller, SSH, USB scanning, helper or production preferences.
import SwiftUI
import AppKit
struct Host: Identifiable { var id: String; var name: String; var target: String }
struct PreviewState { var servers = [Host(id: String(repeating: "a", count: 32), name: "Build server", target: "build-host"), Host(id: String(repeating: "b", count: 32), name: "Windows station", target: "windows-host")] }
@MainActor final class Model: ObservableObject {
    @Published var state = PreviewState()
    func host(_ id: String) -> Host? { state.servers.first { $0.id == id } }
}
@MainActor final class AppLanguage: ObservableObject {
    static let shared = AppLanguage()
    @Published var code = "zh-Hans"
}
let accent = Color(red: 0.07, green: 0.48, blue: 0.46)
let ink = Color(red: 0.12, green: 0.18, blue: 0.23)
let muted = Color(red: 0.43, green: 0.48, blue: 0.52)
#if !LINK_WORKSPACE_TESTS
@main struct LinkPreviewApp: App {
    @StateObject var model = Model()
    var body: some Scene {
        WindowGroup("UTA · 链路预览") {
            LinkCanvasView(preview: true).environmentObject(model).frame(minWidth: 1080, minHeight: 700).preferredColorScheme(.light)
        }.defaultSize(width: 1240, height: 800)
    }
}

#endif
