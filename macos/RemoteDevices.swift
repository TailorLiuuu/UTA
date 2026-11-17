import SwiftUI
import AppKit

struct RemoteDevice: Decodable, Identifiable {
    let id: String
    let session: String
    let state: String
    let nodes: [String]
    let busid: String
    let vidpid: String
    let access: String
    let busy: Bool
    var ready: Bool { state == "attached" }
    var serials: [String] { nodes.filter { $0.hasPrefix("/dev/tty") } }
}

@MainActor final class RemoteDeviceModel: ObservableObject {
    @Published var devices: [RemoteDevice] = []
    @Published var selected = ""
    @Published var busy = false
    @Published var message = ""
    @Published var output = ""
    @Published var clientID = ""
    @Published var grants = ""
    @Published var runID = ""
    let cli = LinkCLI()
    private var logBytes = Data()
    private var publishTask: Task<Void, Never>?
    var device: RemoteDevice? { devices.first { $0.id == selected } }
    func stop() { cli.cancel() }
    private func receive(_ raw: String) {
        guard let data = raw.data(using: .utf8), let event = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return }
        if let id = event["run_id"] as? String { runID = id }
        if let text = event["text"] as? String { logBytes.append(contentsOf: text.utf8) }
        if event["event"] as? String == "serial", let text = event["data"] as? String, let bytes = Data(base64Encoded: text) { logBytes.append(bytes) }
        if logBytes.count > 65536 { logBytes = logBytes.suffix(65536) }
        if (event["text"] != nil || event["event"] as? String == "serial"), publishTask == nil {
            publishTask = Task { [weak self] in
                try? await Task.sleep(for: .milliseconds(100))
                guard !Task.isCancelled, let self else { return }
                self.output = String(decoding: self.logBytes, as: UTF8.self)
                self.publishTask = nil
            }
        }
    }
    func perform(_ action: String, host: Host, extra: [String] = []) async {
        guard !busy else { return }
        busy = true; message = L("正在处理…")
        if action == "logs" { publishTask?.cancel(); publishTask = nil; logBytes.removeAll(); output = ""; runID = "" }
        defer { busy = false }
        do {
            let data = try await cli.run(["port", action, "--to", host.id] + extra, progress: { _ in }, event: { [weak self] line in
                Task { @MainActor in self?.receive(line) }
            })
            guard let envelope = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
            if action == "list" {
                let array = try JSONSerialization.data(withJSONObject: envelope["result"] ?? [])
                devices = try JSONDecoder().decode([RemoteDevice].self, from: array)
                if !devices.contains(where: { $0.id == selected }) { selected = "" }
                message = devices.isEmpty ? L("没有已授权的远程设备。请在提供设备的电脑上授权此客户端。") : L("设备状态已更新")
            } else if action == "identity", let result = envelope["result"] as? [String: Any], let id = result["client_id"] as? String {
                clientID = id
                NSPasteboard.general.clearContents(); NSPasteboard.general.setString(id, forType: .string)
                message = L("客户端标识已复制；将它交给提供设备的电脑授权。它不是密码。")
            } else if action == "grants" {
                let list = envelope["result"] as? [[String: Any]] ?? []
                grants = list.map { row in
                    let expires = Date(timeIntervalSince1970: row["expires"] as? Double ?? 0)
                    return "\(row["recipient"] as? String ?? "")\n\(expires.formatted())"
                }.joined(separator: "\n\n")
                message = list.isEmpty ? L("没有有效的设备授权") : L("授权列表已更新")
            } else {
                message = action == "logs" ? L("日志读取已结束，端口已释放") : L("操作完成")
            }
        } catch {
            message = error.localizedDescription
            if action == "list" { devices = []; selected = "" }
        }
    }
    func copyCommand(host: Host) {
        guard let device else { return }
        let command = "'/Applications/UTA.app/Contents/Resources/uta' port run --to '\(host.id)' --id '\(device.id)' --upload /path/to/firmware.bin --timeout 900 -- /path/to/original-flasher --port '{port}' '{upload}'"
        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(command, forType: .string)
        message = L("已复制命令模板，请按项目原烧录工具替换路径和参数。")
    }
}

struct RemoteDevicePanel: View {
    let host: Host
    @ObservedObject private var language = AppLanguage.shared
    @StateObject private var remote = RemoteDeviceModel()
    @State private var expanded = true
    @State private var recipient = ""
    @State private var baud = "115200"
    @State private var seconds = 60
    @State private var node = ""
    @State private var confirmLogs = false
    @State private var confirmRevoke = false
    @AppStorage private var savedDevice: String
    init(host: Host, model: RemoteDeviceModel? = nil) {
        self.host = host
        self._remote = StateObject(wrappedValue: model ?? RemoteDeviceModel())
        self._savedDevice = AppStorage(wrappedValue: "", "uta.remoteDevice." + host.id)
    }
    private var recipientValid: Bool { recipient.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil }
    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 12) {
                Text(L("直接使用这个服务器上的设备，无需创建链路。刷新只查询状态，不打开串口。"))
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button(L("刷新远程设备")) { Task { await remote.perform("list", host: host) } }.disabled(remote.busy)
                    Button(L("复制接收端标识")) { Task { await remote.perform("identity", host: host) } }.disabled(remote.busy)
                    if remote.busy { ProgressView().controlSize(.small); Button(L("停止")) { remote.stop() } }
                }
                if !remote.devices.isEmpty {
                    Picker(L("选择远程设备"), selection: $remote.selected) {
                        Text(L("请选择设备")).tag("")
                        ForEach(remote.devices) { d in
                            Text("\(d.vidpid.isEmpty ? "USB" : d.vidpid) · \(d.busid) · \(d.access == "owner" ? L("本机提供") : L("已授权"))").tag(d.id)
                        }
                    }.disabled(remote.busy)
                }
                if let device = remote.device {
                    HStack {
                        Text(device.busy ? L("远程操作占用中") : device.ready ? L("云端已挂载") : L("设备尚未就绪"))
                            .foregroundStyle(device.ready ? Color.teal : Color.orange)
                        Text(device.id).font(.system(size: 10, design: .monospaced)).textSelection(.enabled)
                        Spacer()
                        Button(L("复制烧录命令")) { remote.copyCommand(host: host) }.disabled(!device.ready)
                    }
                    Text(device.nodes.joined(separator: "\n")).font(.system(size: 12, design: .monospaced)).textSelection(.enabled)
                    if !device.serials.isEmpty {
                        Picker(L("日志串口"), selection: $node) {
                            Text(L("单个串口自动选择")).tag("")
                            ForEach(device.serials, id: \.self) { Text($0).tag($0) }
                        }.disabled(remote.busy)
                        HStack {
                            Text(L("波特率")); TextField("115200", text: $baud).frame(width: 100)
                            Picker(L("采样时间"), selection: $seconds) {
                                Text(L("60 秒")).tag(60); Text(L("5 分钟")).tag(300); Text(L("15 分钟")).tag(900)
                            }.frame(width: 180)
                            Button(L("查看串口日志")) { confirmLogs = true }
                                .disabled(remote.busy || device.busy || !device.ready || Int(baud) == nil || (device.serials.count > 1 && node.isEmpty))
                        }
                        Text(L("只在查看时占用串口；烧录前先停止日志。界面保留最近 64 KiB，不自动保存文件。"))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if device.access == "owner" {
                        DisclosureGroup(L("授权这台设备给其他客户端")) {
                            VStack(alignment: .leading, spacing: 8) {
                                TextField(L("粘贴接收客户端标识"), text: $recipient)
                                HStack {
                                    Button(L("授权 24 小时")) { Task { await remote.perform("share", host: host, extra: ["--id", device.id, "--recipient", recipient]) } }
                                        .disabled(remote.busy || !recipientValid)
                                    Button(L("撤销授权")) { confirmRevoke = true }
                                        .disabled(remote.busy || !recipientValid)
                                    Button(L("查看授权")) { Task { await remote.perform("grants", host: host, extra: ["--id", device.id]) } }.disabled(remote.busy)
                                }
                                Text(L("仅授权这台设备的状态、工具执行和日志读取；不会分享其他设备或允许断开 USB。撤权会停止该授权的活动操作。"))
                                    .font(.caption).foregroundStyle(.secondary)
                                if !remote.grants.isEmpty { Text(remote.grants).font(.system(size: 10, design: .monospaced)).textSelection(.enabled) }
                            }.padding(.top, 6)
                        }
                    }
                }
                if !remote.message.isEmpty { Text(remote.message).font(.caption).textSelection(.enabled) }
                if !remote.runID.isEmpty { Text("run_id: \(remote.runID)").font(.system(size: 10, design: .monospaced)).textSelection(.enabled) }
                if !remote.output.isEmpty {
                    ScrollView([.vertical, .horizontal]) {
                        Text(remote.output).font(.system(size: 11, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                    }.frame(height: 220).padding(8).background(Color.black.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
                }
            }.padding(.top, 10)
        } label: {
            Label(L("远程设备与日志"), systemImage: "terminal").font(.system(size: 14, weight: .semibold))
        }
        .padding(16).background(.white, in: RoundedRectangle(cornerRadius: 12))
        .onAppear { if remote.selected.isEmpty { remote.selected = savedDevice } }
        .onChange(of: remote.selected) { _, value in node = ""; remote.grants = ""; savedDevice = value }
        .onDisappear { remote.stop() }
        .confirmationDialog(L("撤销这台设备的授权？"), isPresented: $confirmRevoke) {
            Button(L("撤销授权"), role: .destructive) {
                guard let device = remote.device else { return }
                Task { await remote.perform("revoke", host: host, extra: ["--id", device.id, "--recipient", recipient]) }
            }
            Button(L("取消"), role: .cancel) {}
        } message: {
            Text(L("撤销会停止该客户端正在进行的日志或烧录，烧录中止可能需要恢复设备。"))
        }
        .confirmationDialog(L("开始读取远程串口？"), isPresented: $confirmLogs) {
            Button(L("开始读取")) {
                guard let device = remote.device, let value = Int(baud) else { return }
                let args = ["--id", device.id, "--baud", String(value), "--seconds", String(seconds)] + (node.isEmpty ? [] : ["--node", node])
                Task { await remote.perform("logs", host: host, extra: args) }
            }
            Button(L("取消"), role: .cancel) {}
        } message: {
            Text("\(host.name) · \(remote.device?.busid ?? "") · \(baud) baud · \(seconds) s")
            Text(L("将按所选波特率打开这个设备的串口。打开串口可能触发部分板卡复位；请先关闭其他烧录和日志工具。停止或离开页面会释放本次读取。"))
        }
    }
}
