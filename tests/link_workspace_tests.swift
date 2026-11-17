import Foundation

@main struct LinkWorkspaceTests {
    @MainActor static func main() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("uta-links-test-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let path = root.appendingPathComponent("private/links.json")
        let store = LinkWorkspace(storageOverride: path)
        store.change { $0.workspace = "sample-project"; $0.artifact = "/tmp/file with 'quotes'; no-shell.bin" }
        try await Task.sleep(for: .milliseconds(600))
        let saved = try Data(contentsOf: path)
        let decoded = try JSONDecoder().decode(LinkGraph.self, from: saved)
        precondition(decoded == store.graph)
        let permissions = try FileManager.default.attributesOfItem(atPath: path.path)[.posixPermissions] as! NSNumber
        precondition(permissions.intValue == 0o600)
        let restored = LinkWorkspace(storageOverride: path)
        precondition(restored.graph == store.graph)
        store.undo(); precondition(store.graph.workspace == "project-a")
        store.redo(); precondition(store.graph.workspace == "sample-project")
        let bad = root.appendingPathComponent("broken.json"); try Data("not-json".utf8).write(to: bad)
        let broken = LinkWorkspace(storageOverride: bad); precondition(broken.error != nil)
        broken.change { $0.workspace = "must-not-overwrite" }
        try await Task.sleep(for: .milliseconds(450))
        let badContents = try String(contentsOf: bad, encoding: .utf8)
        precondition(badContents == "not-json")
        let host = Host(id: String(repeating: "a", count: 32), name: "Test host", target: "test-only")
        store.change { $0.nodes[1].serverID = host.id; $0.nodes[1].receiverCLI = "/tmp/receiver path/uta-server" }
        let target = store.graph.nodes[1]
        await store.check(target, host: host)
        precondition(store.error == nil && store.checked[target.id]?.serverID == host.id)
        await store.send(store.graph.edges[0], host: host)
        precondition(store.error == nil && store.receipt.contains("verified.bin") && !store.busy)
        let data = try await store.cli.run(["capture", "path with 'quotes'", "; untouched", "--to", host.id]) { _ in }
        let response = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        precondition(response["args"] as! [String] == ["capture", "path with 'quotes'", "; untouched", "--to", host.id])
        let waiting = Task { try await store.cli.run(["wait"]) { _ in } }
        try await Task.sleep(for: .milliseconds(300)); store.cli.cancel()
        do { _ = try await waiting.value; preconditionFailure("Cancellation must fail the command") } catch {}
        var portable = store.graph; portable.nodes[2].serverID = host.id; portable.nodes[2].usbBusID = "private-device"
        portable = portable.portableCopy()
        precondition(portable.artifact.isEmpty && portable.nodes.allSatisfy { $0.serverID.isEmpty && $0.usbBusID.isEmpty && $0.receiverCLI.isEmpty })
        try await Task.sleep(for: .milliseconds(400))
        print("Workspace persistence, private permissions, invalid-file preservation, CLI arguments, receiver check, upload result, cancellation and sanitized export passed")
    }
}
