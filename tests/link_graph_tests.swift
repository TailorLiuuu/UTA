import Foundation

@main struct LinkGraphTests {
    static func main() throws {
        var graph = LinkGraph.example()
        try graph.validate()
        let original = graph
        let decoded = try JSONDecoder().decode(LinkGraph.self, from: JSONEncoder().encode(graph))
        precondition(decoded == graph)
        precondition(graph.artifactOrder().count == 1)
        precondition(graph.directUpload(graph.edges[0]))
        precondition(graph.connectionError(source: graph.nodes[0].id, target: graph.nodes[0].id, kind: "artifact") != nil)
        precondition(graph.connectionError(source: graph.nodes[0].id, target: graph.nodes[2].id, kind: "usb") != nil)
        precondition(graph.connectionError(source: graph.nodes[0].id, target: graph.nodes[1].id, kind: "artifact") != nil)
        let relay = try graph.insertRelay(on: graph.edges[0].id)
        try graph.validate()
        precondition(graph.artifactOrder().map(\.target) == [relay, original.nodes[1].id])
        precondition(graph.connectionError(source: original.nodes[1].id, target: relay, kind: "artifact") != nil)
        graph.removeNode(relay); try graph.validate()
        precondition(graph.edges.count == 1 && graph.edges[0].kind == "usb")
        graph = original; graph.nodes[1].platform = "windows"
        precondition((try? graph.validate()) == nil)
        graph = original; graph.nodes[1].id = graph.nodes[0].id
        precondition((try? graph.validate()) == nil)
        graph = original; graph.workspace = "../another-client"
        precondition((try? graph.validate()) == nil)
        graph = original; graph.nodes[0].x = .infinity
        precondition((try? graph.validate()) == nil)
        graph = original; graph.nodes[0].serverID = "bad-server-id"
        precondition((try? graph.validate()) == nil)
        // Independent edge kinds may point in opposite directions.
        graph = original; graph.nodes[0].platform = "linux"; graph.edges = [graph.edges[0]]
        try graph.connect(source: graph.nodes[1].id, target: graph.nodes[0].id, kind: "usb")
        try graph.validate()
        graph = original; graph.nodes[1].serverID = String(repeating: "a", count: 32)
        graph.nodes[2].serverID = graph.nodes[1].serverID
        precondition((try? graph.validate()) == nil)
        graph = original
        graph.nodes[2].serverID = String(repeating: "b", count: 32); graph.nodes[2].usbBusID = "6-3"
        var duplicate = graph.nodes[2]; duplicate.id = UUID().uuidString.lowercased(); duplicate.x += 300
        graph.nodes.append(duplicate)
        precondition(graph.connectionError(source: duplicate.id, target: graph.nodes[1].id, kind: "usb") != nil)
        graph.artifact = "/private/path/firmware.bin"; graph.nodes[2].receiverCLI = "/private/receiver"
        let portable = graph.portableCopy()
        precondition(portable.artifact.isEmpty && portable.nodes.allSatisfy { $0.serverID.isEmpty && $0.usbBusID.isEmpty && $0.receiverCLI.isEmpty })
        let tooLarge = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try Data(repeating: 32, count: 128 * 1024 + 1).write(to: tooLarge)
        defer { try? FileManager.default.removeItem(at: tooLarge) }
        precondition((try? LinkGraph.read(tooLarge)) == nil)
        print("Link graph: serialization, direction, relay order, cycles, deletion, limits and invalid imports passed")
    }
}
