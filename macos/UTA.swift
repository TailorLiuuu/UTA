import SwiftUI
import AppKit

struct Host: Codable, Identifiable, Hashable {
    var id: String
    var name: String
    var target: String
    var port: Int
    var identity: String
    var useSshConfig: Bool?
}
struct SSHCandidate: Decodable, Identifiable {
    let alias: String
    let source: String
    var id: String { alias }
}
struct SSHDiscovery: Decodable {
    let hosts: [SSHCandidate]
    let warnings: [String]
}
struct SSHEnvelope: Decodable {
    let ok: Bool
    let result: SSHDiscovery?
    let error: String?
}
struct ManualInstallPlan: Decodable {
    let source: String
    let sha256: String
    let upload: String
    let install: String
}
struct ManualInstallEnvelope: Decodable {
    let ok: Bool
    let result: ManualInstallPlan?
    let error: String?
}
struct Device: Decodable, Identifiable {
    let busid: String
    let name: String
    let manufacturer: String
    let serial: String
    let vidpid: String
    let speed: Int
    let access: USBAccess?
    var id: String { busid }
}
struct USBInterfaceOwner: Decodable {
    let number: Int
    let owner: String
    let drivers: [String]
}
struct USBPortProcess: Decodable {
    let pid: Int
    let command: String
    let user: String
    let ports: [String]
}
struct USBAccess: Decodable {
    let state: String
    let message: String
    let interfaces: [USBInterfaceOwner]
    let ports: [String]
    let processes: [USBPortProcess]
    let clients: [String]
    let checked: Double
    let errors: [String]
    let scope: String
}
struct USBAccessView: View {
    let access: USBAccess?
    var body: some View {
        if let access {
            DisclosureGroup {
                VStack(alignment: .leading, spacing: 7) {
                    ForEach(access.interfaces, id: \.number) { item in
                        Text("接口 \(item.number)：\(item.owner.isEmpty ? (item.drivers.isEmpty ? "未发现绑定驱动" : item.drivers.joined(separator: "、")) : item.owner)")
                    }
                    if !access.ports.isEmpty {
                        Text("Mac 本地串口：\(access.ports.joined(separator: "、"))")
                            .font(.system(size: 11, design: .monospaced))
                    }
                    ForEach(access.processes, id: \.pid) { process in
                        Text("占用进程：\(process.command) · PID \(process.pid) · UID \(process.user)")
                    }
                    if access.processes.isEmpty && access.errors.isEmpty && !access.ports.isEmpty {
                        Text("未发现当前权限可见的串口占用进程")
                    }
                    if !access.clients.isEmpty {
                        Text("USB 访问客户端（不代表独占）：\(access.clients.joined(separator: "、"))")
                    }
                    ForEach(access.errors, id: \.self) { Text($0).foregroundStyle(.orange) }
                    Text(access.scope)
                    Text("只读检查 · \(Date(timeIntervalSince1970: access.checked).formatted(date: .omitted, time: .standard))")
                }.padding(.top, 7).textSelection(.enabled)
            } label: {
                Label("USB 占用诊断 · \(access.message)", systemImage: "info.circle")
            }
            .font(.system(size: 11)).foregroundStyle(muted)
        }
    }
}
struct ReconnectCandidate: Decodable {
    let name: String
    let vidpid: String
    let token: String
}
struct Session: Decodable {
    let busid: String
    let name: String?
    let vidpid: String?
    let serverId: String
    let state: String
    let message: String
    let nodes: [String]
    let log: String?
    let sshLog: String?
    let remoteCleanupPending: Bool?
    let disconnectReason: String?
    let reconnectEnabled: Bool?
    let reconnectDeadline: Double?
    let reconnectIds: [String]?
    let reconnectCandidate: ReconnectCandidate?
    let reconnectRemembered: Bool?
    var isActive: Bool { ["connecting", "enumerating", "attached", "disconnecting", "waiting_reconnect"].contains(state) }
}

struct ReconnectControl: View {
    @EnvironmentObject var model: Model
    let session: Session
    @State private var identityText = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Toggle("允许设备切换模式后重新接入", isOn: Binding(
                get: { session.reconnectEnabled ?? false },
                set: { enabled in Task { await model.call(["action": "reconnect_policy", "busid": session.busid, "enabled": enabled]) } }))
                .toggleStyle(.checkbox).disabled(model.state.busy.contains(session.busid))
            if session.reconnectEnabled == true {
                Text("断开后等待 2 分钟。首次出现新模式时请你确认，之后会记住并自动接回。")
                    .font(.system(size: 11)).foregroundStyle(muted)
                if let candidate = session.reconnectCandidate, session.state == "waiting_reconnect" || session.state == "error" {
                    VStack(alignment: .leading, spacing: 8) {
                        Label("是否仍是刚才的设备？", systemImage: "questionmark.circle")
                            .font(.system(size: 13, weight: .semibold))
                        Text("同一 USB 插口出现了新标识：\(candidate.name)（\(candidate.vidpid)）。如果你刚刚切换了设备模式，请确认；如果换了另一台设备，请取消。")
                            .font(.system(size: 12))
                        Text("确认后接回 \(model.host(session.serverId)?.name ?? "原服务器")，并记住此模式。")
                            .font(.system(size: 11)).foregroundStyle(muted)
                        HStack {
                            Button("是，记住并接回") {
                                Task { await model.call(["action": "reconnect_confirm", "busid": session.busid, "token": candidate.token]) }
                            }.buttonStyle(.borderedProminent).tint(accent)
                            Button("不是，留在本机") {
                                Task { await model.call(["action": "reconnect_reject", "busid": session.busid, "token": candidate.token]) }
                            }
                        }.disabled(model.state.busy.contains(session.busid))
                    }.padding(12).background(accent.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                }
                DisclosureGroup("高级：已允许的设备标识") {
                    Text(session.reconnectRemembered == true ? "已记住：仅用于此服务器、原插口和原设备身份。" : "一般不需要手动填写，出现新模式时确认即可。")
                        .font(.system(size: 11)).foregroundStyle(muted)
                    HStack {
                        TextField("VID:PID，多个用逗号分隔", text: $identityText)
                            .textFieldStyle(.roundedBorder)
                        Button("应用") {
                            let ids = identityText.split(separator: ",").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
                            Task { await model.call(["action": "reconnect_policy", "busid": session.busid, "enabled": true, "vidpids": ids]) }
                        }.disabled(model.state.busy.contains(session.busid))
                    }
                    Button("清除已记住的模式") {
                        Task { await model.call(["action": "reconnect_forget", "busid": session.busid]) }
                    }.disabled(model.state.busy.contains(session.busid) || ((session.reconnectIds?.count ?? 0) < 2 && session.reconnectRemembered != true))
                    Text("清除后仅保留最初标识。没有序列号的设备无法区分同口替换的同型号板，请勿在等待期间换板。")
                        .font(.system(size: 11)).foregroundStyle(muted)
                }.onAppear { identityText = (session.reconnectIds ?? [session.vidpid ?? ""]).joined(separator: ", ") }
                    .onChange(of: session.reconnectIds) { _, ids in identityText = (ids ?? []).joined(separator: ", ") }
            }
            if session.state == "waiting_reconnect", let deadline = session.reconnectDeadline {
                Text("等待至 \(Date(timeIntervalSince1970: deadline).formatted(date: .omitted, time: .standard)) · 点击断开可取消")
                    .font(.system(size: 11)).foregroundStyle(.orange)
            }
        }.font(.system(size: 12))
    }
}
struct Check: Decodable {
    let state: String
    let message: String
    let checked: Double?
    let agentStatus: String?
    let details: AgentDetails?
    var installation: String { agentStatus ?? (details?.version != nil ? "installed" : "unknown") }
}
struct AgentDetails: Decodable {
    let version: String?
}
struct Snapshot: Decodable {
    let servers: [Host]
    let devices: [Device]
    let sessions: [Session]
    let checks: [String: Check]
    let scanError: String
    let home: String
    let busy: [String]
    let localAccess: LocalUSBAccess?
    static let empty = Snapshot(servers: [], devices: [], sessions: [], checks: [:],
                                scanError: "", home: "", busy: [], localAccess: nil)
}
struct LocalUSBAccess: Decodable {
    let available: Bool
    let message: String
}
struct Envelope: Decodable {
    let ok: Bool
    let result: Snapshot?
    let error: String?
}

@MainActor final class Model: ObservableObject {
    @Published var state = Snapshot.empty
    @Published var error: String?
    @Published var loading = true
    @Published var selected: String?
    private var refreshing = false

    static func execute(_ json: String) async throws -> Data {
        let resource = Bundle.main.resourceURL!
        let launcher = resource.appendingPathComponent("uta")
        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let task = Process()
                task.executableURL = launcher
                task.arguments = ["rpc", json]
                let output = Pipe()
                let errors = Pipe()
                task.standardOutput = output
                task.standardError = errors
                do {
                    try task.run()
                    // CLI output is bounded; drain stdout before waiting.
                    let data = output.fileHandleForReading.readDataToEndOfFile()
                    task.waitUntilExit()
                    if data.isEmpty {
                        let text = String(data: errors.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                        throw NSError(domain: "UTA", code: Int(task.terminationStatus),
                            userInfo: [NSLocalizedDescriptionKey: text.isEmpty ? "CLI 未返回结果" : text])
                    }
                    continuation.resume(returning: data)
                } catch { continuation.resume(throwing: error) }
            }
        }
    }

    func refresh() async {
        guard !refreshing else { return }
        refreshing = true
        defer { refreshing = false; loading = false }
        do {
            let data = try await Self.execute(#"{"action":"snapshot"}"#)
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            let envelope = try decoder.decode(Envelope.self, from: data)
            if let result = envelope.result {
                state = result
                if let selected, !result.servers.contains(where: { $0.id == selected }) {
                    self.selected = nil
                }
            } else { error = envelope.error ?? "后台服务返回错误" }
        } catch { self.error = error.localizedDescription }
    }

    @discardableResult func call(_ object: [String: Any]) async -> Bool {
        do {
            let text = String(data: try JSONSerialization.data(withJSONObject: object), encoding: .utf8)!
            let data = try await Self.execute(text)
            let response = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            guard response?["ok"] as? Bool == true else {
                error = response?["error"] as? String ?? "操作未完成"
                return false
            }
            if object["action"] as? String != "shutdown" { await refresh() }
            return true
        } catch { self.error = error.localizedDescription; return false }
    }
    func session(_ device: Device) -> Session? { state.sessions.first { $0.busid == device.busid } }
    func host(_ id: String) -> Host? { state.servers.first { $0.id == id } }
}

let accent = Color(red: 0.07, green: 0.48, blue: 0.46)
let ink = Color(red: 0.12, green: 0.18, blue: 0.23)
let muted = Color(red: 0.43, green: 0.48, blue: 0.52)
let utaMark = Bundle.main.url(forResource: "UTAMark", withExtension: "png")
    .flatMap { NSImage(contentsOf: $0) }

// Original radar geometry using the rounded, fine-line style referenced from Iconsax.
struct ConnectionRadar: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.addEllipse(in: CGRect(x: 3, y: 3, width: 18, height: 18))
        path.addArc(center: CGPoint(x: 12, y: 12), radius: 4.5,
                    startAngle: .degrees(0), endAngle: .degrees(270), clockwise: false)
        path.move(to: CGPoint(x: 12, y: 12))
        path.addLine(to: CGPoint(x: 18.4, y: 5.6))
        return path.applying(CGAffineTransform(scaleX: rect.width / 24, y: rect.height / 24))
    }
}

struct ConnectionCheckButton: View {
    @Environment(\.isEnabled) private var enabled
    @State private var hovering = false
    var action: () -> Void
    var body: some View {
        Button(action: action) {
            ConnectionRadar()
                .stroke(style: StrokeStyle(lineWidth: 1.4, lineCap: .round, lineJoin: .round))
                .frame(width: 21, height: 21)
                .foregroundStyle(enabled ? accent : muted)
                .frame(width: 32, height: 32)
                .background(accent.opacity(hovering && enabled ? 0.13 : 0.055),
                            in: RoundedRectangle(cornerRadius: 9))
                .contentShape(RoundedRectangle(cornerRadius: 9))
                .opacity(enabled ? 1 : 0.5)
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .accessibilityLabel("检查连接")
        .help("检查连接")
    }
}

struct Badge: View {
    var text: String
    var color: Color
    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(text).font(.system(size: 11, weight: .medium))
        }
        .padding(.horizontal, 9).padding(.vertical, 5)
        .background(color.opacity(0.09), in: Capsule()).foregroundStyle(color)
    }
}

struct HostEditor: View {
    @EnvironmentObject var model: Model
    @Environment(\.dismiss) var dismiss
    var host: Host?
    @State var name = ""
    @State var target = ""
    @State var port = "22"
    @State var identity = ""
    @State var useSSHConfig = false
    @State var saving = false
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                Image(systemName: "server.rack").font(.title2).foregroundStyle(accent)
                VStack(alignment: .leading, spacing: 4) {
                    Text(host == nil ? "添加服务器" : "编辑服务器").font(.title2.bold())
                    Text("连接你已有的 Linux 开发环境").foregroundStyle(muted)
                }
            }
            VStack(alignment: .leading, spacing: 14) {
                field("备注名称", hint: "例如：云端开发机 · 实验室", value: $name)
                Text("显示在侧栏和设备列表中，修改备注不会改变 SSH 别名或中断连接。")
                    .font(.system(size: 11)).foregroundStyle(muted)
                field("SSH 地址 / 别名", hint: "developer@192.0.2.10 或 dev-cloud", value: $target)
                Toggle("沿用本机 SSH 配置的端口和密钥", isOn: $useSSHConfig)
                if !useSSHConfig {
                field("SSH 端口", hint: "22", value: $port)
                VStack(alignment: .leading, spacing: 6) {
                    Text("私钥文件（可选）").font(.system(size: 12, weight: .medium))
                    HStack {
                        TextField("留空则使用 SSH Agent 和系统 SSH 配置", text: $identity)
                            .textFieldStyle(.roundedBorder)
                        Button("选择…") {
                            let panel = NSOpenPanel()
                            panel.canChooseDirectories = false
                            panel.showsHiddenFiles = true
                            if panel.runModal() == .OK { identity = panel.url?.path ?? "" }
                        }
                    }
                }
                }
            }
            Text("首次连接前，请先在终端通过 SSH 登录一次，确认服务器指纹并配置密钥登录。软件不会保存密码；跳板机等选项沿用系统 SSH 配置。")
                .font(.system(size: 12)).foregroundStyle(muted).fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button(saving ? "保存中…" : "保存服务器") {
                    saving = true
                    Task {
                        if await model.call(["action": "server_save", "server": [
                            "id": host?.id ?? "", "name": name, "target": target,
                            "port": useSSHConfig ? "22" : port, "identity": identity,
                            "use_ssh_config": useSSHConfig
                        ]]) { dismiss() }
                        saving = false
                    }
                }.buttonStyle(.borderedProminent).tint(accent)
                    .disabled(name.isEmpty || target.isEmpty || saving)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(28).frame(width: 480)
        .onAppear {
            if let host { name = host.name; target = host.target; port = String(host.port); identity = host.identity; useSSHConfig = host.useSshConfig == true }
        }
    }
    func field(_ label: String, hint: String, value: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(.system(size: 12, weight: .medium))
            TextField(hint, text: value).textFieldStyle(.roundedBorder)
        }
    }
}

struct SSHImportView: View {
    @EnvironmentObject var model: Model
    @Environment(\.dismiss) var dismiss
    @State private var hosts: [SSHCandidate] = []
    @State private var warnings: [String] = []
    @State private var chosen: Set<String> = []
    @State private var loading = true
    @State private var saving = false
    @State private var failure: String?
    func exists(_ alias: String) -> Bool {
        model.state.servers.contains { $0.target.lowercased() == alias.lowercased() }
    }
    func load() async {
        loading = true
        failure = nil
        defer { loading = false }
        do {
            let data = try await Model.execute(#"{"action":"ssh_hosts"}"#)
            let response = try JSONDecoder().decode(SSHEnvelope.self, from: data)
            guard response.ok, let result = response.result else {
                failure = response.error ?? "读取 SSH 配置失败"; return
            }
            hosts = result.hosts; warnings = result.warnings
            chosen = chosen.intersection(Set(hosts.filter { !exists($0.alias) }.map(\.alias)))
        } catch { failure = error.localizedDescription }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("从 SSH 配置导入").font(.title2.bold())
            Text("读取 ~/.ssh/config 与 Include 中的具体 Host 别名。连接时沿用端口、密钥、用户名和跳板机；导入不会发起连接。")
                .font(.system(size: 12)).foregroundStyle(muted)
            if let failure { Text(failure).foregroundStyle(.red).font(.caption) }
            if loading { ProgressView("正在读取本机配置…") }
            else if hosts.isEmpty {
                Text("没有找到可导入的 Host 别名。可手动添加服务器；Host * 等通配规则不会作为服务器列出。")
                    .foregroundStyle(muted).padding(.vertical, 24)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        ForEach(hosts) { host in
                            Toggle(isOn: Binding(get: { chosen.contains(host.alias) },
                                set: { if $0 { chosen.insert(host.alias) } else { chosen.remove(host.alias) } })) {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(host.alias + (exists(host.alias) ? " · 已添加" : "")).font(.system(size: 13, weight: .medium))
                                    Text(host.source).font(.system(size: 10)).foregroundStyle(muted)
                                }
                            }.disabled(exists(host.alias) || saving)
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
                }.frame(height: 260).background(Color.black.opacity(0.025), in: RoundedRectangle(cornerRadius: 10))
            }
            if !warnings.isEmpty {
                Text(warnings.joined(separator: "\n")).font(.caption).foregroundStyle(.orange).lineLimit(4)
            }
            HStack {
                Button("重新读取") { Task { await load() } }.disabled(loading || saving)
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction).disabled(saving)
                Button("导入所选（\(chosen.count)）") {
                    saving = true
                    Task {
                        if await model.call(["action": "server_import", "aliases": chosen.sorted()]) {
                            model.selected = model.state.servers.first { chosen.contains($0.target) }?.id
                            dismiss()
                        }
                        saving = false
                    }
                }.buttonStyle(.borderedProminent).tint(accent)
                    .disabled(chosen.isEmpty || saving || loading)
            }
        }.padding(28).frame(width: 570).task { await load() }
    }
}

struct RemotePortsView: View {
    let session: Session
    let serverName: String
    private var serialPorts: [String] {
        session.nodes.filter { $0.hasPrefix("/dev/tty") || $0.hasPrefix("/dev/serial/") }
    }
    var body: some View {
        if session.state == "attached" || session.state == "enumerating" {
            VStack(alignment: .leading, spacing: 7) {
                Text("云端设备端口 · \(serverName)").font(.system(size: 12, weight: .semibold))
                if session.state == "enumerating" {
                    Text("正在识别 USB，云端端口尚未就绪。")
                        .font(.system(size: 12)).foregroundStyle(.orange)
                } else {
                    if serialPorts.isEmpty {
                        Text("未发现云端串口；可使用下方 USB 节点，或检查服务器驱动。")
                            .font(.system(size: 11)).foregroundStyle(muted)
                    }
                    ForEach(session.nodes, id: \.self) { node in
                        HStack {
                            Text(serialPorts.contains(node) ? "串口" : "USB / 其他节点")
                                .font(.system(size: 11)).foregroundStyle(muted)
                            Text(node).font(.system(size: 12, design: .monospaced))
                                .foregroundStyle(accent).textSelection(.enabled)
                            Spacer()
                            Button("复制") {
                                NSPasteboard.general.clearContents()
                                NSPasteboard.general.setString(node, forType: .string)
                            }.buttonStyle(.borderless)
                        }
                    }
                    Text("在该服务器的终端或远程开发会话中使用这些路径。")
                        .font(.system(size: 11)).foregroundStyle(muted)
                }
            }.padding(12).background(accent.opacity(0.05), in: RoundedRectangle(cornerRadius: 8))
        }
    }
}

struct DeviceCard: View {
    @EnvironmentObject var model: Model
    let device: Device
    @State private var target = ""
    @State private var details = false
    var session: Session? { model.session(device) }
    var busy: Bool { model.state.busy.contains(device.busid) }
    var status: (String, Color) {
        switch session?.state {
        case "attached": return ("云端已挂载", accent)
        case "connecting": return ("正在连接", .orange)
        case "enumerating": return ("等待枚举", .orange)
        case "disconnecting": return ("正在释放", .orange)
        case "waiting_reconnect": return ("等待设备重连", .orange)
        case "error": return ("连接异常", .red)
        case "permission_required": return ("需要本机授权", .orange)
        default: return (session?.remoteCleanupPending == true ? "待清理" : "在本地", muted)
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            HStack(alignment: .top, spacing: 13) {
                Image(systemName: "cable.connector")
                    .font(.system(size: 23, weight: .medium)).foregroundStyle(accent)
                    .frame(width: 46, height: 46).background(accent.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
                VStack(alignment: .leading, spacing: 5) {
                    Text(device.name).font(.system(size: 15, weight: .semibold)).foregroundStyle(ink)
                    Text("\(device.vidpid)  ·  \(device.busid)")
                        .font(.system(size: 11, design: .monospaced)).foregroundStyle(muted)
                }
                Spacer()
                Badge(text: status.0, color: status.1)
            }
            HStack(spacing: 10) {
                Text("接入到").font(.system(size: 12)).foregroundStyle(muted)
                Picker("目标服务器", selection: $target) {
                    Text("选择服务器").tag("")
                    ForEach(model.state.servers) { host in Text(host.name).tag(host.id) }
                }.labelsHidden().frame(maxWidth: 260).disabled(busy)
                Spacer()
                if session != nil && session?.state != "local" || session?.remoteCleanupPending == true {
                    Button("断开") {
                        Task { await model.call(["action": "disconnect", "busid": device.busid]) }
                    }.disabled(busy)
                }
                Button(session?.state == "attached" && target != session?.serverId ? "切换接入" : "接入") {
                    Task { await model.call(["action": "connect", "busid": device.busid, "server_id": target]) }
                }
                .buttonStyle(.borderedProminent).tint(accent)
                .disabled(target.isEmpty || busy || (session?.state == "attached" && target == session?.serverId))
            }
            if let session {
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: session.state == "error" ? "exclamationmark.circle" : "info.circle")
                    Text(session.message).textSelection(.enabled)
                }
                .font(.system(size: 12))
                .foregroundStyle(session.state == "error" ? Color.red : muted)
                .fixedSize(horizontal: false, vertical: true)
                RemotePortsView(session: session, serverName: model.host(session.serverId)?.name ?? "目标服务器")
                ReconnectControl(session: session)
            }
            USBAccessView(access: device.access)
            DisclosureGroup("设备详情与日志", isExpanded: $details) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("制造商：\(device.manufacturer.isEmpty ? "未提供" : device.manufacturer)")
                    Text("序列号：\(device.serial.isEmpty ? "未提供 · 请按 USB 接口位置识别" : device.serial)")
                    if let reason = session?.disconnectReason {
                        Text("上次断开原因：\(reason)")
                    }
                    if let session, let host = model.host(session.serverId) {
                        Text("当前分配：\(host.name)  (\(host.target))")
                    }
                    HStack {
                        if let log = session?.log {
                            Button("USB 日志") { NSWorkspace.shared.open(URL(fileURLWithPath: log)) }
                        }
                        if let log = session?.sshLog {
                            Button("连接日志") { NSWorkspace.shared.open(URL(fileURLWithPath: log)) }
                        }
                    }
                }.font(.system(size: 11)).foregroundStyle(muted).padding(.top, 6).textSelection(.enabled)
            }.font(.system(size: 11)).foregroundStyle(muted)
        }
        .padding(19).background(.white, in: RoundedRectangle(cornerRadius: 15))
        .overlay(RoundedRectangle(cornerRadius: 15).stroke(Color.black.opacity(0.055)))
        .onAppear { target = session?.serverId ?? model.selected ?? "" }
        .onChange(of: model.selected) { _, id in
            if session?.state != "attached" { target = id ?? "" }
        }
        .onChange(of: model.state.servers.map(\.id)) { _, ids in
            if !ids.contains(target) { target = model.selected ?? "" }
        }
    }
}

struct ManualInstallView: View {
    @EnvironmentObject var model: Model
    @Environment(\.dismiss) private var dismiss
    let host: Host
    @State private var plan: ManualInstallPlan?
    @State private var error: String?
    @State private var copied = ""
    @State private var exported = ""
    @State private var checking = false

    func load() async {
        do {
            let request = try JSONSerialization.data(withJSONObject: ["action": "server_manual", "id": host.id])
            let data = try await Model.execute(String(decoding: request, as: UTF8.self))
            let response = try JSONDecoder().decode(ManualInstallEnvelope.self, from: data)
            plan = response.result
            if !response.ok { error = response.error ?? "无法生成安装命令" }
        } catch { self.error = error.localizedDescription }
    }
    func command(_ title: String, text: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(title).font(.system(size: 13, weight: .semibold))
                Spacer()
                Button(copied == title ? "已复制" : "复制命令") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(text, forType: .string)
                    copied = title
                }
            }
            Text(text).font(.system(size: 11, design: .monospaced))
                .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }.padding(15).background(Color.black.opacity(0.035), in: RoundedRectangle(cornerRadius: 12))
    }
    func exportScript(_ plan: ManualInstallPlan) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "uta-server.py"
        panel.title = "导出 Linux 服务端脚本"
        if panel.runModal() == .OK, let url = panel.url {
            do {
                let data = try Data(contentsOf: URL(fileURLWithPath: plan.source))
                try data.write(to: url, options: .atomic)
                exported = url.path
            } catch { self.error = error.localizedDescription }
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("手动安装云端代理").font(.title2.bold())
            Text(host.name + " · " + host.target).foregroundStyle(accent).font(.system(size: 13))
            Text("在 Mac 终端按顺序执行下面两步。命令使用这台服务器的 SSH 配置，允许你交互输入 SSH 或 sudo 密码。")
                .font(.system(size: 12)).foregroundStyle(muted)
            if let error { Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
            if let plan {
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        command("1. 上传服务端脚本", text: plan.upload)
                        command("2. 校验并安装到服务器", text: plan.install)
                        Text("安装目标：/usr/local/bin/uta-server\n命令会校验脚本 SHA-256、安装 CLI、加载虚拟 USB 模块并运行检查。两步命令需来自同一次打开的窗口。")
                            .font(.system(size: 12)).foregroundStyle(muted)
                        Divider()
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("需要管理员代装？").font(.system(size: 13, weight: .medium))
                                Text("导出脚本后，可自行上传或交给管理员安装。")
                                    .font(.system(size: 12)).foregroundStyle(muted)
                            }
                            Spacer()
                            Button("导出服务端脚本…") { exportScript(plan) }
                        }
                        if !exported.isEmpty { Text("已导出：\(exported)").font(.caption).textSelection(.enabled) }
                        Text("手动安装可以交互授权，但不能绕过管理员权限。后续从 App 接入 USB 仍需 root，或管理员为这个 CLI 配置非交互 sudo 权限；具体方法见测试指南。")
                            .font(.system(size: 12)).foregroundStyle(muted)
                        Button("打开权限配置与安装指南") {
                            if let url = Bundle.main.resourceURL?.appendingPathComponent("docs/TESTING.md") {
                                NSWorkspace.shared.open(url)
                            }
                        }
                    }
                }.frame(maxHeight: 430)
            } else if error == nil { ProgressView("正在准备命令…").frame(maxWidth: .infinity) }
            HStack {
                Text("复制和导出不会执行安装").font(.caption).foregroundStyle(muted)
                Spacer()
                Button("关闭") { dismiss() }.keyboardShortcut(.cancelAction)
                Button(checking ? "检查中…" : "安装完成，重新检查") {
                    checking = true
                    Task {
                        if await model.call(["action": "server_check", "id": host.id]) { dismiss() }
                        checking = false
                    }
                }.buttonStyle(.borderedProminent).tint(accent)
                    .disabled(checking || model.state.busy.contains(host.id))
            }
        }.padding(28).frame(width: 720).task { await load() }
    }
}

struct ContentView: View {
    @EnvironmentObject var model: Model
    @State private var editor = false
    @State private var editing: Host?
    @State private var help = false
    @State private var removing: Host?
    @State private var importing = false
    @State private var manualHost: Host?
    @State private var chosenDevices: Set<String> = []
    @State private var submitting = false
    var selected: Host? { model.selected.flatMap { model.host($0) } }
    var attached: Int { model.state.sessions.filter { $0.state == "attached" }.count }
    var capturedSessions: [Session] {
        model.state.sessions.filter { s in
            s.isActive && !model.state.devices.contains { $0.busid == s.busid }
        }
    }
    var knownDeviceCount: Int { model.state.devices.count + capturedSessions.count }

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider()
            VStack(alignment: .leading, spacing: 0) {
                header
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        if let selected { serverPanel(selected) }
                        USBPermissionView()
                        HStack {
                            Text("USB 设备").font(.system(size: 18, weight: .semibold))
                            Text("\(knownDeviceCount)").font(.system(size: 12))
                                .padding(.horizontal, 7).padding(.vertical, 3)
                                .background(Color.black.opacity(0.05), in: Capsule())
                            Spacer()
                            Text(selected == nil ? "选择设备 → 选择服务器 → 接入" : "勾选设备 → 接入当前服务器")
                                .font(.system(size: 11)).foregroundStyle(muted)
                        }
                        if let selected { deviceActions(selected) }
                        if !model.state.scanError.isEmpty {
                            Label(model.state.scanError, systemImage: "exclamationmark.triangle")
                                .font(.system(size: 12)).foregroundStyle(.red).textSelection(.enabled)
                        }
                        if model.state.devices.isEmpty && capturedSessions.isEmpty {
                            VStack(spacing: 13) {
                                Image(systemName: "cable.connector.horizontal").font(.system(size: 42)).foregroundStyle(accent.opacity(0.7))
                                Text(model.loading ? "正在读取 USB 设备…" : "插入你的开发板").font(.headline)
                                Text("设备会自动出现在这里。只有点击「接入」后，才会交给云端使用。")
                                    .font(.system(size: 12)).foregroundStyle(muted)
                            }.frame(maxWidth: .infinity).padding(.vertical, 65)
                                .background(.white, in: RoundedRectangle(cornerRadius: 15))
                        }
                        ForEach(model.state.devices) { device in
                            if let selected { serverDevice(device, host: selected) }
                            else { DeviceCard(device: device) }
                        }
                        let missing = model.state.sessions.filter { s in
                            !model.state.devices.contains { $0.busid == s.busid }
                                && (selected == nil || s.serverId == selected?.id)
                        }
                        ForEach(missing, id: \.busid) { s in
                            VStack(alignment: .leading, spacing: 12) {
                                HStack {
                                    VStack(alignment: .leading, spacing: 5) {
                                        Text(s.name ?? "USB 设备").font(.system(size: 15, weight: .semibold))
                                        Text("\(s.vidpid ?? "USB") · \(s.busid)")
                                            .font(.system(size: 11, design: .monospaced)).foregroundStyle(muted)
                                    }
                                    Spacer()
                                    Badge(text: s.state == "attached" ? "云端已挂载" :
                                            (s.state == "waiting_reconnect" ? "等待设备重连" : (s.remoteCleanupPending == true ? "正在自动清理" : (s.isActive ? "正在处理" : "已断开"))),
                                          color: s.state == "attached" ? accent : (s.isActive || s.remoteCleanupPending == true ? .orange : muted))
                                    if s.isActive {
                                        Button("断开") {
                                            Task { await model.call(["action": "disconnect", "busid": s.busid]) }
                                        }.disabled(model.state.busy.contains(s.busid))
                                    }
                                }
                                Text(s.message).font(.system(size: 12)).foregroundStyle(muted).textSelection(.enabled)
                                RemotePortsView(session: s, serverName: model.host(s.serverId)?.name ?? s.serverId)
                                ReconnectControl(session: s)
                                Text(s.state == "waiting_reconnect" ? (s.remoteCleanupPending == true ? "正在清理旧云端端口，完成后再自动接入。" : "旧云端端口已释放；设备返回后会自动创建新端口。") : s.isActive ? "设备由 UTA 接管；断开后会交还 Mac，再选择其他服务器接入。" : "本地扫描暂未发现设备；请检查连接后重新接入。")
                                    .font(.system(size: 11)).foregroundStyle(muted)
                            }.padding().background(.white, in: RoundedRectangle(cornerRadius: 12))
                        }
                    }.padding(28)
                }
                HStack(spacing: 7) {
                    Circle().fill(model.loading ? .orange : accent).frame(width: 6, height: 6)
                    Text("后台服务运行时，关闭窗口不会中断连接")
                    Spacer()
                    Text("SSH 加密 · 仅分享所选设备")
                }.font(.system(size: 10)).foregroundStyle(muted)
                    .padding(.horizontal, 28).padding(.vertical, 12)
            }.background(Color(red: 0.965, green: 0.973, blue: 0.976))
        }
        .frame(minWidth: 1000, minHeight: 660)
        .preferredColorScheme(.light)
        .sheet(isPresented: $editor) { HostEditor(host: nil).environmentObject(model) }
        .sheet(item: $editing) { host in HostEditor(host: host).environmentObject(model) }
        .sheet(item: $manualHost) { host in ManualInstallView(host: host).environmentObject(model) }
        .sheet(isPresented: $importing) { SSHImportView().environmentObject(model) }
        .sheet(isPresented: $help) { helpView }
        .task(id: selected) {
            if let selected, model.state.checks[selected.id] == nil,
               !model.state.busy.contains(selected.id) {
                await model.call(["action": "server_check", "id": selected.id])
            }
        }
        .onChange(of: model.selected) { _, _ in chosenDevices.removeAll() }
        .onChange(of: model.state.devices.map(\.busid)) { _, ids in
            chosenDevices.formIntersection(Set(ids))
        }
        .alert("操作未完成", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
            Button("知道了") { model.error = nil }
        } message: { Text(model.error ?? "") }
        .confirmationDialog("删除服务器配置？", isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } })) {
            if let removing {
                Button("删除 \(removing.name)", role: .destructive) {
                    Task {
                        if await model.call(["action": "server_remove", "id": removing.id]) { model.selected = nil }
                        self.removing = nil
                    }
                }
            }
        }
        .task {
            while !Task.isCancelled {
                await model.refresh()
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                if let utaMark {
                    Image(nsImage: utaMark).resizable().interpolation(.high)
                        .scaledToFit().frame(width: 38, height: 38).accessibilityLabel("UTA 标识")
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text("UTA").font(.system(size: 19, weight: .bold)).foregroundStyle(ink)
                    Text("USB to Air").font(.system(size: 10)).foregroundStyle(muted)
                }
            }.padding(.horizontal, 22).padding(.top, 30).padding(.bottom, 30)
            Button {
                model.selected = nil
            } label: {
                HStack {
                    Image(systemName: "square.grid.2x2")
                    Text("设备总览")
                    Spacer()
                    Text("\(knownDeviceCount)").foregroundStyle(muted)
                }.font(.system(size: 13, weight: .medium))
                    .frame(maxWidth: .infinity, alignment: .leading).padding(12)
                    .contentShape(Rectangle())
                    .background(model.selected == nil ? accent.opacity(0.09) : .clear, in: RoundedRectangle(cornerRadius: 9))
            }.buttonStyle(.plain).padding(.horizontal, 12)
            HStack {
                Text("服务器").font(.system(size: 11, weight: .semibold)).foregroundStyle(muted)
                Spacer()
                Button { editing = nil; editor = true } label: { Image(systemName: "plus") }
                    .buttonStyle(.plain).help("添加服务器")
            }.padding(.horizontal, 24).padding(.top, 26).padding(.bottom, 12)
            ScrollView {
                VStack(spacing: 5) {
                    ForEach(model.state.servers) { host in
                        Button { model.selected = host.id } label: {
                            HStack(spacing: 10) {
                                Image(systemName: "server.rack").foregroundStyle(model.selected == host.id ? accent : muted)
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(host.name).font(.system(size: 12, weight: .medium)).lineLimit(1)
                                    Text(host.target).font(.system(size: 10)).foregroundStyle(muted).lineLimit(1)
                                }
                                Spacer(minLength: 0)
                                Circle().fill(model.state.checks[host.id]?.state == "ready" ? accent : Color.gray.opacity(0.4))
                                    .frame(width: 6, height: 6)
                            }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
                                .contentShape(Rectangle())
                                .background(model.selected == host.id ? accent.opacity(0.09) : .clear, in: RoundedRectangle(cornerRadius: 9))
                        }.buttonStyle(.plain)
                    }
                }.padding(.horizontal, 12)
            }
            Button("从 SSH 配置导入", systemImage: "square.and.arrow.down") { importing = true }
                .buttonStyle(.plain).font(.system(size: 12)).foregroundStyle(accent)
                .padding(.horizontal, 22).padding(.vertical, 14)
            if model.state.servers.isEmpty {
                Text("添加一台服务器，\n开始连接你的设备。")
                    .font(.system(size: 12)).foregroundStyle(muted).lineSpacing(5).padding(.horizontal, 24)
            }
            Spacer()
            Button { help = true } label: {
                Label("连接指南与 CLI", systemImage: "questionmark.circle")
                    .font(.system(size: 12)).foregroundStyle(muted)
            }.buttonStyle(.plain).padding(24)
        }.frame(width: 235).background(.white)
    }

    var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 6) {
                Text(selected?.name ?? "设备总览").font(.system(size: 26, weight: .bold)).foregroundStyle(ink)
                Text("本地连接，远程使用。固件留在你的服务器。").font(.system(size: 12)).foregroundStyle(muted)
            }
            Spacer()
            Badge(text: "\(attached) 个已挂载", color: accent)
            Button { Task { await model.refresh() } } label: { Image(systemName: "arrow.clockwise") }
                .help("刷新状态")
            Button("添加服务器", systemImage: "plus") { editing = nil; editor = true }
        }.padding(28).background(.white.opacity(0.6))
    }

    func serverPanel(_ host: Host) -> some View {
        let check = model.state.checks[host.id]
        return VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label(host.target + (host.useSshConfig == true ? " · SSH 配置" : " : \(host.port)"), systemImage: "network")
                    .font(.system(size: 12, design: .monospaced))
                Spacer()
                Button("编辑") { editing = host }
                Button { removing = host } label: { Image(systemName: "trash") }
            }
            Text(model.state.checks[host.id]?.message ?? "尚未检查。先确认 SSH 可登录，再安装或检查云端代理。")
                .font(.system(size: 12)).foregroundStyle(muted).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            if let checked = model.state.checks[host.id]?.checked {
                Text("上次检查：\(Date(timeIntervalSince1970: checked).formatted(date: .omitted, time: .standard))")
                    .font(.system(size: 10)).foregroundStyle(muted)
            }
            HStack {
                ConnectionCheckButton {
                    Task { await model.call(["action": "server_check", "id": host.id]) }
                }
                if check?.installation == "installed" {
                    Label("云端代理已安装" + (check?.details?.version.map { " · v\($0)" } ?? ""),
                          systemImage: "checkmark.circle.fill")
                        .font(.system(size: 12)).foregroundStyle(accent)
                    Menu {
                        Button("重新安装云端代理") {
                            Task { await model.call(["action": "server_install", "id": host.id]) }
                        }
                    } label: { Image(systemName: "ellipsis") }
                        .menuStyle(.borderlessButton).frame(width: 24).help("代理维护")
                } else if check?.installation == "missing" {
                    Button("安装云端代理", systemImage: "arrow.down.to.line") {
                        Task { await model.call(["action": "server_install", "id": host.id]) }
                    }
                } else {
                    Text(check?.state == "checking" ? "正在确认安装状态…" : "安装状态待确认")
                        .font(.system(size: 12)).foregroundStyle(muted)
                }
                Spacer()
                Text(check?.installation == "installed" ? "" : "安装需要 root 或已配置的 sudo 权限")
                    .font(.system(size: 10)).foregroundStyle(muted)
            }.disabled(model.state.busy.contains(host.id))
            HStack(spacing: 10) {
                Button("手动安装", systemImage: "terminal") { manualHost = host }
                Text(check?.state == "error" ? "自动安装或权限检查失败时，可在终端交互授权。" : "在终端执行安装命令，或导出脚本交给管理员。")
                    .font(.system(size: 11)).foregroundStyle(muted)
            }
        }.padding(17).background(accent.opacity(0.045), in: RoundedRectangle(cornerRadius: 12))
    }

    func deviceActions(_ host: Host) -> some View {
        HStack {
            Text("已选 \(chosenDevices.count) 个").font(.system(size: 12)).foregroundStyle(muted)
            if !chosenDevices.isEmpty {
                Button("清空选择") { chosenDevices.removeAll() }.buttonStyle(.plain).font(.caption)
            }
            Spacer()
            Button("释放所选") { submitDevices(host, release: true) }
                .disabled(!model.state.sessions.contains { chosenDevices.contains($0.busid) && $0.serverId == host.id && ($0.state != "local" || $0.remoteCleanupPending == true) })
            Button("接入 \(host.name)", systemImage: "link") { submitDevices(host, release: false) }
                .buttonStyle(.borderedProminent).tint(accent)
        }.disabled(submitting || chosenDevices.isEmpty || chosenDevices.contains { model.state.busy.contains($0) })
    }

    func submitDevices(_ host: Host, release: Bool) {
        let ids = chosenDevices.sorted()
        submitting = true
        Task {
            for id in ids {
                let session = model.state.sessions.first { $0.busid == id }
                if release {
                    guard session?.serverId == host.id,
                          session?.state != "local" || session?.remoteCleanupPending == true else { continue }
                } else if session?.serverId == host.id && session?.state == "attached" { continue }
                let request: [String: Any] = release ?
                    ["action": "disconnect", "busid": id] :
                    ["action": "connect", "busid": id, "server_id": host.id]
                if await model.call(request) { chosenDevices.remove(id) }
                else { break }
            }
            submitting = false
        }
    }

    func serverDevice(_ device: Device, host: Host) -> some View {
        let session = model.session(device)
        let assigned = session.flatMap { model.host($0.serverId) }
        let elsewhere = assigned != nil && assigned?.id != host.id && session?.state != "local"
        let state = session?.state ?? "local"
        let label = state == "attached" ? "云端已挂载" :
            state == "connecting" ? "正在连接" :
            state == "enumerating" ? "等待枚举" :
            state == "disconnecting" ? "正在释放" :
            state == "waiting_reconnect" ? "等待设备重连" :
            state == "error" ? "连接异常" :
            state == "permission_required" ? "需要本机授权" :
            session?.remoteCleanupPending == true ? "待清理" : "在本地"
        return VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 14) {
                Toggle("选择 \(device.name)", isOn: Binding(get: { chosenDevices.contains(device.busid) },
                    set: { if $0 { chosenDevices.insert(device.busid) } else { chosenDevices.remove(device.busid) } }))
                    .labelsHidden().toggleStyle(.checkbox)
                    .disabled(submitting || model.state.busy.contains(device.busid))
                Image(systemName: "cable.connector").font(.title2).foregroundStyle(accent)
                VStack(alignment: .leading, spacing: 4) {
                    Text(device.name).font(.system(size: 14, weight: .semibold))
                    Text("\(device.vidpid) · \(device.busid)").font(.system(size: 11, design: .monospaced)).foregroundStyle(muted)
                }
                Spacer()
                Badge(text: label, color: state == "error" ? .red : state == "attached" ? accent : muted)
            }
            if elsewhere {
                Text("当前分配给 \(assigned?.name ?? "")；接入将先释放旧连接。")
                    .font(.system(size: 12)).foregroundStyle(.orange)
            } else if state == "attached" {
                Text("已接入 \(host.name)").font(.system(size: 12)).foregroundStyle(accent)
            }
            if let session {
                Text(session.message).font(.system(size: 12)).foregroundStyle(state == "error" ? .red : muted)
                    .textSelection(.enabled)
                RemotePortsView(session: session, serverName: assigned?.name ?? host.name)
                ReconnectControl(session: session)
            }
            USBAccessView(access: device.access)
        }.padding(18).background(.white, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(chosenDevices.contains(device.busid) ? accent.opacity(0.5) : Color.black.opacity(0.055)))
    }

    var helpView: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("从本地 USB，到云端开发环境").font(.title2.bold())
            Text("1. 在终端通过 SSH 登录服务器，确认指纹并配置密钥。\n\n2. 从 SSH 配置导入服务器，点击「安装云端代理」。Linux 需要 vhci_hcd；安装和挂载需要管理员权限。\n\n3. 选择侧栏服务器，勾选开发板并点击「接入」。只有显示「云端已挂载」后，再开始烧录。\n\n4. 在远程开发会话运行 sudo uta-server list 查询设备，随后使用原来的烧录工具。")
                .font(.system(size: 13)).lineSpacing(4)
            Text("设备复位或切换模式后，UTA 会释放旧端口，并等待 2 分钟自动接回原服务器。按原 USB 插口、序列号及你允许的 VID:PID 匹配；标识变化需要先配置。手动断开可取消重连。UTA 不执行或重发设备协议命令，一个设备同时只交给一台服务器使用。")
                .font(.system(size: 12)).foregroundStyle(muted)
            HStack {
                Button("打开完整测试指南") {
                    if let url = Bundle.main.resourceURL?.appendingPathComponent("docs/TESTING.md") { NSWorkspace.shared.open(url) }
                }
                Button("打开 CLI 所在目录") {
                    if let url = Bundle.main.resourceURL { NSWorkspace.shared.open(url) }
                }
                Spacer()
                Button("完成") { help = false }.keyboardShortcut(.defaultAction)
            }
        }.padding(30).frame(width: 600)
    }
}

@main struct UTAApp: App {
    @StateObject var model = Model()
    var body: some Scene {
        WindowGroup("UTA") {
            ContentView().environmentObject(model)
        }.defaultSize(width: 1080, height: 740)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("停止 UTA 后台服务") {
                    Task {
                        _ = await model.call(["action": "shutdown"])
                        NSApplication.shared.terminate(nil)
                    }
                }
            }
        }
    }
}
