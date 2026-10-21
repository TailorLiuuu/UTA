import Foundation
import SystemConfiguration
import Darwin
@_silgen_name("uta_export_stream")
func utaExportStream(_ fd: Int32, _ logfd: Int32, _ busid: UnsafePointer<CChar>,
                     _ vidpid: UnsafePointer<CChar>, _ serial: UnsafePointer<CChar>) -> Int32

// A fixed-purpose root helper: no shell, arbitrary executable, path, or file operations.
// The accepted client CDHash is generated at build time and checked by XPC itself.
private let reservations = NSLock()
private var exportedDevices = Set<String>()
private func releaseDevice(_ busid: String) {
    reservations.lock(); exportedDevices.remove(busid); reservations.unlock()
}

final class USBLease: NSObject, USBHelperProtocol {
    let uid: uid_t
    private let lock = NSLock()
    private var closed = false
    private var used = false
    private var stream: FileHandle?
    init(uid: uid_t) { self.uid = uid }
    func ping(withReply reply: @escaping (Bool, Int) -> Void) { reply(geteuid() == 0, 1) }

    func exportDevice(_ busid: String, vidpid: String, serial: String,
                      withReply reply: @escaping (FileHandle?, FileHandle?, String?) -> Void) {
        lock.lock()
        defer { lock.unlock() }
        var consoleUID: uid_t = 0
        _ = SCDynamicStoreCopyConsoleUser(nil, &consoleUID, nil)
        guard geteuid() == 0, uid >= 501, uid == consoleUID else {
            reply(nil, nil, "仅允许当前登录用户使用已授权的 USB 接管服务"); return
        }
        guard !closed, !used, validUSBSelection(busid, vidpid, serial) else {
            reply(nil, nil, "设备参数无效或连接已使用"); return
        }
        reservations.lock()
        let allowed = exportedDevices.count < 8 && !exportedDevices.contains(busid)
        if allowed { exportedDevices.insert(busid) }
        reservations.unlock()
        guard allowed else { reply(nil, nil, "设备正在接管或已达到 8 个会话上限"); return }
        var fds: [Int32] = [-1, -1]
        guard socketpair(AF_UNIX, SOCK_STREAM, 0, &fds) == 0 else {
            release(busid); reply(nil, nil, "无法创建 USB 数据通道"); return
        }
        let workerStream = FileHandle(fileDescriptor: fds[0], closeOnDealloc: true)
        let clientStream = FileHandle(fileDescriptor: fds[1], closeOnDealloc: true)
        for fd in fds { _ = fcntl(fd, F_SETFD, FD_CLOEXEC) }
        let log = Pipe()
        let dataFD = dup(workerStream.fileDescriptor)
        let logFD = dup(log.fileHandleForWriting.fileDescriptor)
        guard dataFD >= 0 && logFD >= 0 else {
            if dataFD >= 0 { close(dataFD) }
            if logFD >= 0 { close(logFD) }
            release(busid)
            reply(nil, nil, "USB 数据通道分配失败")
            return
        }
        used = true
        stream = workerStream
        // The native bridge is linked into this helper; it never loads user-selected code.
        reply(clientStream, log.fileHandleForReading, nil)
        try? log.fileHandleForWriting.close()
        DispatchQueue.global().async {
            _ = busid.withCString { b in vidpid.withCString { v in serial.withCString { s in
                utaExportStream(dataFD, logFD, b, v, s)
            }}}
            releaseDevice(busid)
        }
    }
    private func release(_ busid: String) {
        releaseDevice(busid)
    }
    func invalidate() {
        lock.lock()
        closed = true
        let socket = stream
        stream = nil
        lock.unlock()
        if let socket {
            shutdown(socket.fileDescriptor, SHUT_RDWR)
            try? socket.close()
        }
    }
}

final class HelperDelegate: NSObject, NSXPCListenerDelegate {
    func listener(_ listener: NSXPCListener, shouldAcceptNewConnection connection: NSXPCConnection) -> Bool {
        var uid: uid_t = 0
        _ = SCDynamicStoreCopyConsoleUser(nil, &uid, nil)
        guard geteuid() == 0, uid >= 501, connection.effectiveUserIdentifier == uid else { return false }
        let lease = USBLease(uid: uid)
        connection.exportedInterface = usbHelperInterface()
        connection.exportedObject = lease
        connection.invalidationHandler = { lease.invalidate() }
        connection.interruptionHandler = { lease.invalidate() }
        connection.resume()
        return true
    }
}

@main struct USBHelperMain {
    static func main() {
        signal(SIGPIPE, SIG_IGN)
        guard geteuid() == 0 else { fputs("UTA helper must be managed by launchd as root\n", stderr); exit(1) }
        let delegate = HelperDelegate()
        let listener = NSXPCListener(machServiceName: usbHelperService)
        listener.setConnectionCodeSigningRequirement(utaHelperClientRequirement)
        listener.delegate = delegate
        listener.resume()
        withExtendedLifetime(delegate) { dispatchMain() }
    }
}
