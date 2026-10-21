import Foundation
import Darwin

// Stable Developer ID service, separate from the pre-signing prototype record.
let usbHelperService = "dev.uta.usb-helper.v1"
let usbHelperPlist = usbHelperService + ".plist"
let legacyUSBHelperPlist = "dev.uta.usb-helper.plist"

@objc protocol USBHelperProtocol {
    func ping(withReply reply: @escaping (Bool, Int) -> Void)
    func exportDevice(_ busid: String, vidpid: String, serial: String,
                      withReply reply: @escaping (FileHandle?, FileHandle?, String?) -> Void)
}

func usbHelperInterface() -> NSXPCInterface {
    NSXPCInterface(with: USBHelperProtocol.self)
}

// FileHandle.read(upToCount:) can wait for the requested size on a socket/pipe.
// USB request/reply traffic must forward each available chunk immediately.
func readUSBChunk(_ handle: FileHandle, size: Int = 65536) throws -> Data {
    var bytes = [UInt8](repeating: 0, count: size)
    while true {
        let count = Darwin.read(handle.fileDescriptor, &bytes, bytes.count)
        if count >= 0 { return Data(bytes.prefix(count)) }
        if errno != EINTR { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
    }
}

func validUSBSelection(_ busid: String, _ vidpid: String, _ serial: String) -> Bool {
    busid.range(of: #"^[0-9]{1,3}-[0-9.]{1,24}$"#, options: .regularExpression) != nil &&
    vidpid.range(of: #"^[0-9a-f]{4}:[0-9a-f]{4}$"#, options: .regularExpression) != nil &&
    serial.utf8.count <= 256 && !serial.contains("\0")
}
