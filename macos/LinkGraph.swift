import Foundation

/// A saved route is a plan, never proof of an SSH connection or USB ownership.
struct LinkNode: Codable, Identifiable, Equatable {
    var id: String = UUID().uuidString.lowercased()
    var name: String
    var platform: String = "linux"
    var role: String = "relay"
    var serverID: String = ""
    var x: Double
    var y: Double
    var usbBusID: String = ""
    var receiverCLI: String = ""
    var isLocal: Bool { platform == "local" }
}
struct LinkEdge: Codable, Identifiable, Equatable {
    var id: String = UUID().uuidString.lowercased()
    var source: String
    var target: String
    var kind: String // artifact or usb; direction is provider -> consumer
}
struct LinkGraph: Codable, Equatable {
    var version = 1
    var name = ""
    var workspace = "project-a"
    var artifact = ""
    var nodes: [LinkNode] = []
    var edges: [LinkEdge] = []

    static func example() -> LinkGraph {
        let mac = LinkNode(name: "Mac", platform: "local", role: "build", x: 180, y: 180)
        let linux = LinkNode(name: "Linux", role: "execute", x: 700, y: 295)
        let windows = LinkNode(name: "Windows", platform: "windows", role: "usb", x: 180, y: 445)
        return LinkGraph(nodes: [mac, linux, windows], edges: [
            LinkEdge(source: mac.id, target: linux.id, kind: "artifact"),
            LinkEdge(source: windows.id, target: linux.id, kind: "usb")])
    }
    static func read(_ url: URL) throws -> LinkGraph {
        let attributes = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
        guard attributes.isRegularFile == true, attributes.isSymbolicLink != true else { throw LinkGraphError("链路文件必须是普通文件") }
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        let data = try handle.read(upToCount: 128 * 1024 + 1) ?? Data()
        guard data.count <= 128 * 1024 else { throw LinkGraphError("链路文件过大") }
        let graph = try JSONDecoder().decode(LinkGraph.self, from: data)
        try graph.validate(); return graph
    }
    func node(_ id: String) -> LinkNode? { nodes.first { $0.id == id } }
    func connectionError(source: String, target: String, kind: String) -> String? {
        guard ["artifact", "usb"].contains(kind), let from = node(source), let to = node(target) else { return "节点或链路类型无效" }
        guard source != target else { return "不能连接到自身" }
        guard edges.count < 64 else { return "最多支持 64 条连线" }
        guard !edges.contains(where: { $0.source == source && $0.target == target && $0.kind == kind }) else { return "这条连线已存在" }
        if kind == "usb" {
            guard to.platform == "linux" else { return "USB 接入目标需要是 Linux；Mac 和 Windows 可以提供 USB" }
            guard !edges.contains(where: { $0.source == source && $0.kind == "usb" }) else { return "同一 USB 提供节点只能交给一个目标；多设备请添加独立节点" }
            if !from.serverID.isEmpty && !from.usbBusID.isEmpty && edges.contains(where: { edge in
                guard edge.kind == "usb", let provider = node(edge.source) else { return false }
                return provider.serverID == from.serverID && provider.usbBusID == from.usbBusID
            }) { return "此服务器上的同一 USB 标识已被另一条链路使用" }
        } else {
            guard !to.isLocal else { return "此版本的固件接收端请选择远端节点" }
            guard !edges.contains(where: { $0.target == target && $0.kind == "artifact" }) else { return "一个节点只接收一条固件输入，避免混用文件" }
        }
        // Only same-kind cycles matter. A -> B (firmware), B -> A (USB) is valid.
        var pending = [target], visited = Set<String>()
        while let current = pending.popLast() {
            if current == source { return "这条连线会形成循环" }
            if visited.insert(current).inserted {
                pending += edges.filter { $0.kind == kind && $0.source == current }.map(\.target)
            }
        }
        if !from.isLocal && !to.isLocal && !from.serverID.isEmpty && from.serverID == to.serverID {
            return "两个节点绑定了同一台服务器，无需跨机连线"
        }
        return nil
    }
    mutating func connect(source: String, target: String, kind: String) throws {
        if let issue = connectionError(source: source, target: target, kind: kind) { throw LinkGraphError(issue) }
        edges.append(LinkEdge(source: source, target: target, kind: kind))
    }
    mutating func removeNode(_ id: String) {
        nodes.removeAll { $0.id == id }; edges.removeAll { $0.source == id || $0.target == id }
    }
    mutating func insertRelay(on id: String) throws -> String {
        guard nodes.count < 32, let index = edges.firstIndex(where: { $0.id == id }), edges[index].kind == "artifact",
              let from = node(edges[index].source), let to = node(edges[index].target) else { throw LinkGraphError("请选择固件连线后插入中继") }
        let relay = LinkNode(name: "Relay", x: (from.x + to.x) / 2, y: max(from.y, to.y) + 225)
        nodes.append(relay); edges.remove(at: index)
        try connect(source: from.id, target: relay.id, kind: "artifact")
        try connect(source: relay.id, target: to.id, kind: "artifact")
        return relay.id
    }
    func validate() throws {
        guard version == 1, nodes.count <= 32, edges.count <= 64, name.count <= 120,
              artifact.count <= 4096, workspace.range(of: #"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"#, options: .regularExpression) != nil else {
            throw LinkGraphError("链路文件版本、规模或项目标识无效")
        }
        guard Set(nodes.map(\.id)).count == nodes.count, Set(edges.map(\.id)).count == edges.count,
              nodes.filter(\.isLocal).count <= 1 else { throw LinkGraphError("节点或连线标识重复") }
        for node in nodes {
            guard UUID(uuidString: node.id) != nil, !node.name.isEmpty, node.name.count <= 120,
                  node.usbBusID.count <= 120, node.receiverCLI.count <= 512, node.x.isFinite, node.y.isFinite,
                  (120...4000).contains(node.x), (110...3000).contains(node.y),
                  ["local", "macos", "linux", "windows"].contains(node.platform),
                  ["build", "relay", "execute", "usb"].contains(node.role),
                  node.serverID.isEmpty || node.serverID.range(of: #"^[a-f0-9]{32}$"#, options: .regularExpression) != nil else {
                throw LinkGraphError("节点配置无效")
            }
        }
        var checked = self; checked.edges = []
        for edge in edges {
            guard UUID(uuidString: edge.id) != nil else { throw LinkGraphError("连线标识无效") }
            try checked.connect(source: edge.source, target: edge.target, kind: edge.kind)
        }
    }
    /// Deterministic plan order. USB is a separate prerequisite, never a firmware hop.
    func artifactOrder() -> [LinkEdge] {
        var pending = edges.filter { $0.kind == "artifact" }, ordered: [LinkEdge] = []
        while !pending.isEmpty {
            guard let index = pending.firstIndex(where: { edge in !pending.contains { $0.target == edge.source } }) else { break }
            ordered.append(pending.remove(at: index))
        }
        return ordered
    }
    func directUpload(_ edge: LinkEdge) -> Bool {
        edge.kind == "artifact" && node(edge.source)?.isLocal == true && node(edge.target)?.isLocal == false
    }
    func portableCopy() -> LinkGraph {
        var copy = self; copy.artifact = ""
        for i in copy.nodes.indices {
            copy.nodes[i].serverID = ""; copy.nodes[i].usbBusID = ""; copy.nodes[i].receiverCLI = ""
        }
        return copy
    }
}
struct LinkGraphError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}
