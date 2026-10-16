import SwiftUI

struct RingView: View {
    let model: RingModel
    @State private var memory = SystemMemory.read()
    /// Messages wait for the user's yes: a soft warm tint and a slow, deeper pulse on the idle ring.
    private var waiting: Bool { model.waiting > 0 && model.phase == .idle }
    private var tint: Color {
        if waiting { return Color(red: 1, green: 0.78, blue: 0.5) }
        switch model.phase {
        case .listening: return .mint
        case .thinking: return .cyan
        case .speaking: return Color(red: 0.4, green: 0.65, blue: 1)
        case .error, .noSpeech, .quitting: return .orange
        case .copied: return .yellow
        default: return Color(red: 0.25, green: 0.85, blue: 0.95)
        }
    }
    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30)) { timeline in
            let time = timeline.date.timeIntervalSinceReferenceDate
            let pulse = 1 + sin(time * (waiting ? 1.1 : model.phase == .idle ? 1.5 : 4)) * (waiting ? 0.04 : 0.025) + model.level * 0.04
            VStack(spacing: 6) {
                ZStack {
                    Circle().stroke(tint.opacity(0.15), lineWidth: 18).blur(radius: 12).frame(width: 142, height: 142)
                    Circle().stroke(tint.opacity(0.3), lineWidth: 1).frame(width: 166, height: 166)
                    Circle().trim(from: 0.02, to: 0.77)
                        .stroke(AngularGradient(colors: [.clear, tint, .white.opacity(0.9), tint.opacity(0.2)], center: .center), style: StrokeStyle(lineWidth: 3, lineCap: .round))
                        .frame(width: 152, height: 152).rotationEffect(.degrees(time * (model.phase == .thinking ? 100 : 20)))
                    Circle().trim(from: 0.1, to: 0.88).stroke(tint.opacity(0.5), style: StrokeStyle(lineWidth: 1, dash: [3, 8]))
                        .frame(width: 128, height: 128).rotationEffect(.degrees(-time * 15))
                    ForEach(0..<40, id: \.self) { i in
                        Capsule().fill(tint.opacity(0.35 + 0.45 * (1 + sin(time * 2 + Double(i))) / 2))
                            .frame(width: 2, height: model.phase == .listening ? 4 + model.level * 12 : 5)
                            .offset(y: -95).rotationEffect(.degrees(Double(i) * 9))
                    }
                    Group {
                        VStack(spacing: 6) {
                            Image(systemName: model.phase == .listening ? (model.dictating ? "character.cursor.ibeam" : "waveform") : model.phase == .speaking ? "speaker.wave.2" : "sparkle")
                                .font(.system(size: 28, weight: .ultraLight))
                            Text(memory?.label ?? "RAM —").font(.system(size: 9, weight: .medium, design: .monospaced))
                                .monospacedDigit().lineLimit(1).minimumScaleFactor(0.8)
                                .help("Sistem RAM kullanımı / System memory used")
                        }.foregroundStyle(tint).frame(width: 100, height: 100).contentShape(Circle())
                    }
                    if waiting && model.waiting > 1 {
                        Text("\(model.waiting)").font(.system(size: 10, weight: .semibold, design: .rounded)).monospacedDigit()
                            .foregroundStyle(.black.opacity(0.75)).frame(width: 18, height: 18).background(Circle().fill(tint))
                            .offset(x: 52, y: -52)
                    }
                }.frame(width: 220, height: 220).scaleEffect(pulse)
                Text(model.phase == .quitting ? "" : model.phase == .idle ? (model.shortcutEnabled ? "\(model.shortcutLabel)  HOLD TO TALK" : "CLICK TO TYPE") : model.phase == .noSpeech ? "NO SPEECH" : model.dictating && model.phase != .error ? (model.phase == .thinking ? "TYPING" : "DICTATION") : model.phase.rawValue.uppercased())
                    .font(.system(size: 9, weight: .medium, design: .monospaced)).tracking(1.5)
                    .foregroundStyle(tint.opacity(0.75)).shadow(color: .black.opacity(0.6), radius: 4)
            }
        }.frame(width: 240, height: 260)
            .task {
                while !Task.isCancelled {
                    memory = SystemMemory.read()
                    do { try await Task.sleep(for: .seconds(2)) } catch { return }
                }
            }
            .overlay(RingInteraction(onDoubleClick: model.toggleInput, onRightClick: model.contextMenu, onClick: model.ringClicked))
    }
}
