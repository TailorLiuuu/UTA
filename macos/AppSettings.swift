import SwiftUI

@MainActor final class AppLanguage: ObservableObject {
    static let shared = AppLanguage()
    @Published private(set) var code: String
    var locale: Locale { Locale(identifier: code) }
    private init() { code = UserDefaults.standard.string(forKey: "UTA.language") == "en" ? "en" : "zh-Hans" }
    func select(_ value: String) {
        guard ["zh-Hans", "en"].contains(value), value != code else { return }
        UserDefaults.standard.set(value, forKey: "UTA.language")
        // AppKit and Sparkle cache bundle language. This is app-scoped, not a
        // system language change; native windows follow the choice next launch.
        UserDefaults.standard.set([value], forKey: "AppleLanguages")
        code = value
    }
}

struct AppSettingsView: View {
    @ObservedObject private var language = AppLanguage.shared
    @EnvironmentObject var updater: AppUpdate
    @Environment(\.dismiss) var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack {
                Label(L("设置"), systemImage: "gearshape").font(.title2.bold())
                Spacer()
                Button(L("完成")) { dismiss() }.keyboardShortcut(.cancelAction)
            }
            VStack(alignment: .leading, spacing: 9) {
                Text(L("语言")).font(.headline)
                Picker(L("界面语言"), selection: Binding(get: { language.code }, set: language.select)) {
                    Text("中文").tag("zh-Hans")
                    Text("English").tag("en")
                }.pickerStyle(.segmented)
                Text(L("界面立即切换；系统和更新窗口在下次启动后使用所选语言。"))
                    .font(.caption).foregroundStyle(muted)
            }
            Divider()
            VStack(alignment: .leading, spacing: 12) {
                Text(L("软件更新")).font(.headline)
                Toggle(L("自动检查更新"), isOn: Binding(get: { updater.automatic }, set: updater.setAutomatic))
                    .toggleStyle(.switch)
                Text(L("发现新版本时在首页提示，由你选择下载并安装。不会自动中断 USB 连接。"))
                    .font(.caption).foregroundStyle(muted)
                HStack {
                    Text(D(updater.message)).font(.caption).foregroundStyle(muted)
                    Spacer()
                    Button(L("检查更新")) { updater.check() }
                        .disabled(!updater.canCheck)
                }
                Text("UTA v\(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "") · build \(Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "")")
                    .font(.caption).foregroundStyle(muted)
            }
        }.padding(28).frame(width: 490)
    }
}

struct UpdateBanner: View {
    @ObservedObject private var language = AppLanguage.shared
    @EnvironmentObject var updater: AppUpdate
    var body: some View {
        if let version = updater.availableVersion {
            HStack(spacing: 14) {
                Image(systemName: "arrow.down.circle.fill").font(.title2).foregroundStyle(accent)
                VStack(alignment: .leading, spacing: 4) {
                    Text(L("发现新版本 · \(version)")).font(.system(size: 14, weight: .semibold))
                    Text(L("更新已就绪。请先断开 USB 并等待云端清理完成，再进行安装。"))
                        .font(.caption).foregroundStyle(muted)
                }
                Spacer()
                Button(L("立即更新")) { updater.check() }
                    .buttonStyle(.borderedProminent).tint(accent).disabled(updater.checking || !updater.canCheck)
            }.padding(16).background(accent.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
        }
    }
}
