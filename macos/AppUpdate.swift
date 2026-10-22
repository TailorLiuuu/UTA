import SwiftUI
import Sparkle

/// Sparkle owns download, EdDSA validation, replacement and relaunch.
/// UTA reserves the controller while the update dialog is open, so a CLI cannot
/// start a new export between the initial idle check and application termination.
@MainActor final class AppUpdate: NSObject, ObservableObject, SPUUpdaterDelegate {
    @Published var message = "检查更新"
    @Published var availableVersion: String?
    @Published var checking = false
    @Published var canCheck = false
    @Published var automatic = true
    weak var model: Model?
    private var observation: NSKeyValueObservation?
    private var started = false
    private var scheduledCheck: Task<Void, Never>?
    private(set) var reserved = false
    private(set) var installing = false
    lazy var controller = SPUStandardUpdaterController(startingUpdater: false,
        updaterDelegate: self, userDriverDelegate: nil)

    func start(model: Model) {
        guard !started else { return }
        started = true
        self.model = model
        observation = controller.updater.observe(\.canCheckForUpdates, options: [.initial, .new]) { [weak self] _, change in
            Task { @MainActor in self?.canCheck = change.newValue ?? false }
        }
        controller.startUpdater()
        let preferences = UserDefaults.standard
        automatic = preferences.object(forKey: "UTA.automaticUpdates") as? Bool
            ?? controller.updater.automaticallyChecksForUpdates
        preferences.set(automatic, forKey: "UTA.automaticUpdates")
        // UTA schedules information-only checks. Sparkle's scheduled UI sessions
        // are disabled, so a home banner never holds an unfinished installer UI.
        controller.updater.automaticallyChecksForUpdates = false
        scheduledCheck = Task { [weak self] in
            while !Task.isCancelled {
                self?.probe()
                do { try await Task.sleep(for: .seconds(6 * 60 * 60)) }
                catch { return }
            }
        }
    }

    func check() {
        guard controller.updater.canCheckForUpdates else { return }
        do {
            try reserveForInteractiveUpdate()
            controller.checkForUpdates(nil)
        } catch { model?.error = error.localizedDescription }
    }
    private func reserveForInteractiveUpdate() throws {
        _ = try Self.request("update_prepare")
        reserved = true
        checking = true
    }
    private func probe() {
        if automatic && controller.updater.canCheckForUpdates {
            controller.updater.checkForUpdateInformation()
        }
    }
    func setAutomatic(_ value: Bool) {
        automatic = value
        UserDefaults.standard.set(value, forKey: "UTA.automaticUpdates")
        if value { probe() }
    }

    // The delegate must return synchronously. The small local RPC is bounded and
    // contains no SSH/network operation; the daemon atomically refuses busy USBs.
    static func request(_ action: String) throws -> [String: Any] {
        let process = Process()
        process.executableURL = Bundle.main.resourceURL!.appendingPathComponent("uta")
        let payload: [String: Any] = ["action": action, "pid": ProcessInfo.processInfo.processIdentifier]
        process.arguments = ["rpc", String(data: try JSONSerialization.data(withJSONObject: payload), encoding: .utf8)!]
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        try process.run()
        let timeout = DispatchWorkItem { if process.isRunning { process.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + 12, execute: timeout)
        defer { timeout.cancel() }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard let response = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              response["ok"] as? Bool == true else {
            let response = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            throw NSError(domain: "UTA.Update", code: 1, userInfo: [NSLocalizedDescriptionKey:
                response?["error"] as? String ?? L("无法确认本地后台状态，请稍后重试")])
        }
        return response["result"] as? [String: Any] ?? [:]
    }

    func updater(_ updater: SPUUpdater, mayPerform updateCheck: SPUUpdateCheck) throws {
        // Scheduled checks only discover versions. They never reserve USB or
        // open an update window; the home banner is the gentle reminder.
        if updateCheck == .updates { try reserveForInteractiveUpdate() }
        message = "正在检查更新…"
    }
    func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        availableVersion = item.displayVersionString
        message = "发现新版本 · " + item.displayVersionString
    }
    func updater(_ updater: SPUUpdater, willDownloadUpdate item: SUAppcastItem, with request: NSMutableURLRequest) {
        message = "正在下载更新…"
    }
    func updater(_ updater: SPUUpdater, willInstallUpdate item: SUAppcastItem) {
        installing = true
        message = "正在准备安装并重启…"
    }
    func updater(_ updater: SPUUpdater, didFinishUpdateCycleFor updateCheck: SPUUpdateCheck, error: Error?) {
        guard !installing || error != nil else { return }
        installing = false
        if let error, (error as NSError).code == SUError.noUpdateError.rawValue { availableVersion = nil }
        releaseReservation()
        if let error, (error as NSError).code != SUError.noUpdateError.rawValue {
            message = "检查未完成，请重试"
        } else {
            message = "检查更新"
        }
    }
    func releaseReservation() {
        if reserved { _ = try? Self.request("update_cancel") }
        reserved = false
        checking = false
    }
}

@MainActor final class UTAApplicationDelegate: NSObject, NSApplicationDelegate {
    weak var update: AppUpdate?
    private var terminating = false
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let update, update.reserved || update.installing else { return .terminateNow }
        guard !terminating else { return .terminateLater }
        terminating = true
        update.model?.suspendedForUpdate = true
        Task {
            do {
                await update.model?.quiesceForUpdate()
                let result = try AppUpdate.request("update_shutdown")
                guard let pid = result["pid"] as? Int32 else {
                    throw NSError(domain: "UTA.Update", code: 2,
                        userInfo: [NSLocalizedDescriptionKey: L("后台未返回退出状态")])
                }
                for _ in 0..<100 {
                    if kill(pid, 0) != 0 && errno == ESRCH {
                        sender.reply(toApplicationShouldTerminate: true)
                        return
                    }
                    try await Task.sleep(for: .milliseconds(100))
                }
                throw NSError(domain: "UTA.Update", code: 3,
                    userInfo: [NSLocalizedDescriptionKey: L("后台未及时退出，暂未安装更新，请重试")])
            } catch {
                update.model?.suspendedForUpdate = false
                update.model?.error = error.localizedDescription
                terminating = false
                sender.reply(toApplicationShouldTerminate: false)
            }
        }
        return .terminateLater
    }
}
