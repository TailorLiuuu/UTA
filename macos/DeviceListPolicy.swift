import Foundation

/// A missing USB can still be captured or need cleanup/reconnection. Completed
/// sessions remain in diagnostics, but are not physical devices in the list.
func showsMissingUSBSession(state: String, cleanupPending: Bool, busy: Bool,
                            reconnectDeadline: TimeInterval?, now: TimeInterval) -> Bool {
    ["connecting", "enumerating", "attached", "disconnecting", "waiting_reconnect"].contains(state)
        || cleanupPending || busy || (reconnectDeadline ?? 0) > now
}
