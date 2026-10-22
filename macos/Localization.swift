import Foundation

/// Interpolation retains a stable translation key and keeps user-provided values
/// separate. Replacement happens once, so braces inside a server name are safe.
struct UIString: ExpressibleByStringLiteral, ExpressibleByStringInterpolation {
    var key: String
    var arguments: [String]
    init(stringLiteral value: String) { key = value; arguments = [] }
    init(stringInterpolation: StringInterpolation) {
        key = stringInterpolation.key; arguments = stringInterpolation.arguments
    }
    struct StringInterpolation: StringInterpolationProtocol {
        var key = ""
        var arguments: [String] = []
        init(literalCapacity: Int, interpolationCount: Int) {}
        mutating func appendLiteral(_ value: String) { key += value }
        mutating func appendInterpolation<T>(_ value: T) {
            key += "{\(arguments.count)}"; arguments.append(String(describing: value))
        }
    }
}

struct TranslationCatalog {
    let english: [String: String]
    static let shared: TranslationCatalog = {
        guard let url = Bundle.main.url(forResource: "en", withExtension: "json") else { return TranslationCatalog(english: [:]) }
        return TranslationCatalog(english: (try? JSONDecoder().decode([String: String].self, from: Data(contentsOf: url))) ?? [:])
    }()
    func render(_ key: String, arguments: [String], english useEnglish: Bool) -> String {
        let template = useEnglish ? (english[key] ?? key) : key
        let regex = try! NSRegularExpression(pattern: #"\{([0-9]+)\}"#)
        let source = template as NSString
        var output = "", cursor = 0
        for match in regex.matches(in: template, range: NSRange(location: 0, length: source.length)) {
            output += source.substring(with: NSRange(location: cursor, length: match.range.location-cursor))
            let index = Int(source.substring(with: match.range(at: 1)))!
            output += arguments.indices.contains(index) ? arguments[index] : source.substring(with: match.range)
            cursor = NSMaxRange(match.range)
        }
        return output + source.substring(from: cursor)
    }
    /// Translate only presentation messages, never device names, paths, SSH
    /// commands or wire-protocol values. Unknown technical errors stay verbatim.
    func message(_ raw: String, english useEnglish: Bool) -> String {
        guard useEnglish else { return raw }
        if let value = english[raw] { return value }
        for key in english.keys.sorted(by: { $0.count > $1.count }) {
            if key.contains("{0}") {
                let token = try! NSRegularExpression(pattern: #"\{[0-9]+\}"#)
                let source = key as NSString
                var pattern = "", cursor = 0
                for match in token.matches(in: key, range: NSRange(location: 0, length: source.length)) {
                    pattern += NSRegularExpression.escapedPattern(for: source.substring(with: NSRange(location: cursor, length: match.range.location-cursor))) + "(.*?)"
                    cursor = NSMaxRange(match.range)
                }
                pattern += NSRegularExpression.escapedPattern(for: source.substring(from: cursor))
                guard let regex = try? NSRegularExpression(pattern: "^"+pattern+"$", options: [.dotMatchesLineSeparators]),
                      let match = regex.firstMatch(in: raw, range: NSRange(raw.startIndex..., in: raw)), match.numberOfRanges > 1 else { continue }
                let text = raw as NSString
                return render(key, arguments: (1..<match.numberOfRanges).map { text.substring(with: match.range(at: $0)) }, english: true)
            } else if key.count > 5 && (key.hasSuffix("：") || key.hasSuffix(": ")) && raw.hasPrefix(key) {
                return english[key]! + String(raw.dropFirst(key.count))
            }
        }
        return raw
    }
}

func L(_ value: UIString) -> String {
    TranslationCatalog.shared.render(value.key, arguments: value.arguments,
        english: UserDefaults.standard.string(forKey: "UTA.language") == "en")
}
func D(_ message: String) -> String {
    TranslationCatalog.shared.message(message, english: UserDefaults.standard.string(forKey: "UTA.language") == "en")
}
