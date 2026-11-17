import SwiftUI
import AppKit

private let usbLinkColor = Color(red: 0.46, green: 0.35, blue: 0.75)
private let linkCardSize = CGSize(width: 224, height: 184)

/// Only invoked by explicit check/send buttons. Drawing a line never starts I/O.
final class LinkCLI: @unchecked Sendable {
    private let lock = NSLock()
    private var process: Process?
    private var cancelled = false
    func cancel() { lock.lock(); cancelled = true; let task = process; lock.unlock(); if task?.isRunning == true { task?.interrupt() } }
    func run(_ arguments: [String], progress: @escaping @Sendable (String) -> Void, event: @escaping @Sendable (String) -> Void = { _ in }) async throws -> Data {
        try await withCheckedThrowingContinuation { continuation in
            self.lock.lock(); self.cancelled = false; self.lock.unlock()
            DispatchQueue.global(qos: .userInitiated).async {
                guard let launcher = Bundle.main.resourceURL?.appendingPathComponent("uta") else {
                    continuation.resume(throwing: LinkGraphError("找不到 UTA CLI")); return
                }
                let task = Process(), output = Pipe(), errors = Pipe()
                task.executableURL = launcher; task.arguments = arguments
                task.standardOutput = output; task.standardError = errors
                do {
                    try task.run(); self.lock.lock(); self.process = task; let cancelled = self.cancelled; self.lock.unlock()
                    if cancelled, task.isRunning { task.interrupt() }
                    let readers = DispatchGroup(); readers.enter()
                    DispatchQueue.global(qos: .utility).async {
                        var pending = Data()
                        while true {
                            let data = errors.fileHandleForReading.availableData
                            if data.isEmpty { break }
                            pending.append(data)
                            while let end = pending.firstIndex(of: 10) {
                                let line = pending.prefix(upTo: end); pending.removeSubrange(...end)
                                if let raw = String(data: line, encoding: .utf8) { event(raw) }
                                if let event = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
                                   let sent = event["file_bytes"] as? Int, let size = event["file_size"] as? Int {
                                    progress(String(format: "%.1f / %.1f MiB", Double(sent) / 1048576, Double(size) / 1048576))
                                }
                            }
                            if pending.count > 1024 * 1024 { pending.removeAll(); task.interrupt() }
                        }
                        readers.leave()
                    }
                    var result = Data(), oversized = false
                    while true {
                        let data = output.fileHandleForReading.readData(ofLength: 8192)
                        if data.isEmpty { break }
                        if result.count + data.count <= 2 * 1024 * 1024 { result.append(data) }
                        else { oversized = true; if task.isRunning { task.interrupt() } }
                    }
                    task.waitUntilExit(); readers.wait()
                    self.lock.lock(); self.process = nil; self.lock.unlock()
                    guard !oversized else { throw LinkGraphError("CLI 返回数据过大") }
                    guard !result.isEmpty else { throw LinkGraphError(task.terminationStatus == 0 ? "CLI 未返回结果" : "操作已结束或取消，请检查连接后重试") }
                    let object = try JSONSerialization.jsonObject(with: result) as? [String: Any]
                    guard task.terminationStatus == 0, object?["ok"] as? Bool == true else {
                        let result = object?["result"] as? [String: Any]
                        throw LinkGraphError(object?["error"] as? String ?? result?["error"] as? String ?? result?["state"] as? String ?? "操作未完成")
                    }
                    continuation.resume(returning: result)
                } catch { continuation.resume(throwing: error) }
            }
        }
    }
}

@MainActor final class LinkWorkspace: ObservableObject {
    @Published var graph = LinkGraph.example()
    @Published var selectedNode: String?
    @Published var selectedEdge: String?
    @Published var error: String?
    @Published var saveMessage = ""
    @Published var activity = ""
    @Published var busy = false
    @Published var receipt = ""
    @Published var receiptEdge: String?
    @Published var checked: [String: (serverID: String, text: String)] = [:]
    private var undoStack: [LinkGraph] = [], redoStack: [LinkGraph] = []
    private var saveTask: Task<Void, Never>?
    private var blockedSave = false
    private let storage: URL?
    let preview: Bool
    let cli = LinkCLI()
    var canUndo: Bool { !undoStack.isEmpty }
    var canRedo: Bool { !redoStack.isEmpty }
    var issue: String? { do { try graph.validate(); return nil } catch { return error.localizedDescription } }

    init(preview: Bool = false, storageOverride: URL? = nil) {
        self.preview = preview
        storage = preview ? nil : (storageOverride ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/UTA/links.json"))
        guard let storage, FileManager.default.fileExists(atPath: storage.path) else { return }
        do {
            guard try storage.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink != true else { throw LinkGraphError("链路文件不能是符号链接") }
            graph = try LinkGraph.read(storage)
        } catch { self.error = error.localizedDescription; blockedSave = true; saveMessage = "原配置无法读取，已保留；请先导出新方案" }
    }
    func change(_ edit: (inout LinkGraph) -> Void) {
        let old = graph; edit(&graph)
        guard graph != old else { return }
        undoStack.append(old); if undoStack.count > 40 { undoStack.removeFirst() }; redoStack = []
        receipt = ""; persist()
    }
    func finishDrag(_ old: LinkGraph) {
        guard graph != old else { return }
        undoStack.append(old); if undoStack.count > 40 { undoStack.removeFirst() }; redoStack = []; persist()
    }
    func undo() { guard let old = undoStack.popLast() else { return }; redoStack.append(graph); graph = old; selectedNode = nil; selectedEdge = nil; persist() }
    func redo() { guard let next = redoStack.popLast() else { return }; undoStack.append(graph); graph = next; selectedNode = nil; selectedEdge = nil; persist() }
    func persist() {
        saveTask?.cancel()
        guard !blockedSave else { return }
        if let issue { saveMessage = issue; return }
        saveMessage = preview ? "预览：不写入现有配置" : "正在保存…"
        guard let storage else { return }
        let snapshot = graph
        saveTask = Task {
            do {
                try await Task.sleep(for: .milliseconds(350))
                let parent = storage.deletingLastPathComponent()
                try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                guard try parent.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink != true else { throw LinkGraphError("链路目录不能是符号链接") }
                if FileManager.default.fileExists(atPath: storage.path), try storage.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink == true { throw LinkGraphError("链路文件不能是符号链接") }
                let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
                try encoder.encode(snapshot).write(to: storage, options: .atomic)
                try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: storage.path)
                saveMessage = "已保存到本机"
            } catch is CancellationError {} catch { self.error = error.localizedDescription; saveMessage = "保存失败" }
        }
    }
    func connect(_ source: String, _ target: String, _ kind: String) {
        if let issue = graph.connectionError(source: source, target: target, kind: kind) { error = issue; return }
        change { $0.edges.append(LinkEdge(source: source, target: target, kind: kind)) }
    }
    func importFile() {
        let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let value = try LinkGraph.read(url)
            change { $0 = value }; selectedNode = nil; selectedEdge = nil; checked = [:]
        } catch { self.error = error.localizedDescription }
    }
    func exportFile() {
        do {
            try graph.validate(); let panel = NSSavePanel(); panel.nameFieldStringValue = "uta-links.json"
            guard panel.runModal() == .OK, let url = panel.url else { return }
            let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            // Share topology only; receiver bindings, device IDs and local paths stay private.
            let portable = graph.portableCopy()
            try encoder.encode(portable).write(to: url, options: .atomic)
        } catch { self.error = error.localizedDescription }
    }
    func options(node: LinkNode) -> [String] {
        var args = ["--to", node.serverID, "--workspace", graph.workspace, "--remote-platform", node.platform == "windows" ? "windows" : "posix"]
        if !node.receiverCLI.isEmpty { args += ["--remote-cli", node.receiverCLI] }
        return args
    }
    func check(_ node: LinkNode, host: Host) async {
        guard !busy, !preview else { return }; busy = true; activity = "检查接收端…"
        defer { busy = false; activity = "" }
        do {
            guard node.platform != "windows" || !node.receiverCLI.isEmpty else { throw LinkGraphError("请填写 Windows 接收端 uta-server.cmd 的完整路径") }
            let data = try await cli.run(["transfers", "check"] + options(node: node)) { _ in }
            let result = (try JSONSerialization.jsonObject(with: data) as? [String: Any])?["result"] as? [String: Any]
            guard result?["protocol"] as? Int == 1 else { throw LinkGraphError("接收端需要更新 UTA CLI") }
            let platform = result?["platform"] as? String
            guard platform == ["windows": "win32", "macos": "darwin", "linux": "linux"][node.platform] else { throw LinkGraphError("节点系统与接收端不一致，请修改节点系统后重试") }
            checked[node.id] = (host.id, "文件接收可用")
        } catch { checked[node.id] = (host.id, "检查未通过"); self.error = error.localizedDescription }
    }
    func send(_ edge: LinkEdge, host: Host) async {
        guard !busy, !preview, graph.directUpload(edge), let node = graph.node(edge.target), node.serverID == host.id else { return }
        do { try graph.validate() } catch { self.error = error.localizedDescription; return }
        guard !graph.artifact.isEmpty else { error = "请先选择本机固件文件"; return }
        guard node.platform != "windows" || !node.receiverCLI.isEmpty else { error = "请填写 Windows 接收端 uta-server.cmd 的完整路径"; return }
        busy = true; activity = "正在传输并校验…"; receipt = ""; receiptEdge = edge.id
        defer { busy = false; activity = "" }
        do {
            let data = try await cli.run(["send", graph.artifact] + options(node: node)) { [weak self] value in
                Task { @MainActor in self?.activity = value }
            }
            let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            let value = object?["result"] as? [String: Any]
            receipt = "\(value?["entry"] as? String ?? "")\n\(value?["id"] as? String ?? "")"
            checked[node.id] = (host.id, "文件接收可用")
        } catch { self.error = error.localizedDescription }
    }
}

struct LinkCanvasView: View {
    @EnvironmentObject var model: Model
    @ObservedObject private var language = AppLanguage.shared
    @StateObject private var store: LinkWorkspace
    @State private var zoom = 0.75
    @State private var canvasWidth: CGFloat = 900
    @State private var inspectorShown = false
    @State private var connecting: (source: String, kind: String)?
    @State private var pointer: CGPoint?
    @State private var dragStart: LinkGraph?
    @State private var confirmReset = false
    @State private var linkKind = "artifact"
    @State private var linkTarget = ""
    @State private var showPlan = false
    init(preview: Bool = false, workspace: LinkWorkspace? = nil) { _store = StateObject(wrappedValue: workspace ?? LinkWorkspace(preview: preview)) }
    private var extent: CGSize {
        CGSize(width: max(1080, (store.graph.nodes.map(\.x).max() ?? 0) + 170), height: max(680, (store.graph.nodes.map(\.y).max() ?? 0) + 180))
    }
    private func displayName(_ node: LinkNode) -> String {
        node.name
    }
    private func color(_ kind: String) -> Color { kind == "usb" ? usbLinkColor : accent }
    private func port(_ node: LinkNode, _ kind: String, input: Bool) -> CGPoint {
        CGPoint(x: node.x + (input ? -112 : 112), y: node.y + (kind == "usb" ? 65 : 27))
    }
    private func curve(_ from: CGPoint, _ to: CGPoint) -> Path {
        if from.x > to.x {
            let right = from.x + 50, left = to.x - 50, bottom = max(from.y, to.y) + 110
            return Path { path in
                path.move(to: from); path.addLine(to: CGPoint(x: right - 15, y: from.y))
                path.addQuadCurve(to: CGPoint(x: right, y: from.y + 15), control: CGPoint(x: right, y: from.y))
                path.addLine(to: CGPoint(x: right, y: bottom - 15))
                path.addQuadCurve(to: CGPoint(x: right - 15, y: bottom), control: CGPoint(x: right, y: bottom))
                path.addLine(to: CGPoint(x: left + 15, y: bottom))
                path.addQuadCurve(to: CGPoint(x: left, y: bottom - 15), control: CGPoint(x: left, y: bottom))
                path.addLine(to: CGPoint(x: left, y: to.y + 15))
                path.addQuadCurve(to: CGPoint(x: left + 15, y: to.y), control: CGPoint(x: left, y: to.y))
                path.addLine(to: to)
            }
        }
        let offset = max(80, abs(to.x - from.x) * 0.45)
        return Path { path in
            path.move(to: from)
            path.addCurve(to: to, control1: CGPoint(x: from.x + offset, y: from.y), control2: CGPoint(x: to.x - offset, y: to.y))
        }
    }
    var body: some View {
        VStack(spacing: 0) {
            toolbar
            HStack(spacing: 0) {
                VStack(spacing: 0) {
                    HStack(spacing: 16) {
                        legend("固件传输", "artifact"); legend("USB 提供 → 使用", "usb")
                        Spacer()
                        Text(L("拖动节点标题；从右侧圆点拖到目标左侧圆点")).font(.caption2).foregroundStyle(muted)
                    }.padding(16).background(.white.opacity(0.65))
                    GeometryReader { geometry in
                    ScrollView([.horizontal, .vertical]) {
                        ZStack(alignment: .topLeading) {
                            grid
                            ForEach(store.graph.edges) { edge in edgeView(edge) }
                            if let connecting, let source = store.graph.node(connecting.source), let pointer {
                                curve(port(source, connecting.kind, input: false), pointer)
                                    .stroke(color(connecting.kind), style: StrokeStyle(lineWidth: 2, dash: [5, 4])).allowsHitTesting(false)
                            }
                            ForEach(store.graph.nodes) { node in nodeView(node).position(x: node.x, y: node.y) }
                        }.frame(width: extent.width, height: extent.height).coordinateSpace(name: "uta-links")
                            .scaleEffect(zoom, anchor: .topLeading)
                            .frame(width: extent.width * zoom, height: extent.height * zoom, alignment: .topLeading)
                    }.background(Color(red: 0.955, green: 0.967, blue: 0.971))
                        .onAppear { canvasWidth = geometry.size.width; fit() }
                        .onChange(of: geometry.size.width) { _, width in canvasWidth = width }
                    }
                    HStack {
                        Text(D(store.issue ?? store.saveMessage)).lineLimit(1)
                        Spacer()
                        Button { zoom = max(0.5, zoom - 0.1) } label: { Image(systemName: "minus.magnifyingglass") }
                        Text("\(Int(zoom * 100))%").monospacedDigit().frame(width: 40)
                        Button { zoom = min(1.5, zoom + 0.1) } label: { Image(systemName: "plus.magnifyingglass") }
                        Button(L("适应")) { fit() }
                    }.font(.caption).foregroundStyle(muted).padding(12)
                }
                if inspectorShown {
                    Divider()
                    VStack(spacing: 0) {
                        HStack { Spacer(); Button { inspectorShown = false } label: { Image(systemName: "sidebar.right") }.help(L("收起详情")) }.padding(10)
                        inspector
                    }.frame(width: 270).background(.white)
                }
            }
            if store.busy {
                HStack { ProgressView().controlSize(.small); Text(D(store.activity)); Spacer(); Button(L("取消传输")) { store.cli.cancel() } }
                    .font(.caption).padding(12).background(accent.opacity(0.08))
            }
        }.foregroundStyle(ink)
            .alert(L("操作未完成"), isPresented: Binding(get: { store.error != nil }, set: { if !$0 { store.error = nil } })) {
                Button(L("知道了")) { store.error = nil }
            } message: { Text(D(store.error ?? "")) }
            .confirmationDialog(L("重置为示例链路？"), isPresented: $confirmReset) {
                Button(L("重置画布")) { store.change { $0 = .example() }; store.selectedNode = nil; store.selectedEdge = nil }
            } message: { Text(L("仅修改图示，不会断开任何 USB 或修改服务器。可撤销。")) }
            .sheet(isPresented: $showPlan) { planView }
            .onDisappear { connecting = nil; pointer = nil }
            .onChange(of: store.selectedNode) { _, id in if id != nil && dragStart == nil { inspectorShown = true } }
            .onChange(of: store.selectedEdge) { _, id in if id != nil { inspectorShown = true } }
    }
    private func fit() { zoom = min(1, max(0.5, (canvasWidth - 24) / extent.width)) }
    private var toolbar: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 5) {
                Text(L("链路工作台")).font(.system(size: 25, weight: .bold))
                Text(L("固件去哪里，USB 从哪里来，烧录在哪里运行。")).font(.caption).foregroundStyle(muted)
            }
            Spacer()
            Button { store.selectedNode = nil; store.selectedEdge = nil; inspectorShown.toggle() } label: { Image(systemName: "slider.horizontal.3") }.help(L("链路设置"))
            Button { store.undo() } label: { Image(systemName: "arrow.uturn.backward") }.disabled(!store.canUndo).help(L("撤销"))
            Button { store.redo() } label: { Image(systemName: "arrow.uturn.forward") }.disabled(!store.canRedo).help(L("重做"))
            Menu {
                if !store.graph.nodes.contains(where: \.isLocal) { Button(L("本机 Mac")) { addNode(nil, local: true) } }
                Button(L("未绑定的电脑")) { addNode(nil) }
                ForEach(model.state.servers) { host in Button(host.name) { addNode(host) } }
            } label: { Label(L("添加节点"), systemImage: "plus") }.disabled(store.graph.nodes.count >= 32)
            Menu {
                Button(L("导入链路…")) { store.importFile() }
                Button(L("导出链路…")) { store.exportFile() }
                Button(L("重置画布")) { confirmReset = true }
            } label: { Image(systemName: "ellipsis.circle") }
            Button(L("查看执行计划")) { showPlan = true }.buttonStyle(.borderedProminent).tint(accent)
        }.padding(22).background(.white).disabled(store.busy)
    }
    private func addNode(_ host: Host?, local: Bool = false) {
        guard store.graph.nodes.count < 32 else { return }
        var position = CGPoint(x: 160, y: 160)
        for index in 0..<48 {
            let x = Double(160 + (index % 6) * 275), y = Double(160 + (index / 6) * 245)
            let occupied = store.graph.nodes.contains { abs($0.x - x) < 245 && abs($0.y - y) < 210 }
            if !occupied { position = CGPoint(x: x, y: y); break }
        }
        let node = LinkNode(name: local ? "Mac" : (host?.name ?? L("新节点")), platform: local ? "local" : "linux", role: local ? "build" : "relay", serverID: host?.id ?? "", x: position.x, y: position.y)
        store.change { $0.nodes.append(node) }; store.selectedNode = node.id; store.selectedEdge = nil
    }
    private func legend(_ label: UIString, _ kind: String) -> some View {
        HStack(spacing: 6) { Capsule().fill(color(kind)).frame(width: 20, height: 3); Text(L(label)).font(.caption) }
    }
    private var grid: some View {
        Canvas { context, size in
            for x in stride(from: 20.0, to: size.width, by: 24) {
                for y in stride(from: 20.0, to: size.height, by: 24) {
                    context.fill(Path(ellipseIn: CGRect(x: x, y: y, width: 1.5, height: 1.5)), with: .color(.gray.opacity(0.2)))
                }
            }
        }.contentShape(Rectangle()).onTapGesture { store.selectedNode = nil; store.selectedEdge = nil; connecting = nil; pointer = nil }
    }
    private func edgeView(_ edge: LinkEdge) -> some View {
        Group {
            if let from = store.graph.node(edge.source), let to = store.graph.node(edge.target) {
                let a = port(from, edge.kind, input: false), b = port(to, edge.kind, input: true)
                let selected = store.selectedEdge == edge.id
                let path = curve(a, b)
                path.stroke(color(edge.kind).opacity(selected ? 1 : 0.55), style: StrokeStyle(lineWidth: selected ? 3 : 2, dash: edge.kind == "usb" ? [7, 4] : []))
                    .overlay(path.stroke(.clear, lineWidth: 18).contentShape(path.strokedPath(StrokeStyle(lineWidth: 18)))
                        .onTapGesture { store.selectedEdge = edge.id; store.selectedNode = nil; inspectorShown = true })
                Image(systemName: "arrowtriangle.right.fill").font(.system(size: 9)).foregroundStyle(color(edge.kind))
                    .position(x: b.x - 8, y: b.y).allowsHitTesting(false)
                Button { store.selectedEdge = edge.id; store.selectedNode = nil; inspectorShown = true } label: {
                    Text(edge.kind == "usb" ? L("USB · 待确认") : L("固件 · 已规划"))
                        .font(.system(size: 11, weight: .medium)).foregroundStyle(color(edge.kind))
                        .padding(.horizontal, 9).padding(.vertical, 5)
                        .background(.white, in: Capsule()).overlay(Capsule().stroke(color(edge.kind).opacity(0.3)))
                }.buttonStyle(.plain).position(x: (a.x + b.x) / 2, y: a.x > b.x ? max(a.y, b.y) + 93 : (a.y + b.y) / 2 - 17)
            }
        }
    }
    private func nodeView(_ node: LinkNode) -> some View {
        VStack(spacing: 0) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: node.isLocal || node.platform == "macos" ? "laptopcomputer" : node.platform == "windows" ? "desktopcomputer" : "server.rack")
                    .font(.system(size: 22)).foregroundStyle(node.role == "usb" ? usbLinkColor : accent)
                    .frame(width: 36, height: 38).background(Color.gray.opacity(0.05), in: RoundedRectangle(cornerRadius: 9))
                VStack(alignment: .leading, spacing: 5) {
                    Text(displayName(node)).font(.system(size: 14, weight: .semibold)).lineLimit(1)
                    Text(roleName(node.role)).font(.system(size: 11)).foregroundStyle(muted)
                }
                Spacer(minLength: 0)
            }.padding(15).frame(height: 80).contentShape(Rectangle())
                .gesture(DragGesture(minimumDistance: 3, coordinateSpace: .named("uta-links"))
                    .onChanged { value in
                        guard !store.busy else { return }
                        if dragStart == nil { dragStart = store.graph }
                        guard let initial = dragStart?.node(node.id), let index = store.graph.nodes.firstIndex(where: { $0.id == node.id }) else { return }
                        store.graph.nodes[index].x = min(4000, max(120, initial.x + value.translation.width))
                        store.graph.nodes[index].y = min(3000, max(110, initial.y + value.translation.height))
                        store.selectedNode = node.id; store.selectedEdge = nil
                    }.onEnded { _ in if let dragStart { store.finishDrag(dragStart) }; dragStart = nil })
            HStack(spacing: 5) {
                Circle().fill(node.isLocal ? accent : .gray.opacity(0.45)).frame(width: 5, height: 5)
                Text(nodeStatus(node)).font(.system(size: 10)).foregroundStyle(muted).lineLimit(1)
                Spacer()
            }.padding(.horizontal, 15).frame(height: 20)
            portRow(node, "artifact").frame(height: 38)
            portRow(node, "usb").frame(height: 38)
            Spacer(minLength: 8)
        }.frame(width: linkCardSize.width, height: linkCardSize.height)
            .background(.white, in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(store.selectedNode == node.id ? accent : Color.gray.opacity(0.2), lineWidth: store.selectedNode == node.id ? 2 : 1))
            .shadow(color: .black.opacity(0.04), radius: 9, y: 3)
            .onTapGesture { store.selectedNode = node.id; store.selectedEdge = nil; inspectorShown = true }
    }
    private func portRow(_ node: LinkNode, _ kind: String) -> some View {
        HStack(spacing: 7) {
            Circle().fill(color(kind).opacity(0.15)).overlay(Circle().stroke(color(kind), lineWidth: 2))
                .frame(width: 12, height: 12).offset(x: -6)
                .onTapGesture { if let connecting, connecting.kind == kind { store.connect(connecting.source, node.id, kind); self.connecting = nil; pointer = nil } }
                .accessibilityLabel(L("接入端点"))
            Text(kind == "usb" ? L("使用 USB") : L("接收固件")).foregroundStyle(muted)
            Spacer()
            Text(kind == "usb" ? L("提供 USB") : L("发送固件")).foregroundStyle(color(kind))
            Circle().fill(color(kind)).frame(width: 12, height: 12).offset(x: 6)
                .contentShape(Circle().inset(by: -8))
                .gesture(DragGesture(minimumDistance: 2, coordinateSpace: .named("uta-links"))
                    .onChanged { value in guard !store.busy else { return }; connecting = (node.id, kind); pointer = value.location }
                    .onEnded { value in
                        defer { connecting = nil; pointer = nil }
                        guard !store.busy else { return }
                        let target = store.graph.nodes.first { candidate in
                            let point = port(candidate, kind, input: true)
                            return hypot(point.x - value.location.x, point.y - value.location.y) < 30
                        }
                        if let target { store.connect(node.id, target.id, kind) }
                    })
                .onTapGesture { connecting = (node.id, kind); pointer = port(node, kind, input: false) }
                .accessibilityLabel(L("输出端点"))
        }.font(.system(size: 10)).background(color(kind).opacity(0.025))
    }
    private func roleName(_ role: String) -> String {
        switch role { case "build": return L("编译 / 固件来源"); case "execute": return L("运行烧录工具"); case "usb": return L("USB 所在电脑"); default: return L("固件中继") }
    }
    private func nodeStatus(_ node: LinkNode) -> String {
        if node.isLocal { return L("本机 · 不需要 USB 接管") }
        guard model.host(node.serverID) != nil else { return L("待绑定服务器配置") }
        if let checked = store.checked[node.id], checked.serverID == node.serverID { return D(checked.text) }
        return L("已配置 · 尚未检查")
    }
    private var inspector: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if let id = store.selectedNode, let node = store.graph.node(id) { nodeInspector(node) }
                else if let id = store.selectedEdge, let edge = store.graph.edges.first(where: { $0.id == id }) { edgeInspector(edge) }
                else { overviewInspector }
            }.padding(20).frame(maxWidth: .infinity, alignment: .leading)
        }.disabled(store.busy)
    }
    private func field(_ id: String, _ path: WritableKeyPath<LinkNode, String>) -> Binding<String> {
        Binding(get: { store.graph.node(id)?[keyPath: path] ?? "" }, set: { value in
            store.change { graph in
                if let i = graph.nodes.firstIndex(where: { $0.id == id }) {
                    let old = graph.nodes[i]
                    if path == \.serverID, let host = model.host(value),
                       ["Linux", "Windows", "Relay", L("新节点"), model.host(old.serverID)?.name ?? ""].contains(old.name) {
                        graph.nodes[i].name = host.name
                    }
                    graph.nodes[i][keyPath: path] = value
                }
            }
        })
    }
    private func nodeInspector(_ node: LinkNode) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Label(L("节点配置"), systemImage: "slider.horizontal.3").font(.headline)
            TextField(L("节点备注"), text: field(node.id, \.name)).textFieldStyle(.roundedBorder)
            if !node.isLocal {
                Picker(L("服务器"), selection: field(node.id, \.serverID)) {
                    Text(L("请选择已保存的服务器")).tag("")
                    if !node.serverID.isEmpty && model.host(node.serverID) == nil { Text(L("原服务器已移除")).tag(node.serverID) }
                    ForEach(model.state.servers) { Text($0.name).tag($0.id) }
                }
                Picker(L("系统"), selection: field(node.id, \.platform)) {
                    Text("Linux").tag("linux"); Text("macOS").tag("macos"); Text("Windows").tag("windows")
                }
                TextField(node.platform == "windows" ? "C:/path/uta-server.cmd" : "/usr/local/bin/uta-server", text: field(node.id, \.receiverCLI))
                    .textFieldStyle(.roundedBorder).help(L("接收端 CLI 路径；Linux 和 Mac 留空使用默认值"))
            }
            Picker(L("用途"), selection: field(node.id, \.role)) {
                Text(L("编译 / 固件来源")).tag("build"); Text(L("固件中继")).tag("relay")
                Text(L("运行烧录工具")).tag("execute"); Text(L("USB 所在电脑")).tag("usb")
            }
            if node.role == "usb" {
                TextField(L("USB 标识（如 6-3）"), text: field(node.id, \.usbBusID)).textFieldStyle(.roundedBorder)
                note("这是设备备注，不会仅凭标识接管端口。请在 USB 所在电脑的 UTA 确认实际接入。")
            }
            if let host = model.host(node.serverID), !node.isLocal {
                Text(host.target).font(.caption.monospaced()).textSelection(.enabled)
                Button(L("检查文件接收能力")) { Task { await store.check(node, host: host) } }.disabled(store.preview)
                Text(nodeStatus(node)).font(.caption).foregroundStyle(muted)
            }
            Divider()
            Text(L("添加连线")).font(.subheadline.bold())
            Picker(L("类型"), selection: $linkKind) { Text(L("固件传输")).tag("artifact"); Text("USB").tag("usb") }
            Picker(L("目标"), selection: $linkTarget) {
                Text(L("选择目标节点")).tag("")
                ForEach(store.graph.nodes.filter { $0.id != node.id }) { Text(displayName($0)).tag($0.id) }
            }
            Button(L("连接到目标")) { store.connect(node.id, linkTarget, linkKind) }.disabled(linkTarget.isEmpty)
            Divider()
            Button(L("从图中移除节点"), role: .destructive) { store.change { $0.removeNode(node.id) }; store.selectedNode = nil }
            note("只修改图示，不会断开现有 USB 会话。")
        }
    }
    private func edgeInspector(_ edge: LinkEdge) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Label(edge.kind == "usb" ? L("USB 链路") : L("固件链路"), systemImage: "point.3.connected.trianglepath.dotted").font(.headline)
            Text("\(store.graph.node(edge.source).map(displayName) ?? "") → \(store.graph.node(edge.target).map(displayName) ?? "")").font(.subheadline.bold())
            if edge.kind == "usb" {
                note("箭头从插着设备的电脑指向运行工具的 Linux。固件无需传到 USB 所在电脑。")
                Label(L("图示规划 · 未验证 USB 会话"), systemImage: "clock").font(.caption).foregroundStyle(usbLinkColor)
                note("请在 USB 所在电脑的 UTA 接入目标服务器，并在目标服务器确认自己的设备端口后烧录。")
            } else {
                artifactPicker
                if store.graph.directUpload(edge), let node = store.graph.node(edge.target), let host = model.host(node.serverID) {
                    Button(L("发送固件并校验")) { Task { await store.send(edge, host: host) } }
                        .buttonStyle(.borderedProminent).tint(accent)
                        .disabled(store.preview || store.graph.artifact.isEmpty || store.issue != nil || (node.platform == "windows" && node.receiverCLI.isEmpty))
                    note("只发送文件，不调用烧录工具、不操作 USB。")
                    Button(L("复制传输命令")) { copyCommand(edge) }
                } else if store.graph.directUpload(edge) {
                    note("请先选择目标节点，将它绑定到已保存的服务器配置。")
                } else {
                    note("远端中继链路已规划。需在源节点配置目标 UTA，再从该节点发送；当前不自动执行远端多跳任务。")
                }
                Button(L("在此连线中插入中继")) {
                    do { var value = store.graph; let id = try value.insertRelay(on: edge.id); store.change { $0 = value }; store.selectedNode = id; store.selectedEdge = nil }
                    catch { store.error = error.localizedDescription }
                }
            }
            if !store.receipt.isEmpty && store.receiptEdge == edge.id {
                Label(L("文件已校验提交"), systemImage: "checkmark.circle").foregroundStyle(accent)
                Text(store.receipt).font(.caption.monospaced()).textSelection(.enabled)
            }
            Divider()
            Button(L("删除这条连线"), role: .destructive) { store.change { $0.edges.removeAll { $0.id == edge.id } }; store.selectedEdge = nil }
            note("删除连线只改图示，不会释放真实端口。")
        }
    }
    private var artifactPicker: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("本机固件 / 文件夹")).font(.caption.bold())
            Text(store.graph.artifact.isEmpty ? L("尚未选择") : store.graph.artifact).font(.caption).lineLimit(3).textSelection(.enabled)
            Button(L("选择文件…")) {
                let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = true; panel.allowsMultipleSelection = false
                if panel.runModal() == .OK, let url = panel.url { store.change { $0.artifact = url.path } }
            }
        }
    }
    private var overviewInspector: some View {
        VStack(alignment: .leading, spacing: 17) {
            Label(L("链路说明"), systemImage: "point.3.filled.connected.trianglepath.dotted").font(.headline)
            note("Mac 生成固件并传到 Linux；Windows 提供 USB，Linux 使用该端口运行原来的烧录工具。")
            Divider()
            Text(L("项目标识")).font(.caption.bold())
            TextField("project-a", text: Binding(get: { store.graph.workspace }, set: { value in store.change { $0.workspace = value } })).textFieldStyle(.roundedBorder)
            note("沿用本机客户端身份，文件按客户端和项目分别存放。")
            artifactPicker
            Divider()
            Label(L("规划不等于已连接"), systemImage: "info.circle").font(.subheadline.bold())
            note("拖动、连线、导入都不会建立 SSH 或接管 USB。只有点击检查或发送，才会访问对应服务器。")
            note("导出会移除服务器绑定、文件路径与 USB 标识；节点备注仍会保留。")
        }
    }
    private func note(_ text: UIString) -> some View { Text(L(text)).font(.caption).foregroundStyle(muted).fixedSize(horizontal: false, vertical: true) }
    private func copyCommand(_ edge: LinkEdge) {
        guard let target = store.graph.node(edge.target), store.graph.directUpload(edge), !target.serverID.isEmpty else { return }
        func quote(_ value: String) -> String { "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'" }
        let command = ["uta", "send", store.graph.artifact.isEmpty ? "firmware.bin" : store.graph.artifact] + store.options(node: target)
        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(command.map(quote).joined(separator: " "), forType: .string)
    }
    private var planView: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack { Text(L("执行计划")).font(.title2.bold()); Spacer(); Button(L("完成")) { showPlan = false } }
            Text(L("以下是配置计划；不会自动执行编译、烧录或接管 USB。")).font(.caption).foregroundStyle(muted)
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let issue = store.issue { Label(D(issue), systemImage: "exclamationmark.triangle").foregroundStyle(.orange) }
                    ForEach(store.graph.nodes.filter { !$0.isLocal && model.host($0.serverID) == nil }) { node in
                        Label(L("待绑定服务器：\(displayName(node))"), systemImage: "exclamationmark.circle").font(.caption).foregroundStyle(.orange)
                    }
                    ForEach(Array(store.graph.artifactOrder().enumerated()), id: \.element.id) { index, edge in
                        VStack(alignment: .leading, spacing: 5) {
                            Text("\(index + 1). \(store.graph.node(edge.source).map(displayName) ?? "") → \(store.graph.node(edge.target).map(displayName) ?? "")").font(.headline)
                            Text(store.graph.directUpload(edge) ? L("固件传输：绑定目标并选择文件后，可在连线详情中发送。") : L("远端中继：需要源节点自己的目标配置；暂不自动执行。"))
                                .font(.caption).foregroundStyle(muted)
                        }
                    }
                    Divider()
                    ForEach(store.graph.nodes.filter { $0.role == "execute" }) { node in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(L("烧录执行位置：\(displayName(node))")).font(.headline)
                            let providers = store.graph.edges.filter { $0.kind == "usb" && $0.target == node.id }.compactMap { store.graph.node($0.source).map(displayName) }
                            Text(L("USB 来源：\(providers.isEmpty ? L("尚未配置") : providers.joined(separator: "、"))")).font(.caption)
                            note("确认文件校验完成和真实 USB 端口后，在此节点调用原来的烧录工具。")
                        }
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }
        }.padding(26).frame(width: 620, height: 460)
    }
}
