import Foundation
import NaturalLanguage

// Local detection, limited to the user's two supported spoken languages.
let text = String(data: FileHandle.standardInput.readDataToEndOfFile(), encoding: .utf8) ?? ""
let recognizer = NLLanguageRecognizer()
recognizer.languageConstraints = [.turkish, .english]
recognizer.processString(text)
print(recognizer.dominantLanguage == .turkish ? "tr" : "en")
