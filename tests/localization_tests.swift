import Foundation

@main struct LocalizationTests {
    static func main() throws {
        let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
        let catalog = TranslationCatalog(english: try JSONDecoder().decode([String: String].self, from: data))
        let name = "实验室 {1} / C:\\board"
        let input: UIString = "确认后接回 \(name)，并记住此模式。"
        precondition(input.key == "确认后接回 {0}，并记住此模式。")
        precondition(catalog.render(input.key, arguments: input.arguments, english: true) == "Reconnect to \(name) and remember this mode.")
        precondition(catalog.render(input.key, arguments: input.arguments, english: false) == "确认后接回 \(name)，并记住此模式。")
        precondition(catalog.message("SSH 和虚拟 USB 已就绪", english: true) == "SSH and virtual USB are ready")
        precondition(catalog.message("SSH 隧道失败：broken pipe", english: true) == "SSH tunnel failed: broken pipe")
        precondition(catalog.message("后台服务无法启动，请查看 /tmp/日志.log", english: true) == "Could not start the background service. See /tmp/日志.log")
        precondition(catalog.message("Unknown error /tmp/用户/file", english: true) == "Unknown error /tmp/用户/file")
        precondition(catalog.message("SSH 和虚拟 USB 已就绪", english: false) == "SSH 和虚拟 USB 已就绪")
        let pattern = try NSRegularExpression(pattern: #"\{[0-9]+\}"#)
        func placeholders(_ text: String) -> [String] {
            pattern.matches(in: text, range: NSRange(text.startIndex..., in: text)).map { (text as NSString).substring(with: $0.range) }.sorted()
        }
        for (key, value) in catalog.english {
            precondition(!value.isEmpty, "Empty translation: \(key)")
            precondition(placeholders(key) == placeholders(value), "Placeholder mismatch: \(key)")
        }
        print("Localization rendering and \(catalog.english.count) translation entries passed")
    }
}
