import SwiftUI

struct InputView: View {
    @Bindable var model: RingModel
    @FocusState private var focused: Bool
    private var busy: Bool { model.phase == .starting || model.changingMode } // Typed while busy: steered into the running request.
    var body: some View {
        VStack(spacing: 12) {
            if !model.response.isEmpty {
                ScrollView {
                    Text(model.response).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }.frame(maxHeight: .infinity)
            } else { Spacer(minLength: 0) }
            if model.phase == .error {
                Text(model.status).font(.caption).foregroundStyle(.orange)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack(alignment: .bottom, spacing: 8) {
                TextField("Mesaj yaz / Type a message…", text: $model.input, axis: .vertical)
                    .lineLimit(1...4).textFieldStyle(.plain).focused($focused)
                    .onSubmit { model.send() }
                Button { model.send() } label: {
                    Image(systemName: "arrow.up.circle.fill").font(.system(size: 25))
                }.buttonStyle(.plain).foregroundStyle(.cyan)
                    .disabled(busy || model.input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .keyboardShortcut(.return, modifiers: .command)
                    .help("Gönder / Send").accessibilityLabel("Gönder / Send")
            }.padding(12).background(.quaternary, in: RoundedRectangle(cornerRadius: 14))
        }.padding(14).frame(width: 360, height: 230)
            .background(.ultraThinMaterial)
            .onAppear { focused = true }
    }
}
