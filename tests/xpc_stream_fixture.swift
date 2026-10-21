import Foundation
import Darwin

private final class Fixture: NSObject, USBHelperProtocol, NSXPCListenerDelegate {
    func listener(_ listener: NSXPCListener, shouldAcceptNewConnection c: NSXPCConnection) -> Bool {
        c.exportedInterface = usbHelperInterface()
        c.exportedObject = self
        c.resume()
        return true
    }
    func ping(withReply reply: @escaping (Bool, Int) -> Void) { reply(true, 1) }
    func exportDevice(_ busid: String, vidpid: String, serial: String,
                      withReply reply: @escaping (FileHandle?, FileHandle?, String?) -> Void) {
        var fds: [Int32] = [-1, -1]
        guard socketpair(AF_UNIX, SOCK_STREAM, 0, &fds) == 0 else { exit(2) }
        let worker = FileHandle(fileDescriptor: fds[0], closeOnDealloc: true)
        let client = FileHandle(fileDescriptor: fds[1], closeOnDealloc: true)
        let log = Pipe()
        reply(client, log.fileHandleForReading, nil)
        DispatchQueue.global().async {
            do {
                while true {
                    let data = try readUSBChunk(worker)
                    if data.isEmpty { break }
                    try worker.write(contentsOf: data)
                }
                try worker.close()
            } catch { exit(3) }
        }
    }
}

@main struct Main {
    static func main() {
        let fixture = Fixture()
        let listener = NSXPCListener.anonymous()
        listener.delegate = fixture
        listener.resume()
        let connection = NSXPCConnection(listenerEndpoint: listener.endpoint)
        connection.remoteObjectInterface = usbHelperInterface()
        connection.resume()
        let proxy = connection.remoteObjectProxyWithErrorHandler { _ in exit(4) } as! USBHelperProtocol
        proxy.exportDevice("2-1", vidpid: "303a:1001", serial: "") { stream, log, error in
            guard let stream, log != nil, error == nil else { exit(5) }
            do {
                // Must get a response without filling the 64 KiB read buffer or closing input.
                for payload in [Data([0x80]), Data("UTA XPC descriptor test".utf8)] {
                    try stream.write(contentsOf: payload)
                    var received = Data()
                    while received.count < payload.count {
                        let chunk = try readUSBChunk(stream)
                        if chunk.isEmpty { exit(6) }
                        received += chunk
                    }
                    guard received == payload else { exit(7) }
                }
                shutdown(stream.fileDescriptor, SHUT_WR)
                guard try readUSBChunk(stream).isEmpty else { exit(8) }
                print("XPC short-packet roundtrip and EOF: PASS")
                exit(0)
            } catch { exit(9) }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + 5) { exit(10) }
        withExtendedLifetime((fixture, listener, connection)) { dispatchMain() }
    }
}
