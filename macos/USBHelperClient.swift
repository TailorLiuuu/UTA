import Foundation
import Darwin

private func json(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
        FileHandle.standardOutput.write(data + Data([10]))
    }
}
private func fail(_ text: String) -> Never {
    fputs(text + "\n", stderr); exit(1)
}

@main struct USBHelperClientMain {
    static func main() {
        signal(SIGPIPE, SIG_IGN)
        let args = Array(CommandLine.arguments.dropFirst())
        let status = args == ["status"]
        guard status || (args.count == 4 && args[0] == "export" && validUSBSelection(args[1], args[2], args[3])) else {
            fail("Usage: uta-helper-client status | export BUSID VID:PID SERIAL")
        }
        let connection = NSXPCConnection(machServiceName: usbHelperService, options: .privileged)
        connection.remoteObjectInterface = usbHelperInterface()
        connection.invalidationHandler = { fail("USB 接管服务不可用、授权被撤销，或应用与辅助服务版本不匹配；请在 UTA 中检查本机授权") }
        connection.resume()
        let ready = DispatchSemaphore(value: 0)
        let proxy = connection.remoteObjectProxyWithErrorHandler { error in
            fail("无法连接已授权的 USB 服务：" + error.localizedDescription)
        } as! USBHelperProtocol
        if status {
            proxy.ping { privileged, version in
                json(["available": privileged, "protocol": version]); exit(privileged ? 0 : 1)
            }
        } else {
            proxy.exportDevice(args[1], vidpid: args[2], serial: args[3]) { stream, log, error in
                guard let stream, let log else { fail(error ?? "USB 服务没有返回数据通道") }
                json(["ready": true])
                ready.signal()
                DispatchQueue.global().async {
                    while let data = try? readUSBChunk(log), !data.isEmpty {
                        FileHandle.standardError.write(data)
                    }
                }
                DispatchQueue.global().async {
                    while let data = try? readUSBChunk(FileHandle.standardInput), !data.isEmpty {
                        do { try stream.write(contentsOf: data) } catch { break }
                    }
                    shutdown(stream.fileDescriptor, SHUT_RDWR)
                }
                DispatchQueue.global().async {
                    while let data = try? readUSBChunk(stream), !data.isEmpty {
                        do { try FileHandle.standardOutput.write(contentsOf: data) } catch { break }
                    }
                    connection.invalidationHandler = nil
                    connection.invalidate()
                    exit(0)
                }
            }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + 8) {
            if status || ready.wait(timeout: .now()) == .timedOut { fail("USB 授权服务响应超时") }
        }
        withExtendedLifetime(connection) { dispatchMain() }
    }
}
