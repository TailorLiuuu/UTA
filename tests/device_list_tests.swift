import Foundation

@main struct DeviceListTests {
    static func main() {
        func visible(_ state: String, cleanup: Bool = false, busy: Bool = false,
                     deadline: TimeInterval? = nil) -> Bool {
            showsMissingUSBSession(state: state, cleanupPending: cleanup, busy: busy,
                                   reconnectDeadline: deadline, now: 100)
        }
        // Replug: the old bus record is complete; the newly captured device is
        // absent from the ordinary local scan. Only the current card survives.
        precondition(["local", "attached"].filter { visible($0) } == ["attached"])
        precondition(!visible("local"))
        precondition(!visible("error"))
        precondition(!visible("local", deadline: 0))
        precondition(!visible("error", deadline: 99))
        precondition(!visible("error", deadline: 100))
        // Never hide pending cleanup or a reconnect/confirmation opportunity.
        precondition(visible("local", cleanup: true))
        precondition(visible("error", cleanup: true))
        precondition(visible("local", busy: true))
        precondition(visible("error", deadline: 101))
        for state in ["connecting", "enumerating", "attached", "disconnecting", "waiting_reconnect"] {
            precondition(visible(state), state)
        }
        // Two currently attached devices are two cards, regardless of model.
        precondition(["attached", "attached"].filter { visible($0) }.count == 2)
        print("Device list: replug history, cleanup, reconnection and multiple-device checks passed")
    }
}
