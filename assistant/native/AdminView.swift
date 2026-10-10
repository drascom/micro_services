import SwiftUI
import Observation

/// Native "Yönetim / Admin" window: read-only status, settings moved from the context menu,
/// and user editing of app-owned harness skills/extensions. Talks only to the protected loopback API.
struct AdminStatus: Decodable {
    struct Backend: Decodable { let pid: Int; let port: Int; let uptime_s: Int; let memory_mb: Int? }
    struct Live: Decodable, Identifiable { let request: String; let kind: String; let stage: String; let elapsed_ms: Int; var id: String { request } }
    struct Login: Decodable, Identifiable { let provider: String; let type: String; var id: String { provider } }
    struct Owned: Decodable, Identifiable { let name: String; let running: Bool; let pid: Int?; let port: Int?; let memory_mb: Int?; var id: String { name } }
    let backend: Backend; let busy: Bool; let unloading: Bool; let requests: [Live]
    let remote_model: String; let jev_enabled: Bool; let tts_engine: String; let pi_login: [Login]
    let local_healthy: Bool; let processes: [Owned]; let history_messages: Int
}

struct AdminResource: Decodable, Identifiable {
    let kind: String; let name: String; let description: String; let path: String; let files: [String]
    let valid: Bool?; let loading: String?
    var id: String { kind + "/" + name }
}
struct AdminResources: Decodable { let skills: [AdminResource]; let extensions: [AdminResource] }

struct ActivityRow: Identifiable { let id: Int; let time: String; let request: String; let event: String; let details: String }

@MainActor @Observable
final class AdminModel {
    struct Actions {
        var changeShortcut: () -> Void = {}
        var editInstructions: () -> Void = {}
        var openActivityLog: () -> Void = {}
        var restart: () -> Void = {}
        var setMenuBarIcon: (Bool) -> Void = { _ in }
    }
    let backend: BackendClient
    let ring: RingModel
    var actions = Actions()
    var menuBarIcon = UserDefaults.standard.bool(forKey: "showMenuBarIcon")
    var status: AdminStatus?
    var activity: [ActivityRow] = []
    var statusError = ""
    var updated: Date?
    var resources: AdminResources?
    var selectedKind = ""
    var selectedPath = ""
    var content = ""
    var original = ""
    var message = ""
    var loadError = ""
    private var polling: Task<Void, Never>?

    init(backend: BackendClient, ring: RingModel) { self.backend = backend; self.ring = ring }

    func startPolling() {
        polling?.cancel()
        polling = Task {
            while !Task.isCancelled {
                await refreshStatus()
                try? await Task.sleep(for: .seconds(3)) // Intentional status refresh cadence.
            }
        }
        Task { await loadResources() }
    }
    func stopPolling() { polling?.cancel(); polling = nil }

    func refreshStatus() async {
        do {
            let data = try await backend.admin("status")
            status = try JSONDecoder().decode(AdminStatus.self, from: data)
            let raw = (try JSONSerialization.jsonObject(with: data) as? [String: Any])?["activity"] as? [[String: Any]] ?? []
            activity = raw.enumerated().map { index, entry in
                let skip: Set<String> = ["time", "request", "event"]
                let details = entry.filter { !skip.contains($0.key) }.sorted { $0.key < $1.key }
                    .map { "\($0.key)=\($0.value)" }.joined(separator: "  ")
                let time = (entry["time"] as? String).map { String($0.dropFirst(11).prefix(8)) } ?? ""
                return ActivityRow(id: index, time: time, request: entry["request"] as? String ?? "",
                                   event: entry["event"] as? String ?? "", details: details)
            }
            statusError = ""; updated = Date()
        } catch { statusError = error.localizedDescription }
    }

    func loadResources() async {
        do { resources = try JSONDecoder().decode(AdminResources.self, from: try await backend.admin("admin/resources")) }
        catch { message = error.localizedDescription }
    }
    func open(kind: String, path: String) async {
        // Select immediately so the row highlights; load errors are shown in the editor pane.
        selectedKind = kind; selectedPath = path; content = ""; original = ""; message = ""; loadError = ""
        do {
            let data = try await backend.admin("admin/read", ["kind": kind, "path": path])
            guard selectedKind == kind, selectedPath == path else { return }
            guard let text = (try JSONSerialization.jsonObject(with: data) as? [String: Any])?["content"] as? String else {
                loadError = "Geçersiz yanıt / Invalid response"; return
            }
            content = text; original = text
        } catch {
            if selectedKind == kind, selectedPath == path { loadError = error.localizedDescription }
        }
    }
    func save() async {
        do {
            let data = try await backend.admin("admin/write", ["kind": selectedKind, "path": selectedPath, "content": content])
            original = content
            message = ((try JSONSerialization.jsonObject(with: data) as? [String: Any])?["note"] as? String) ?? "Kaydedildi / Saved"
            await loadResources()
        } catch { message = error.localizedDescription }
    }
    func createSkill(name: String, description: String) async {
        do {
            _ = try await backend.admin("admin/create-skill", ["name": name, "description": description])
            await loadResources(); await open(kind: "skill", path: name + "/SKILL.md")
            message = "Skill oluşturuldu / Skill created"
        } catch { message = error.localizedDescription }
    }
    /// The skill/extension that owns the selected file (top-level folder, or the single extension file).
    var selectedResource: AdminResource? {
        let list = selectedKind == "skill" ? resources?.skills : selectedKind == "extension" ? resources?.extensions : nil
        return list?.first { selectedPath == $0.name || selectedPath.hasPrefix($0.name + "/") }
    }
    /// Moves the whole skill/extension to the Trash (backend refuses built-ins), then clears the selection.
    func delete(_ resource: AdminResource) async {
        do {
            let data = try await backend.admin("admin/delete", ["kind": resource.kind, "name": resource.name])
            if selectedResource?.id == resource.id { selectedKind = ""; selectedPath = ""; content = ""; original = ""; loadError = "" }
            await loadResources()
            message = ((try JSONSerialization.jsonObject(with: data) as? [String: Any])?["note"] as? String) ?? "Silindi / Deleted"
        } catch { message = error.localizedDescription }
    }
    func createExtension(name: String) async {
        do {
            _ = try await backend.admin("admin/create-extension", ["name": name])
            await loadResources(); await open(kind: "extension", path: name + ".ts")
            message = "Extension oluşturuldu / Extension created"
        } catch { message = error.localizedDescription }
    }
}

struct AdminView: View {
    @Bindable var admin: AdminModel
    var body: some View {
        TabView {
            StatusTab(admin: admin).tabItem { Text("Durum / Status") }
            ActivityTab(admin: admin).tabItem { Text("Etkinlik / Activity") }
            SettingsTab(admin: admin, ring: admin.ring).tabItem { Text("Ayarlar / Settings") }
            ResourcesTab(admin: admin).tabItem { Text("Skill & Extension") }
        }.padding(12).frame(minWidth: 720, minHeight: 480)
    }
}

private struct StatusTab: View {
    let admin: AdminModel
    private func label(_ stage: String) -> String {
        [ "request_started": "başladı / started", "classification_started": "sınıflandırma / classification",
          "classification_finished": "sınıflandırma bitti / classified", "harness_ready": "harness hazır / harness ready",
          "skill_selected": "skill seçildi / skill selected", "model_started": "model yanıtlıyor / model running",
          "model_finished": "model bitti / model finished", "tool_started": "araç çalışıyor / tool running",
          "tool_finished": "araç bitti / tool finished", "local_fallback": "yerel yedek / local fallback",
          "local_load_started": "yerel model yükleniyor / loading local model", "local_load_finished": "yerel model hazır / local model ready",
          "speech_prepared": "seslendirme / speaking", "tts_fallback": "TTS yedek / TTS fallback", "stt_fallback": "STT yedek / STT fallback" ][stage] ?? stage
    }
    var body: some View {
        Form {
            if !admin.statusError.isEmpty { Text(admin.statusError).foregroundStyle(.orange) }
            Section("Backend") {
                if let s = admin.status {
                    LabeledContent("Durum / State", value: s.unloading ? "Model boşaltılıyor / Unloading" : s.busy ? "Meşgul / Busy" : "Hazır / Idle")
                    LabeledContent("PID / Port", value: "\(s.backend.pid) / \(s.backend.port)")
                    LabeledContent("Çalışma süresi / Uptime", value: Duration.seconds(s.backend.uptime_s).formatted(.time(pattern: .hourMinuteSecond)))
                    if let mb = s.backend.memory_mb { LabeledContent("Bellek / Memory", value: "\(mb) MB") }
                    LabeledContent("Arayüz / Ring", value: "\(admin.ring.phase.rawValue) • \(admin.ring.status)")
                    if s.requests.isEmpty { LabeledContent("Aktif istek / Active request", value: "Yok / None") }
                    ForEach(s.requests) { r in
                        LabeledContent("Aktif / Active (\(r.kind))", value: "\(label(r.stage)) • \(r.elapsed_ms / 1000) s")
                    }
                } else { Text("Bağlanıyor… / Connecting…").foregroundStyle(.secondary) }
            }
            if let s = admin.status {
                Section("Mod / Mode") {
                    LabeledContent("Mod / Mode", value: admin.ring.offline ? "Yerel / Local only" : "Remote (yerel yedek / local fallback)")
                    LabeledContent("Remote model", value: s.remote_model)
                    LabeledContent("Pi giriş / Pi login", value: s.pi_login.isEmpty ? "Yok / None" : s.pi_login.map { "\($0.provider) (\($0.type))" }.joined(separator: ", "))
                    LabeledContent("Jev", value: s.jev_enabled ? "Açık / On" : "Kapalı / Off")
                    LabeledContent("TTS", value: s.tts_engine)
                    LabeledContent("Geçmiş / History", value: "\(s.history_messages) mesaj / messages")
                }
                Section("Süreçler / Processes") {
                    ForEach(s.processes) { p in
                        LabeledContent(p.name, value: p.running
                            ? "Çalışıyor / Running • PID \(p.pid.map(String.init) ?? "?")" + (p.port.map { " • :\($0)" } ?? "") + (p.memory_mb.map { " • \($0) MB" } ?? "")
                            : "Durdu / Stopped" + (p.port.map { " • :\($0)" } ?? ""))
                    }
                    LabeledContent("Yerel model sağlığı / Local health", value: s.local_healthy ? "OK" : "Kapalı / Down")
                }
            }
            if let updated = admin.updated {
                Text("Güncellendi / Updated \(updated.formatted(date: .omitted, time: .standard))").font(.caption).foregroundStyle(.secondary)
            }
        }.formStyle(.grouped)
    }
}

private struct ActivityTab: View {
    let admin: AdminModel
    var body: some View {
        VStack(alignment: .leading) {
            HStack {
                Text("Son \(admin.activity.count) kayıt, yeni olan üstte / Latest entries, newest first").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Log dosyasını aç / Open log file…") { admin.actions.openActivityLog() }
            }
            Table(admin.activity) {
                TableColumn("Saat / Time", value: \.time).width(70)
                TableColumn("İstek / Request", value: \.request).width(75)
                TableColumn("Olay / Event", value: \.event).width(150)
                TableColumn("Ayrıntı / Details", value: \.details)
            }
        }
    }
}

private struct SettingsTab: View {
    @Bindable var admin: AdminModel
    @Bindable var ring: RingModel
    var body: some View {
        Form {
            Section("Etkileşim / Interaction") {
                LabeledContent("Kısayol / Shortcut") {
                    HStack { Text(ring.shortcutLabel).monospaced(); Button("Kısayolu değiştir / Change shortcut…") { admin.actions.changeShortcut() } }
                }
                LabeledContent("Talimatlar / Instructions") {
                    Button("Talimatları düzenle / Edit instructions…") { admin.actions.editInstructions() }
                }
            }
            Section("Mod / Mode") {
                Toggle("Yerel kullan / Local only", isOn: Binding(get: { ring.offline }, set: { ring.setOffline($0) }))
                    .disabled(ring.changingMode || ring.phase == .starting)
                Toggle("Sesli yanıt / Read aloud", isOn: $ring.readAloud)
                Toggle("Menü çubuğu simgesi / Menu bar icon", isOn: Binding(get: { admin.menuBarIcon }, set: { admin.actions.setMenuBarIcon($0) }))
            }
            Section("Uygulama / Application") {
                Button("Yeniden başlat / Restart Ayhan") { admin.actions.restart() }
            }
        }.formStyle(.grouped)
    }
}

private struct ResourcesTab: View {
    @Bindable var admin: AdminModel
    @State private var creatingSkill = false
    @State private var creatingExtension = false
    @State private var newName = ""
    @State private var newDescription = ""
    private var dirty: Bool { !admin.selectedPath.isEmpty && admin.loadError.isEmpty && admin.content != admin.original }
    /// Native List selection (tag = "kind|path"). Local @State drives the List; changes load the file via onChange.
    @State private var selectionKey: String?
    @State private var pendingKey: String?
    @State private var confirmDiscard = false
    @State private var deleting: AdminResource?
    private var currentKey: String? { admin.selectedPath.isEmpty ? nil : admin.selectedKind + "|" + admin.selectedPath }
    private func load(_ key: String) {
        guard let bar = key.firstIndex(of: "|") else { return }
        let kind = String(key[..<bar]), path = String(key[key.index(after: bar)...])
        Task { await admin.open(kind: kind, path: path) }
    }
    private func selectionChanged(_ key: String?) {
        guard let key, key != currentKey else { return }
        if dirty { pendingKey = key; confirmDiscard = true } else { load(key) }
    }

    var body: some View {
        HSplitView {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Button("Yeni skill / New skill…") { newName = ""; newDescription = ""; creatingSkill = true }
                    Button("Yeni extension…") { newName = ""; creatingExtension = true }
                }
                List(selection: $selectionKey) {
                    Section("Skills (.pi/skills)") { ForEach(admin.resources?.skills ?? []) { rows($0) } }
                    Section("Extensions (.pi/extensions)") { ForEach(admin.resources?.extensions ?? []) { rows($0) } }
                }
            }.frame(minWidth: 240, idealWidth: 280)
            VStack(alignment: .leading, spacing: 8) {
                if admin.selectedPath.isEmpty {
                    Text("Görmek veya düzenlemek için bir dosya seç / Select a file to view or edit").foregroundStyle(.secondary)
                    Spacer()
                } else {
                    Text("\(admin.selectedKind == "skill" ? ".pi/skills" : ".pi/extensions")/\(admin.selectedPath)").font(.headline).textSelection(.enabled)
                    if !admin.loadError.isEmpty {
                        Text("Dosya yüklenemedi / Could not load file: \(admin.loadError)").foregroundStyle(.red).textSelection(.enabled)
                        Spacer()
                    } else {
                        TextEditor(text: $admin.content).font(.system(.body, design: .monospaced))
                    }
                    HStack {
                        Spacer()
                        if let resource = admin.selectedResource {
                            Button("Sil / Delete…", role: .destructive) { deleting = resource }.disabled(resource.loading == "built-in")
                        }
                        Button("Geri al / Revert") { admin.content = admin.original }.disabled(!dirty)
                        Button("Kaydet / Save") { Task { await admin.save() } }.disabled(!dirty).keyboardShortcut("s", modifiers: .command)
                    }
                }
                if !admin.message.isEmpty { Text(admin.message).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
            }.padding(.leading, 8).frame(minWidth: 380)
        }
        .onChange(of: selectionKey) { _, key in selectionChanged(key) }
        // Keep the List in sync when the model selects a file itself (create skill/extension); equal keys stop the loop.
        .onChange(of: currentKey, initial: true) { _, key in if selectionKey != key { selectionKey = key } }
        .confirmationDialog("Kaydedilmemiş değişiklikler atılsın mı? / Discard unsaved changes?", isPresented: $confirmDiscard) {
            Button("Değişiklikleri at / Discard", role: .destructive) { if let key = pendingKey { load(key) }; pendingKey = nil }
            Button("İptal / Cancel", role: .cancel) { pendingKey = nil; selectionKey = currentKey }
        }
        .confirmationDialog("\(deleting?.name ?? "") silinsin mi? / Delete \(deleting?.name ?? "")?",
                            isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
            Button("Sil / Delete", role: .destructive) { if let resource = deleting { Task { await admin.delete(resource) } }; deleting = nil }
            Button("İptal / Cancel", role: .cancel) { deleting = nil }
        }
        .sheet(isPresented: $creatingSkill) {
            form(title: "Yeni skill / New skill", description: true) { Task { await admin.createSkill(name: newName, description: newDescription) } }
        }
        .sheet(isPresented: $creatingExtension) {
            form(title: "Yeni extension / New extension", description: false) { Task { await admin.createExtension(name: newName) } }
        }
    }

    @ViewBuilder private func rows(_ resource: AdminResource) -> some View {
        let header = VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(resource.name).bold()
                if resource.valid == false { Text("geçersiz / invalid").font(.caption).foregroundStyle(.orange) }
                if resource.loading == "built-in" { Text("yerleşik / built-in").font(.caption).foregroundStyle(.secondary) }
            }
            if !resource.description.isEmpty { Text(resource.description).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
        }
        if resource.files.count == 1, let file = resource.files.first {
            VStack(alignment: .leading, spacing: 2) {
                header
                Text(file).font(.caption.monospaced()).foregroundStyle(.secondary)
            }.tag(resource.kind + "|" + file).contextMenu { deleteMenu(resource) }
        } else {
            header.selectionDisabled().contextMenu { deleteMenu(resource) }
            ForEach(resource.files, id: \.self) { file in
                Text(file).font(.caption.monospaced()).padding(.leading, 8).tag(resource.kind + "|" + file).contextMenu { deleteMenu(resource) }
            }
        }
    }

    @ViewBuilder private func deleteMenu(_ resource: AdminResource) -> some View {
        Button("Sil / Delete…", role: .destructive) { deleting = resource }.disabled(resource.loading == "built-in")
    }

    private func form(title: String, description: Bool, create: @escaping () -> Void) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline)
            TextField("Ad (küçük harf, rakam, tire) / Name (lowercase, digits, hyphens)", text: $newName)
            if description { TextField("Kısa açıklama / Short description", text: $newDescription) }
            HStack {
                Spacer()
                Button("İptal / Cancel") { creatingSkill = false; creatingExtension = false }.keyboardShortcut(.cancelAction)
                Button("Oluştur / Create") { creatingSkill = false; creatingExtension = false; create() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(newName.isEmpty || (description && newDescription.trimmingCharacters(in: .whitespaces).isEmpty))
            }
        }.padding(16).frame(width: 420)
    }
}
