import AppKit
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

struct MailAccount: Codable, Identifiable, Equatable {
    var account: String; var mailbox: String; var enabled: Bool
    var id: String { account + "\u{1}" + mailbox.lowercased() }
}
struct MailAccounts: Decodable { let accounts: [MailAccount]; let available: [String]?; let available_error: String? }
struct SavedMailAccounts: Decodable { let accounts: [MailAccount] }

struct GmailAccount: Decodable, Identifiable { let email: String; let error: String; let missing_labels: [String]; var id: String { email } }
struct GmailStatus: Decodable {
    var client_configured = false; var connected = false; var accounts: [GmailAccount] = []; var connecting = false
    var triage_enabled = false; var urgent_senders: [String] = []; var error = ""
}
struct GmailBatch: Decodable, Identifiable {
    let batch: String; let at: Double; let kind: String; let count: Int; let undone: Bool; let levels: [String: Int]
    var id: String { batch }
}
struct GmailPlanItem: Decodable { let level: String; let from: String; let subject: String }
struct GmailPlan: Decodable { let at: Double; let counts: [String: Int]; let missing_labels: [String]; let items: [GmailPlanItem] }
struct GmailBatches: Decodable { let batches: [GmailBatch]; var last_plan: GmailPlan? }
struct FilesIndex: Decodable { let files: Int; let found: Int?; let chunks: Int; let running: Bool; let last_run: Double?; let multimodal: Bool; let last_error: String? }
struct FilesPlanGroup: Decodable { let folder: String; let existing: Bool; let files: [String] }
struct FilesPlan: Decodable { let at: Double; let folder: String; let groups: [FilesPlanGroup]; let left: [String] }
struct FilesStatus: Decodable { var enabled = false; var roots: [String] = []; var skip_dirs: [String]? = nil; var index: FilesIndex?; var last_plan: FilesPlan? }

struct VaultStatus: Decodable { var enabled = false; var server = ""; var email = ""; var installed = false; var state = "unknown"; var error = "" }
struct ActivityRow: Identifiable { let id: Int; let time: String; let request: String; let event: String; let details: String }

@MainActor @Observable
final class AdminModel {
    struct Actions {
        var changeShortcut: () -> Void = {}
        var changeDictationShortcut: () -> Void = {}
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
    var mailAccounts: [MailAccount] = []
    var mailAvailable: [String]?
    var mailAvailableError = ""
    var mailMessage = ""
    var remindersEnabled = false
    var tasksEnabled = false
    var tasksList = "Asistan"
    var gmail = GmailStatus()
    var gmailClientId = ""
    var gmailClientSecret = ""
    var gmailUrgent = ""
    var gmailImportance = ""
    var gmailImportanceOriginal = ""
    var gmailMessage = ""
    var pronunciations = ""
    var pronunciationsOriginal = ""
    var pronunciationsMessage = ""
    var gmailBatches: [GmailBatch] = []
    var gmailPlan: GmailPlan?
    var files = FilesStatus()
    var fileRoots = ""
    var fileRootsOriginal = ""
    var fileSkip = ""
    var fileSkipOriginal = ""
    var filesMessage = ""
    var vault = VaultStatus()
    var vaultEmail = ""
    var vaultPassword = ""
    var vaultCode = ""
    var vaultMessage = ""
    var vaultBusy = false
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
        Task { await loadMailAccounts() }
        Task { await loadReminders() }
        Task { await loadGmail(); await loadImportance(); await loadGmailActions() }
        Task { await loadPronunciations() }
        Task { await loadFiles() }
        Task { await loadVault() }
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
        do {
            resources = try JSONDecoder().decode(AdminResources.self, from: try await backend.admin("admin/resources"))
            if message == staleLoadMessage { message = "" }
            staleLoadMessage = ""
        } catch { message = error.localizedDescription; staleLoadMessage = message }
    }
    /// Last resources-load error, so a later successful load clears it without wiping save/create notes.
    @ObservationIgnored private var staleLoadMessage = ""
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
    func loadMailAccounts() async {
        do {
            let value = try JSONDecoder().decode(MailAccounts.self, from: try await backend.admin("admin/mail/accounts"))
            mailAccounts = value.accounts; mailAvailable = value.available
            mailAvailableError = switch value.available_error {
            case nil: ""
            case "automation_denied": "Mail için Otomasyon izni yok (Sistem Ayarları > Gizlilik ve Güvenlik > Otomasyon) / No Automation permission for Mail"
            case "mail_unavailable": "Mail açık değil / Mail is not running"
            default: "Mail hesapları okunamadı / Could not read Mail accounts"
            }
            mailMessage = ""
        } catch { mailMessage = error.localizedDescription }
    }
    /// Saves the whole list; on failure the list is reloaded from the backend.
    func saveMailAccounts(_ accounts: [MailAccount]) async {
        do {
            let list = accounts.map { ["account": $0.account, "mailbox": $0.mailbox, "enabled": $0.enabled] as [String: Any] }
            let data = try await backend.admin("admin/mail/save", json: ["accounts": list])
            mailAccounts = try JSONDecoder().decode(SavedMailAccounts.self, from: data).accounts
            mailMessage = ""
        } catch {
            let text = error.localizedDescription
            await loadMailAccounts(); mailMessage = text
        }
    }
    private func applyReminders(_ data: Data) {
        guard let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        if let on = value["enabled"] as? Bool { remindersEnabled = on }
        if let on = value["tasks_enabled"] as? Bool { tasksEnabled = on }
        if let list = value["tasks_list"] as? String { tasksList = list }
    }
    func loadReminders() async {
        if let data = try? await backend.admin("admin/reminders") { applyReminders(data) }
    }
    /// Off: the reminders tool is not offered to the model from the next request (assistant tasks stop too).
    func setReminders(_ enabled: Bool) async { remindersEnabled = enabled; await saveReminders(["enabled": enabled]) }
    func setTasks(_ enabled: Bool) async { tasksEnabled = enabled; await saveReminders(["tasks_enabled": enabled]) }
    func setTasksList(_ name: String) async { await saveReminders(["tasks_list": name]) }
    private func saveReminders(_ body: [String: Any]) async {
        do {
            let json = try JSONSerialization.data(withJSONObject: body)
            applyReminders(try await backend.admin("admin/reminders/save", data: json)); mailMessage = ""
        }
        catch { await loadReminders(); mailMessage = error.localizedDescription }
    }
    // MARK: Gmail (OAuth client in Keychain via the backend; the backend never returns secrets)
    private func applyGmail(_ data: Data) {
        guard let value = try? JSONDecoder().decode(GmailStatus.self, from: data) else { return }
        gmail = value; gmailUrgent = value.urgent_senders.joined(separator: ", ")
    }
    func loadGmail() async {
        do { applyGmail(try await backend.admin("admin/gmail")) } catch { gmailMessage = error.localizedDescription }
    }
    private func saveGmail(_ body: [String: Any]) async {
        do {
            let json = try JSONSerialization.data(withJSONObject: body)
            applyGmail(try await backend.admin("admin/gmail/save", data: json)); gmailMessage = ""
        }
        catch { gmailMessage = error.localizedDescription; await loadGmail() }
    }
    func saveGmailClient() async {
        await saveGmail(["client_id": gmailClientId, "client_secret": gmailClientSecret])
        if gmailMessage.isEmpty { gmailClientId = ""; gmailClientSecret = "" }
    }
    func setGmailTriage(_ on: Bool) async { gmail.triage_enabled = on; await saveGmail(["triage_enabled": on]) }
    func saveGmailUrgent() async {
        let list = gmailUrgent.split(whereSeparator: { $0 == "," || $0 == "\n" || $0 == " " }).map(String.init).filter { !$0.isEmpty }
        await saveGmail(["urgent_senders": list])
    }
    /// Adds an account: opens Google's consent page in the default browser; the backend's loopback listener completes it.
    func connectGmail() async {
        do {
            let data = try await backend.admin("admin/gmail/connect")
            guard let text = (try JSONSerialization.jsonObject(with: data) as? [String: Any])?["auth_url"] as? String,
                  let url = URL(string: text), url.scheme == "https" else { gmailMessage = "Geçersiz yanıt / Invalid response"; return }
            NSWorkspace.shared.open(url); gmailMessage = ""
            for _ in 0..<150 { // Up to 5 minutes, like the backend's consent timeout.
                try? await Task.sleep(for: .seconds(2))
                await loadGmail()
                if !gmail.connecting { break }
            }
            if !gmail.error.isEmpty { gmailMessage = gmail.error }
        } catch { gmailMessage = error.localizedDescription }
    }
    func disconnectGmail(_ email: String) async {
        do { applyGmail(try await backend.admin("admin/gmail/disconnect", ["account": email])); gmailMessage = "" } catch { gmailMessage = error.localizedDescription }
    }
    func loadImportance() async {
        guard let data = try? await backend.admin("admin/gmail/importance"),
              let text = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["content"] as? String else { return }
        gmailImportance = text; gmailImportanceOriginal = text
    }
    func saveImportance() async {
        do { _ = try await backend.admin("admin/gmail/importance/save", ["content": gmailImportance]); gmailImportanceOriginal = gmailImportance; gmailMessage = "" }
        catch { gmailMessage = error.localizedDescription }
    }
    func loadPronunciations() async {
        guard let data = try? await backend.admin("admin/pronunciations"),
              let text = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["content"] as? String else { return }
        pronunciations = text; pronunciationsOriginal = text
    }
    func savePronunciations() async {
        do { _ = try await backend.admin("admin/pronunciations/save", ["content": pronunciations]); pronunciationsOriginal = pronunciations; pronunciationsMessage = "" }
        catch { pronunciationsMessage = error.localizedDescription }
    }
    func loadGmailActions() async {
        if let data = try? await backend.admin("admin/gmail/actions"), let value = try? JSONDecoder().decode(GmailBatches.self, from: data) { gmailBatches = value.batches; gmailPlan = value.last_plan }
    }
    func undoGmail() async {
        do {
            let data = try await backend.admin("admin/gmail/undo")
            if let value = try? JSONDecoder().decode(GmailBatches.self, from: data) { gmailBatches = value.batches }
            gmailMessage = ""
        } catch { gmailMessage = error.localizedDescription }
    }

    // MARK: Local files (EmbeddingGemma index)
    private func applyFiles(_ data: Data, roots: Bool) {
        guard let value = try? JSONDecoder().decode(FilesStatus.self, from: data) else { return }
        files = value
        if roots {
            fileRoots = value.roots.joined(separator: "\n"); fileRootsOriginal = fileRoots
            fileSkip = (value.skip_dirs ?? []).joined(separator: "\n"); fileSkipOriginal = fileSkip
        }
    }
    func loadFiles(roots: Bool = true) async {
        do { applyFiles(try await backend.admin("admin/files"), roots: roots) } catch { filesMessage = error.localizedDescription }
    }
    private func saveFiles(_ body: [String: Any]) async {
        do {
            let json = try JSONSerialization.data(withJSONObject: body)
            applyFiles(try await backend.admin("admin/files/save", data: json), roots: true); filesMessage = ""
        }
        catch { filesMessage = error.localizedDescription; await loadFiles() }
    }
    func setFiles(_ on: Bool) async { files.enabled = on; await saveFiles(["enabled": on]) }
    func saveFileRoots() async {
        await saveFiles(["roots": fileRoots.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }])
    }
    func saveFileSkip() async {
        await saveFiles(["skip_dirs": fileSkip.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }])
    }
    func reindexFiles() async {
        do { applyFiles(try await backend.admin("admin/files/reindex"), roots: false); filesMessage = "" } catch { filesMessage = error.localizedDescription }
    }

    // MARK: Bitwarden (the master password lives only in this field until Unlock, then it is cleared)
    private func applyVault(_ data: Data, email: Bool = true) {
        guard let value = try? JSONDecoder().decode(VaultStatus.self, from: data) else { return }
        vault = value
        if email { vaultEmail = value.email }
    }
    static func vaultText(_ code: String) -> String {
        switch code {
        case "invalid_password": "Ana parola yanlış / Wrong master password"
        case "two_factor_required": "2FA kodu gerekli / 2FA code required"
        case "not_installed": "bw kurulu değil (scripts/install-bitwarden.sh) / bw not installed"
        case "server_unreachable": "Sunucuya ulaşılamıyor / Server unreachable"
        case "disabled": "Bitwarden kapalı / Bitwarden is off"
        default: code
        }
    }
    func loadVault() async {
        do { applyVault(try await backend.admin("admin/bitwarden")) } catch { vaultMessage = error.localizedDescription }
    }
    private func saveVault(_ body: [String: Any]) async {
        do {
            let json = try JSONSerialization.data(withJSONObject: body)
            applyVault(try await backend.admin("admin/bitwarden/save", data: json)); vaultMessage = ""
        }
        catch { vaultMessage = Self.vaultText(error.localizedDescription); await loadVault() }
    }
    func setVault(_ on: Bool) async { vault.enabled = on; await saveVault(["enabled": on]) }
    func saveVaultEmail() async { await saveVault(["email": vaultEmail]) }
    func unlockVault() async {
        let password = vaultPassword, code = vaultCode
        vaultPassword = ""; vaultBusy = true; defer { vaultBusy = false }
        do { applyVault(try await backend.admin("admin/bitwarden/unlock", ["password": password, "code": code]), email: false); vaultCode = ""; vaultMessage = "" }
        catch { vaultMessage = Self.vaultText(error.localizedDescription); await loadVault() }
    }
    func lockVault() async {
        do { applyVault(try await backend.admin("admin/bitwarden/lock"), email: false); vaultMessage = "" } catch { vaultMessage = error.localizedDescription }
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
          "speech_prepared": "seslendirme / speaking", "tts_fallback": "TTS yedek / TTS fallback", "stt_fallback": "STT yedek / STT fallback",
          "dictation": "dikte / dictation", "file_index_finished": "dosya dizini / file index" ][stage] ?? stage
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
            if !admin.gmailBatches.isEmpty {
                Section("Mail işlemleri / Mail actions") {
                    ForEach(admin.gmailBatches) { b in
                        LabeledContent(Date(timeIntervalSince1970: b.at / 1000).formatted(date: .abbreviated, time: .shortened),
                                       value: "\(b.kind == "trash" ? "Çöp / Trash" : "Düzenleme / Organize") • \(b.count)" +
                                              (b.levels.isEmpty ? "" : " • " + b.levels.sorted { $0.key < $1.key }.map { "\($0.key) \($0.value)" }.joined(separator: ", ")) +
                                              (b.undone ? " • geri alındı / undone" : ""))
                    }
                    HStack {
                        Spacer()
                        Button("Yenile / Refresh") { Task { await admin.loadGmailActions() } }
                        Button("Geri al / Undo") { Task { await admin.undoGmail() } }
                            .disabled(!admin.gmailBatches.contains { $0.kind == "apply" && !$0.undone })
                    }
                }
            }
            if let plan = admin.gmailPlan {
                Section("Son plan / Last plan") {
                    LabeledContent(Date(timeIntervalSince1970: plan.at / 1000).formatted(date: .abbreviated, time: .shortened),
                                   value: ["urgent", "important", "later", "junk"].map { "\(Self.levelMark($0)) \(plan.counts[$0] ?? 0)" }.joined(separator: "  "))
                    if !plan.missing_labels.isEmpty {
                        Text("Eksik etiket / Missing label: " + plan.missing_labels.joined(separator: ", ")).foregroundStyle(.orange)
                    }
                    DisclosureGroup("Mailler / Mails (\(plan.items.count))") {
                        ForEach(Array(plan.items.enumerated()), id: \.offset) { _, item in
                            HStack(alignment: .firstTextBaseline) {
                                Text(Self.levelMark(item.level))
                                Text(item.from).bold().lineLimit(1)
                                Text(item.subject).foregroundStyle(.secondary).lineLimit(1)
                            }
                        }
                    }
                }
            }
            if admin.files.enabled, let index = admin.files.index {
                Section("Dosyalar / Files") {
                    LabeledContent("Dizinlenen / Bulunan • Indexed / Found", value: "\(index.files) / \(index.found ?? index.files) • \(index.chunks) parça / chunks" + (index.multimodal ? "" : " • yalnız metin / text only"))
                    LabeledContent("Son tarama / Last run", value: index.running ? "Çalışıyor / Running"
                        : index.last_run.map { Date(timeIntervalSince1970: $0 / 1000).formatted(date: .abbreviated, time: .shortened) } ?? "Yok / None")
                    if let error = index.last_error { Text(error).font(.caption).foregroundStyle(.orange) }
                    HStack {
                        Spacer()
                        Button("Yenile / Refresh") { Task { await admin.loadFiles(roots: false) } }
                        Button("Yeniden dizinle / Reindex") { Task { await admin.reindexFiles() } }.disabled(index.running)
                    }
                }
            }
            if let plan = admin.files.last_plan {
                Section("Son dosya planı / Last file plan") {
                    LabeledContent(Date(timeIntervalSince1970: plan.at / 1000).formatted(date: .abbreviated, time: .shortened), value: plan.folder)
                    ForEach(Array(plan.groups.enumerated()), id: \.offset) { _, group in
                        DisclosureGroup("\(group.folder)\(group.existing ? "" : " (yeni / new)") • \(group.files.count)") {
                            ForEach(Array(group.files.enumerated()), id: \.offset) { _, name in Text(name).lineLimit(1).truncationMode(.middle) }
                        }
                    }
                    if !plan.left.isEmpty {
                        DisclosureGroup("Yerinde kalan / Left in place • \(plan.left.count)") {
                            ForEach(Array(plan.left.enumerated()), id: \.offset) { _, name in Text(name).lineLimit(1).truncationMode(.middle) }
                        }
                    }
                }
            }
            if let updated = admin.updated {
                Text("Güncellendi / Updated \(updated.formatted(date: .omitted, time: .standard))").font(.caption).foregroundStyle(.secondary)
            }
        }.formStyle(.grouped)
        .task { await admin.loadGmailActions(); await admin.loadFiles(roots: false) }
    }
}

extension StatusTab {
    static func levelMark(_ level: String) -> String {
        switch level { case "urgent": "🔴"; case "important": "🟠"; case "later": "🔵"; default: "⚪️" }
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
    @State private var removing: MailAccount?
    var body: some View {
        Form {
            Section("Etkileşim / Interaction") {
                LabeledContent("Kısayol / Shortcut") {
                    HStack { Text(ring.shortcutLabel).monospaced(); Button("Kısayolu değiştir / Change shortcut…") { admin.actions.changeShortcut() } }
                }
                LabeledContent("Dikte kısayolu / Dictation shortcut") {
                    HStack { Text(ring.dictationLabel).monospaced(); Button("Değiştir / Change…") { admin.actions.changeDictationShortcut() } }
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
                Toggle("Reminders", isOn: Binding(get: { admin.remindersEnabled }, set: { on in Task { await admin.setReminders(on) } }))
                Toggle("Asistan görevleri / Assistant tasks", isOn: Binding(get: { admin.tasksEnabled }, set: { on in Task { await admin.setTasks(on) } }))
                    .disabled(!admin.remindersEnabled)
                TextField("Görev listesi / Task list", text: $admin.tasksList)
                    .onSubmit { Task { await admin.setTasksList(admin.tasksList) } }
                    .disabled(!admin.remindersEnabled || !admin.tasksEnabled)
            }
            MailAccountsSection(admin: admin, removing: $removing)
            GmailSection(admin: admin)
            Section("Dosyalar / Files") {
                Toggle("Dosya arama / File search", isOn: Binding(get: { admin.files.enabled }, set: { on in Task { await admin.setFiles(on) } }))
                TextEditor(text: $admin.fileRoots).font(.system(.body, design: .monospaced)).frame(minHeight: 60)
                HStack {
                    if !admin.filesMessage.isEmpty { Text(admin.filesMessage).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
                    Spacer()
                    Button("Kaydet / Save") { Task { await admin.saveFileRoots() } }.disabled(admin.fileRoots == admin.fileRootsOriginal)
                }
                Text("Atlanan klasörler / Skipped folders").font(.caption)
                TextEditor(text: $admin.fileSkip).font(.system(.body, design: .monospaced)).frame(minHeight: 60)
                HStack {
                    Spacer()
                    Button("Kaydet / Save") { Task { await admin.saveFileSkip() } }.disabled(admin.fileSkip == admin.fileSkipOriginal)
                }
            }
            Section("Bitwarden") {
                Toggle("Bitwarden", isOn: Binding(get: { admin.vault.enabled }, set: { on in Task { await admin.setVault(on) } }))
                LabeledContent("Durum / Status", value: "\(admin.vault.state == "unlocked" ? "Açık / Unlocked" : admin.vault.state == "unauthenticated" ? "Giriş yok / Logged out" : admin.vault.state == "locked" ? "Kilitli / Locked" : admin.vault.state) • \(admin.vault.server)")
                TextField("E-posta / Email", text: $admin.vaultEmail).textContentType(.username)
                    .onSubmit { Task { await admin.saveVaultEmail() } }
                SecureField("Ana parola / Master password", text: $admin.vaultPassword)
                    .onSubmit { Task { await admin.unlockVault() } }
                if admin.vault.state == "unauthenticated" { TextField("2FA kodu / 2FA code", text: $admin.vaultCode) }
                HStack {
                    if !admin.vaultMessage.isEmpty { Text(admin.vaultMessage).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
                    Spacer()
                    if admin.vaultEmail != admin.vault.email { Button("Kaydet / Save") { Task { await admin.saveVaultEmail() } } }
                    Button("Kilidi aç / Unlock") { Task { await admin.unlockVault() } }
                        .disabled(!admin.vault.enabled || admin.vaultPassword.isEmpty || admin.vaultBusy || admin.vaultEmail != admin.vault.email)
                    Button("Kilitle / Lock") { Task { await admin.lockVault() } }.disabled(admin.vault.state != "unlocked")
                }
            }
            Section("Okunuşlar / Pronunciations") {
                TextEditor(text: $admin.pronunciations).font(.system(.body, design: .monospaced)).frame(minHeight: 120)
                HStack {
                    if !admin.pronunciationsMessage.isEmpty { Text(admin.pronunciationsMessage).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
                    Spacer()
                    Button("Kaydet / Save") { Task { await admin.savePronunciations() } }.disabled(admin.pronunciations == admin.pronunciationsOriginal)
                }
            }
            Section("Uygulama / Application") {
                Button("Yeniden başlat / Restart Ayhan") { admin.actions.restart() }
            }
        }.formStyle(.grouped)
        .confirmationDialog("\(removing?.account ?? "") kaldırılsın mı? / Remove?",
                            isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } })) {
            Button("Kaldır / Remove", role: .destructive) {
                if let account = removing { Task { await admin.saveMailAccounts(admin.mailAccounts.filter { $0.id != account.id }) } }
                removing = nil
            }
            Button("İptal / Cancel", role: .cancel) { removing = nil }
        }
    }
}

/// Mail accounts the read-only Mail tools may use. Toggle and remove save immediately.
private struct MailAccountsSection: View {
    @Bindable var admin: AdminModel
    @Binding var removing: MailAccount?
    private static let manual = "\u{0}manual"
    private static let buttonWidth: CGFloat = 110
    @State private var choice = ""
    @State private var manualName = ""
    @State private var mailbox = ""
    private var choices: [String] {
        let added = Set(admin.mailAccounts.map(\.account))
        return (admin.mailAvailable ?? []).filter { !added.contains($0) }
    }
    private var name: String {
        (choice == Self.manual || admin.mailAvailable == nil ? manualName : choice).trimmingCharacters(in: .whitespaces)
    }
    private var box: String { mailbox.trimmingCharacters(in: .whitespaces).isEmpty ? "INBOX" : mailbox.trimmingCharacters(in: .whitespaces) }
    private var canAdd: Bool {
        !name.isEmpty && admin.mailAccounts.count < 10 && !admin.mailAccounts.contains { $0.account == name && $0.mailbox.lowercased() == box.lowercased() }
    }

    var body: some View {
        Section("Mail hesapları / Mail accounts") {
            ForEach(admin.mailAccounts) { account in
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(account.account).lineLimit(1).truncationMode(.middle)
                        Text(account.mailbox).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                    Spacer(minLength: 8)
                    Toggle("Aktif / Active", isOn: Binding(get: { account.enabled }, set: { on in
                        let list = admin.mailAccounts.map { $0.id == account.id ? MailAccount(account: $0.account, mailbox: $0.mailbox, enabled: on) : $0 }
                        admin.mailAccounts = list
                        Task { await admin.saveMailAccounts(list) }
                    })).labelsHidden().toggleStyle(.switch)
                    Button(role: .destructive) { removing = account } label: {
                        Text("Kaldır / Remove").frame(width: Self.buttonWidth)
                    }
                }
            }
            HStack(spacing: 8) {
                if admin.mailAvailable != nil {
                    Picker("Hesap / Account", selection: $choice) {
                        Text("Hesap seç / Choose account").tag("")
                        ForEach(choices, id: \.self) { Text($0).tag($0) }
                        Text("Elle gir / Enter manually").tag(Self.manual)
                    }.labelsHidden().frame(minWidth: 140, maxWidth: 220)
                }
                if admin.mailAvailable == nil || choice == Self.manual {
                    TextField("Hesap adı / Account name", text: $manualName).frame(minWidth: 120, maxWidth: 200)
                }
                Spacer(minLength: 8)
                TextField("Mailbox", text: $mailbox, prompt: Text("INBOX")).labelsHidden().frame(width: 100)
                Button("Ekle / Add") {
                    let list = admin.mailAccounts + [MailAccount(account: name, mailbox: box, enabled: true)]
                    choice = ""; manualName = ""; mailbox = ""
                    Task { await admin.saveMailAccounts(list) }
                }.disabled(!canAdd)
                Button { Task { await admin.loadMailAccounts() } } label: {
                    Text("Yenile / Refresh").frame(width: Self.buttonWidth)
                }
            }
            if !admin.mailAvailableError.isEmpty { Text(admin.mailAvailableError).font(.caption).foregroundStyle(.orange) }
            if !admin.mailMessage.isEmpty { Text(admin.mailMessage).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
        }
    }
}

/// Gmail connection and triage settings. No explanatory text: fields, buttons and one-line warnings only.
private struct GmailSection: View {
    @Bindable var admin: AdminModel
    var body: some View {
        Section("Gmail") {
            TextField("Client ID", text: $admin.gmailClientId, prompt: Text(admin.gmail.client_configured ? "Kayıtlı / Saved" : ""))
            HStack {
                SecureField("Client secret", text: $admin.gmailClientSecret)
                Button("Kaydet / Save") { Task { await admin.saveGmailClient() } }
                    .disabled(admin.gmailClientId.isEmpty || admin.gmailClientSecret.isEmpty)
            }
            ForEach(admin.gmail.accounts) { account in
                VStack(alignment: .leading, spacing: 2) {
                    HStack {
                        Text(account.email)
                        Spacer()
                        Button("Bağlantıyı kes / Disconnect") { Task { await admin.disconnectGmail(account.email) } }
                    }
                    if account.error == "gmail_auth_expired" {
                        Text("Yeniden bağlan / Reconnect").font(.caption).foregroundStyle(.red)
                    } else if !account.error.isEmpty {
                        Text(account.error).font(.caption).foregroundStyle(.red)
                    }
                    if !account.missing_labels.isEmpty {
                        Text("Eksik etiket / Missing label: \(account.missing_labels.joined(separator: ", "))").font(.caption).foregroundStyle(.orange)
                    }
                }
            }
            HStack {
                if admin.gmail.connecting { Text("Tarayıcıda onay bekleniyor / Waiting for browser…").foregroundStyle(.secondary) }
                Spacer()
                Button("Hesap ekle / Add account") { Task { await admin.connectGmail() } }.disabled(!admin.gmail.client_configured || admin.gmail.connecting)
            }
            Toggle("Mail düzenleme / Mail organizing", isOn: Binding(get: { admin.gmail.triage_enabled }, set: { on in Task { await admin.setGmailTriage(on) } }))
                .disabled(!admin.gmail.connected)
            TextField("Acil gönderenler / Urgent senders", text: $admin.gmailUrgent, prompt: Text("boss@work.com, @*.gov.uk"))
                .onSubmit { Task { await admin.saveGmailUrgent() } }
            VStack(alignment: .leading) {
                Text("Önem tanımı / Importance").font(.caption).foregroundStyle(.secondary)
                TextEditor(text: $admin.gmailImportance).font(.system(.body, design: .monospaced)).frame(minHeight: 90)
                HStack {
                    Spacer()
                    Button("Kaydet / Save") { Task { await admin.saveImportance() } }.disabled(admin.gmailImportance == admin.gmailImportanceOriginal)
                }
            }
            if !admin.gmailMessage.isEmpty { Text(admin.gmailMessage).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
        }
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
