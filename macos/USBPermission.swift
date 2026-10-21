import SwiftUI
import ServiceManagement

struct USBPermissionView: View {
    @EnvironmentObject var model: Model
    @State private var serviceStatus = SMAppService.daemon(plistName: usbHelperPlist).status
    @State private var confirming = false
    @State private var working = false
    @State private var issue: String?
    @State private var lastProbe = Date.distantPast
    private var service: SMAppService { SMAppService.daemon(plistName: usbHelperPlist) }
    private var installed: Bool {
        let path = Bundle.main.bundleURL.standardizedFileURL.path
        return path.hasPrefix("/Applications/") ||
            path.hasPrefix(NSHomeDirectory() + "/Applications/")
    }
    private var active: Bool {
        !model.state.busy.isEmpty ||
        model.state.sessions.contains { ["connecting", "enumerating", "attached", "disconnecting"].contains($0.state) }
    }
    private var title: String {
        switch serviceStatus {
        case .enabled:
            if working { return "已获系统批准，正在检查服务" }
            if model.state.localAccess?.available == true { return "本机 USB 接管已授权" }
            return lastProbe == .distantPast ? "已获系统批准，准备检查服务" : "已获系统批准，但 USB 服务不可用"
        case .requiresApproval: return "等待管理员批准 USB 接管"
        case .notFound:
            return FileManager.default.fileExists(atPath: Bundle.main.bundleURL
                .appendingPathComponent("Contents/Library/LaunchDaemons/" + usbHelperPlist).path) ?
                "本机 USB 接管尚未注册" : "本机 USB 接管尚未注册或组件不可用"
        case .notRegistered: return "本机 USB 接管尚未启用"
        @unknown default: return "本机授权状态未知"
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack {
                Label(title, systemImage: serviceStatus == .enabled ? "lock.shield" : "lock")
                    .font(.system(size: 12, weight: .medium))
                Spacer()
                if serviceStatus == .requiresApproval {
                    Button("前往系统设置") { SMAppService.openSystemSettingsLoginItems() }
                    Button("取消启用") { unregister() }.disabled(active)
                } else if serviceStatus == .enabled {
                    Button("重试检查") { Task { await probe() } }
                    Button("停用") { unregister() }.disabled(active)
                } else {
                    Button("启用 USB 接管") { confirming = true }.disabled(!installed)
                }
            }.disabled(working)
            Text("系统驱动占用 USB 时，需要本机辅助服务接管。界面和 SSH 使用普通权限，辅助服务仅处理你选择的设备。")
                .font(.system(size: 11)).foregroundStyle(muted)
            if !installed {
                Text("请先将 UTA.app 放入「应用程序」，再启用系统授权。")
                    .font(.system(size: 11)).foregroundStyle(.orange)
            }
            if serviceStatus == .requiresApproval {
                Text("在系统设置的「登录项与扩展」中允许 UTA，可能需要管理员密码。批准后回到这里重新点击设备接入；取消不会自动重试。")
                    .font(.system(size: 11)).foregroundStyle(.orange)
            }
            if serviceStatus == .enabled, model.state.localAccess?.available == false {
                Text(model.state.localAccess?.message ?? "正在确认服务")
                    .font(.system(size: 11)).foregroundStyle(.orange).textSelection(.enabled)
                Text("系统批准与服务启动是两个步骤。可重试检查；更新应用后仍不可用时，请先断开设备，再停用并重新启用服务。")
                    .font(.system(size: 11)).foregroundStyle(muted)
            }
            if let issue {
                Text(issue).font(.system(size: 11)).foregroundStyle(.red).textSelection(.enabled)
            }
        }.padding(14).background(accent.opacity(0.045), in: RoundedRectangle(cornerRadius: 12))
        .confirmationDialog("启用 Mac USB 接管？", isPresented: $confirming, titleVisibility: .visible) {
            Button("继续系统授权") { register() }
            Button("取消", role: .cancel) { issue = nil }
        } message: {
            Text("UTA 将注册本机管理员辅助服务，用于接管和释放所选 USB。密码由 macOS 处理，UTA 不收集密码。授权后仍需主动选择设备接入服务器，可随时在此停用。")
        }
        .task {
            while !Task.isCancelled {
                serviceStatus = service.status
                let interval: TimeInterval = model.state.localAccess?.available == true ? 30 : 60
                if serviceStatus == .enabled && Date().timeIntervalSince(lastProbe) > interval && !working {
                    await probe()
                }
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }
    private func probe() async {
        guard !working else { return }
        working = true
        lastProbe = Date()
        await model.call(["action": "permission_status"])
        serviceStatus = service.status
        working = false
    }
    private func register() {
        guard !working, !active else { return }
        issue = nil
        working = true
        Task {
          defer { working = false }
          do {
            let legacy = SMAppService.daemon(plistName: legacyUSBHelperPlist)
            if legacy.status == .enabled || legacy.status == .requiresApproval {
                try await legacy.unregister()
            }
            try service.register()
          } catch {
            // First registration may report "not approved" while successfully requesting approval.
            if service.status != .requiresApproval {
                let ns = error as NSError
                issue = "系统未能注册 USB 接管服务：\(ns.localizedDescription)（\(ns.domain) / \(ns.code)）。请检查应用位置和签名。"
            }
          }
          serviceStatus = service.status
          lastProbe = .distantPast
          if serviceStatus == .requiresApproval { SMAppService.openSystemSettingsLoginItems() }
        }
    }
    private func unregister() {
        guard !active else { return }
        working = true
        Task {
            do { try await service.unregister(); issue = nil }
            catch { issue = "停用未完成：" + error.localizedDescription }
            serviceStatus = service.status
            await model.call(["action": "permission_status"])
            working = false
        }
    }
}
