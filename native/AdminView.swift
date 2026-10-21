import AppKit
import SwiftUI
import Observation

/// Native "Yönetim" (Admin) window: read-only status, settings moved from the context menu,
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

struct MemoryItem: Decodable, Identifiable { let id: Int; let kind: String; let text: String; let source: String; let pinned: Bool }
struct MemoryEpisode: Decodable, Identifiable { let id: Int; let ended: Double; let summary: String }
struct MemoryTopic: Decodable, Identifiable { let id: Int; let title: String; let state: String; let decisions: [String]; let open: [String]; let status: String; let active: Bool }
struct MemoryStatus: Decodable { var enabled = true; var extract = true; var items: [MemoryItem] = []; var episodes: [MemoryEpisode] = []; var topics: [MemoryTopic] = [] }

struct TaskRow: Decodable, Identifiable {
    let id: Int; let name: String; let schedule: String; let next: String; let status: String
    let last: String?; let last_status: String?; let last_run: String?; let failures: Int?
}
struct TasksStatus: Decodable { var enabled = false; var tasks: [TaskRow] = [] }

struct RemoteModels: Decodable { var model = ""; var provider = ""; var models: [String] = []; var error = "" }
struct RemoteModelTest: Decodable { let ok: Bool; let model: String?; let error: String? }
struct VaultStatus: Decodable { var enabled = false; var server = ""; var email = ""; var installed = false; var state = "unknown"; var error = "" }
struct UpdateItem: Decodable, Identifiable {
    let id: String; let name: String; let current: String; let latest: String; let status: String
    let summary: String?; let breaking: Bool?; let security: Bool?; let affects_our_flags: Bool?; let size_mb: Int?
}
struct UpdateRollback: Decodable, Identifiable { let id: String; let name: String; let version: String }
struct UpdateRunItem: Decodable, Identifiable { let id: String; let name: String; let from: String; let to: String; let status: String; let steps: [String]; let reason: String? }
struct UpdateRun: Decodable { let id: String; let phase: String; let components: [UpdateRunItem] }
struct UpdatesStatus: Decodable {
    var last_check: Double? = nil; var last_error = ""; var checking = false; var applying: String? = nil; var queued: [String] = []
    var manifest_changed = false; var updates: [UpdateItem] = []; var rollback: [UpdateRollback] = []; var last_run: UpdateRun? = nil
}
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
    var remindersMessage = ""
    var remindersEnabled = false
    var tasksEnabled = false
    var tasksList = "Asistan"
    var announceChime = true
    var announceSnooze = 30
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
    var updates = UpdatesStatus()
    var updatesMessage = ""
    var memory = MemoryStatus()
    var memoryMessage = ""
    var tasks = TasksStatus()
    var tasksMessage = ""
    var heartbeatEnabled = false
    var heartbeatInterval = 30
    var heartbeatStart = 8
    var heartbeatEnd = 23
    var heartbeatContent = ""
    var heartbeatOriginal = ""
    var heartbeatMessage = ""
    var remote = RemoteModels()
    var remoteMessage = ""
    var remoteTesting = false
    private var polling: Task<Void, Never>?

    init(backend: BackendClient, ring: RingModel) { self.backend = backend; self.ring = ring }

    func startPolling() {
        polling?.cancel()
        polling = Task {
            while !Task.isCancelled {
                await refreshStatus()
                if updates.checking || updates.applying != nil || !updates.queued.isEmpty { await loadUpdates() }
                try? await Task.sleep(for: .seconds(3)) // Intentional status refresh cadence.
            }
        }
        Task { await loadResources() }
        Task { await loadReminders() }
        Task { await loadRemoteModels() }
        Task { await loadGmail(); await loadImportance(); await loadGmailActions() }
        Task { await loadPronunciations() }
        Task { await loadFiles() }
        Task { await loadVault() }
        Task { await loadUpdates() }
        Task { await loadMemory() }
        Task { await loadTasks() }
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
                loadError = "Geçersiz yanıt"; return
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
            message = ((try JSONSerialization.jsonObject(with: data) as? [String: Any])?["note"] as? String) ?? "Kaydedildi"
            await loadResources()
        } catch { message = error.localizedDescription }
    }
    func createSkill(name: String, description: String) async {
        do {
            _ = try await backend.admin("admin/create-skill", ["name": name, "description": description])
            await loadResources(); await open(kind: "skill", path: name + "/SKILL.md")
            message = "Skill oluşturuldu"
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
            message = ((try JSONSerialization.jsonObject(with: data) as? [String: Any])?["note"] as? String) ?? "Silindi"
        } catch { message = error.localizedDescription }
    }
    private func applyReminders(_ data: Data) {
        guard let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        if let on = value["enabled"] as? Bool { remindersEnabled = on }
        if let on = value["tasks_enabled"] as? Bool { tasksEnabled = on }
        if let list = value["tasks_list"] as? String { tasksList = list }
        if let on = value["announce_chime"] as? Bool { announceChime = on }
        if let minutes = value["announce_snooze_minutes"] as? Int { announceSnooze = minutes }
    }
    func loadReminders() async {
        if let data = try? await backend.admin("admin/reminders") { applyReminders(data) }
    }
    /// Off: the reminders tool is not offered to the model from the next request (assistant tasks keep running; own store).
    func setReminders(_ enabled: Bool) async { remindersEnabled = enabled; await saveReminders(["enabled": enabled]) }
    func setTasks(_ enabled: Bool) async { tasksEnabled = enabled; await saveReminders(["tasks_enabled": enabled]) }
    func setTasksList(_ name: String) async { await saveReminders(["tasks_list": name]) }
    func setAnnounce(chime: Bool? = nil, snooze: Int? = nil) async {
        if let chime { announceChime = chime }
        if let snooze { announceSnooze = snooze }
        await saveReminders(["announce_chime": announceChime, "announce_snooze_minutes": announceSnooze])
    }
    private func saveReminders(_ body: [String: Any]) async {
        do {
            let json = try JSONSerialization.data(withJSONObject: body)
            applyReminders(try await backend.admin("admin/reminders/save", data: json)); remindersMessage = ""
        }
        catch { await loadReminders(); remindersMessage = error.localizedDescription }
    }
    // MARK: Remote model ("" = follow global Pi settings; applies from the next request)
    func loadRemoteModels() async {
        do { remote = try JSONDecoder().decode(RemoteModels.self, from: try await backend.admin("admin/remote-model")); remoteMessage = remote.error }
        catch { remoteMessage = error.localizedDescription }
    }
    func setRemoteModel(_ model: String) async {
        remote.model = model; remoteMessage = ""
        do {
            let json = try JSONSerialization.data(withJSONObject: ["model": model])
            remote = try JSONDecoder().decode(RemoteModels.self, from: try await backend.admin("admin/remote-model/save", data: json))
        } catch { remoteMessage = error.localizedDescription; await loadRemoteModels() }
    }
    func testRemoteModel() async {
        remoteTesting = true; remoteMessage = ""
        defer { remoteTesting = false }
        do {
            let json = try JSONSerialization.data(withJSONObject: ["model": remote.model])
            let result = try JSONDecoder().decode(RemoteModelTest.self, from: try await backend.admin("admin/remote-model/test", data: json))
            remoteMessage = result.ok ? "✓ \(result.model ?? "")" : (result.error ?? "✗")
        } catch { remoteMessage = error.localizedDescription }
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
                  let url = URL(string: text), url.scheme == "https" else { gmailMessage = "Geçersiz yanıt"; return }
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
        case "invalid_password": "Ana parola yanlış"
        case "two_factor_required": "2FA kodu gerekli"
        case "not_installed": "bw kurulu değil (scripts/install-bitwarden.sh)"
        case "server_unreachable": "Sunucuya ulaşılamıyor"
        case "disabled": "Bitwarden kapalı"
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

    // MARK: Component updates
    func loadUpdates() async {
        do { updates = try JSONDecoder().decode(UpdatesStatus.self, from: try await backend.admin("admin/updates")); updatesMessage = "" }
        catch { updatesMessage = error.localizedDescription }
    }
    private func updatesCall(_ route: String, _ body: [String: Any] = [:]) async {
        do {
            let json = try JSONSerialization.data(withJSONObject: body)
            let data = try await backend.admin(route, data: json)
            if let value = try? JSONDecoder().decode(UpdatesStatus.self, from: data) { updates = value }
            updatesMessage = ""
        } catch { updatesMessage = error.localizedDescription }
    }
    private func memoryCall(_ route: String, _ body: [String: Any] = [:]) async {
        do {
            let data = try await backend.admin(route, data: try JSONSerialization.data(withJSONObject: body))
            if let value = try? JSONDecoder().decode(MemoryStatus.self, from: data) { memory = value }
            memoryMessage = ""
        } catch { memoryMessage = error.localizedDescription }
    }
    func loadMemory() async { await memoryCall("admin/memory") }
    func setMemory(enabled: Bool? = nil, extract: Bool? = nil) async {
        var body: [String: Any] = [:]
        if let enabled { body["enabled"] = enabled }
        if let extract { body["extract"] = extract }
        await memoryCall("admin/memory/settings", body)
    }
    func addMemory(kind: String, text: String) async { await memoryCall("admin/memory/save", ["kind": kind, "text": text]) }
    func editMemory(_ id: Int, text: String) async { await memoryCall("admin/memory/save", ["id": id, "text": text]) }
    func pinMemory(_ id: Int, _ pinned: Bool) async { await memoryCall("admin/memory/save", ["id": id, "pinned": pinned]) }
    func deleteMemory(_ id: Int) async { await memoryCall("admin/memory/delete", ["id": id]) }
    func deleteEpisode(_ id: Int) async { await memoryCall("admin/memory/delete", ["episode": id]) }
    func clearMemory(_ kind: String) async { await memoryCall("admin/memory/delete", ["all": kind]) }
    func saveTopic(_ id: Int, title: String, state: String, decisions: [String], open: [String]) async {
        await memoryCall("admin/memory/topic", ["id": id, "title": title, "state": state, "decisions": decisions, "open": open])
    }
    func activateTopic(_ id: Int, _ active: Bool) async { await memoryCall("admin/memory/topic", ["id": id, "active": active]) }
    func deleteTopic(_ id: Int) async { await memoryCall("admin/memory/delete", ["topic": id]) }

    // MARK: Assistant tasks
    private func tasksCall(_ route: String, _ body: [String: Any] = [:]) async {
        do {
            let data = try await backend.admin(route, data: try JSONSerialization.data(withJSONObject: body))
            tasks = try JSONDecoder().decode(TasksStatus.self, from: data); tasksMessage = ""
        } catch { tasksMessage = error.localizedDescription }
    }
    func loadTasks() async { await tasksCall("admin/tasks") }

    // MARK: Heartbeat (background checklist, off by default)
    private func heartbeatCall(_ route: String, _ body: [String: Any] = [:]) async {
        do {
            let data = try await backend.admin(route, data: try JSONSerialization.data(withJSONObject: body))
            guard let value = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
            if let on = value["enabled"] as? Bool { heartbeatEnabled = on }
            if let minutes = value["interval_minutes"] as? Int { heartbeatInterval = minutes }
            if let hour = value["start_hour"] as? Int { heartbeatStart = hour }
            if let hour = value["end_hour"] as? Int { heartbeatEnd = hour }
            if let text = value["content"] as? String { heartbeatContent = text; heartbeatOriginal = text }
            heartbeatMessage = ""
        } catch { heartbeatMessage = error.localizedDescription }
    }
    func loadHeartbeat() async { await heartbeatCall("admin/heartbeat") }
    func saveHeartbeat(_ body: [String: Any]) async { await heartbeatCall("admin/heartbeat/save", body) }
    func saveHeartbeatChecklist() async { await heartbeatCall("admin/heartbeat/save", ["content": heartbeatContent]) }
    func taskAction(_ id: Int, _ action: String) async { await tasksCall("admin/tasks/action", ["id": id, "action": action]) }

    func checkUpdates() async { await updatesCall("admin/updates/check") }
    func applyUpdate(_ id: String) async { await updatesCall("admin/updates/apply", ["id": id]) }
    func skipUpdate(_ item: UpdateItem) async { await updatesCall("admin/updates/skip", ["id": item.id, "version": item.latest]) }
    func rollbackUpdate(_ id: String) async { await updatesCall("admin/updates/rollback", ["id": id]) }

    func createExtension(name: String) async {
        do {
            _ = try await backend.admin("admin/create-extension", ["name": name])
            await loadResources(); await open(kind: "extension", path: name + ".ts")
            message = "Extension oluşturuldu"
        } catch { message = error.localizedDescription }
    }
}

struct AdminView: View {
    @Bindable var admin: AdminModel
    var body: some View {
        TabView {
            StatusTab(admin: admin).tabItem { Text("Durum") }
            SettingsTab(admin: admin, ring: admin.ring).tabItem { Text("Ayarlar") }
            MemoryTab(admin: admin).tabItem { Text("Hafıza") }
            TasksTab(admin: admin).tabItem { Text("Görevler") }
            UpdatesTab(admin: admin).tabItem { Text("Güncellemeler") }
            ResourcesTab(admin: admin).tabItem { Text("Skill & Extension") }
        }.padding(12).frame(minWidth: 720, minHeight: 480)
    }
}

private struct StatusTab: View {
    let admin: AdminModel
    private func label(_ stage: String) -> String {
        [ "request_started": "başladı", "classification_started": "sınıflandırma",
          "classification_finished": "sınıflandırma bitti", "harness_ready": "harness hazır",
          "skill_selected": "skill seçildi", "model_started": "model yanıtlıyor",
          "model_finished": "model bitti", "tool_started": "araç çalışıyor",
          "tool_finished": "araç bitti", "local_fallback": "yerel yedek",
          "local_load_started": "yerel model yükleniyor", "local_load_finished": "yerel model hazır",
          "speech_prepared": "seslendirme", "tts_fallback": "TTS yedek", "stt_fallback": "STT yedek",
          "dictation": "dikte", "file_index_finished": "dosya dizini" ][stage] ?? stage
    }
    var body: some View {
        Form {
            if !admin.statusError.isEmpty { Text(admin.statusError).foregroundStyle(.orange) }
            Section("Backend") {
                if let s = admin.status {
                    LabeledContent("Durum", value: s.unloading ? "Model boşaltılıyor" : s.busy ? "Meşgul" : "Hazır")
                    LabeledContent("PID / Port", value: "\(s.backend.pid) / \(s.backend.port)")
                    LabeledContent("Çalışma süresi", value: Duration.seconds(s.backend.uptime_s).formatted(.time(pattern: .hourMinuteSecond)))
                    if let mb = s.backend.memory_mb { LabeledContent("Bellek", value: "\(mb) MB") }
                    LabeledContent("Arayüz", value: "\(admin.ring.phase.rawValue) • \(admin.ring.status)")
                    if s.requests.isEmpty { LabeledContent("Aktif istek", value: "Yok") }
                    ForEach(s.requests) { r in
                        LabeledContent("Aktif (\(r.kind))", value: "\(label(r.stage)) • \(r.elapsed_ms / 1000) s")
                    }
                } else { Text("Bağlanıyor…").foregroundStyle(.secondary) }
            }
            if let s = admin.status {
                Section("Mod") {
                    LabeledContent("Mod", value: admin.ring.offline ? "Yalnız yerel" : "Uzak (yerel yedek)")
                    LabeledContent("Uzak model", value: s.remote_model)
                    LabeledContent("Pi giriş", value: s.pi_login.isEmpty ? "Yok" : s.pi_login.map { "\($0.provider) (\($0.type))" }.joined(separator: ", "))
                    LabeledContent("Jev", value: s.jev_enabled ? "Açık" : "Kapalı")
                    LabeledContent("TTS", value: s.tts_engine)
                    LabeledContent("Geçmiş", value: "\(s.history_messages) mesaj")
                }
                Section("Süreçler") {
                    ForEach(s.processes) { p in
                        LabeledContent(p.name, value: p.running
                            ? "Çalışıyor • PID \(p.pid.map(String.init) ?? "?")" + (p.port.map { " • :\($0)" } ?? "") + (p.memory_mb.map { " • \($0) MB" } ?? "")
                            : "Durdu" + (p.port.map { " • :\($0)" } ?? ""))
                    }
                    LabeledContent("Yerel model sağlığı", value: s.local_healthy ? "OK" : "Kapalı")
                }
            }
            if !admin.gmailBatches.isEmpty {
                Section("Mail işlemleri") {
                    ForEach(admin.gmailBatches) { b in
                        LabeledContent(Date(timeIntervalSince1970: b.at / 1000).formatted(date: .abbreviated, time: .shortened),
                                       value: "\(b.kind == "trash" ? "Çöp" : "Düzenleme") • \(b.count)" +
                                              (b.levels.isEmpty ? "" : " • " + b.levels.sorted { $0.key < $1.key }.map { "\($0.key) \($0.value)" }.joined(separator: ", ")) +
                                              (b.undone ? " • geri alındı" : ""))
                    }
                    HStack {
                        Spacer()
                        Button("Yenile") { Task { await admin.loadGmailActions() } }
                        Button("Geri al") { Task { await admin.undoGmail() } }
                            .disabled(!admin.gmailBatches.contains { $0.kind == "apply" && !$0.undone })
                    }
                }
            }
            if let plan = admin.gmailPlan {
                Section("Son plan") {
                    LabeledContent(Date(timeIntervalSince1970: plan.at / 1000).formatted(date: .abbreviated, time: .shortened),
                                   value: ["urgent", "important", "later", "junk"].map { "\(Self.levelMark($0)) \(plan.counts[$0] ?? 0)" }.joined(separator: "  "))
                    if !plan.missing_labels.isEmpty {
                        Text("Eksik etiket: " + plan.missing_labels.joined(separator: ", ")).foregroundStyle(.orange)
                    }
                    DisclosureGroup("Mailler (\(plan.items.count))") {
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
                Section("Dosyalar") {
                    LabeledContent("Dizinlenen / Bulunan", value: "\(index.files) / \(index.found ?? index.files) • \(index.chunks) parça" + (index.multimodal ? "" : " • yalnız metin"))
                    LabeledContent("Son tarama", value: index.running ? "Çalışıyor"
                        : index.last_run.map { Date(timeIntervalSince1970: $0 / 1000).formatted(date: .abbreviated, time: .shortened) } ?? "Yok")
                    if let error = index.last_error { Text(error).font(.caption).foregroundStyle(.orange) }
                    HStack {
                        Spacer()
                        Button("Yenile") { Task { await admin.loadFiles(roots: false) } }
                        Button("Yeniden dizinle") { Task { await admin.reindexFiles() } }.disabled(index.running)
                    }
                }
            }
            if let plan = admin.files.last_plan {
                Section("Son dosya planı") {
                    LabeledContent(Date(timeIntervalSince1970: plan.at / 1000).formatted(date: .abbreviated, time: .shortened), value: plan.folder)
                    ForEach(Array(plan.groups.enumerated()), id: \.offset) { _, group in
                        DisclosureGroup("\(group.folder)\(group.existing ? "" : " (yeni)") • \(group.files.count)") {
                            ForEach(Array(group.files.enumerated()), id: \.offset) { _, name in Text(name).lineLimit(1).truncationMode(.middle) }
                        }
                    }
                    if !plan.left.isEmpty {
                        DisclosureGroup("Yerinde kalan • \(plan.left.count)") {
                            ForEach(Array(plan.left.enumerated()), id: \.offset) { _, name in Text(name).lineLimit(1).truncationMode(.middle) }
                        }
                    }
                }
            }
            ActivitySection(admin: admin)
            if let updated = admin.updated {
                Text("Güncellendi \(updated.formatted(date: .omitted, time: .standard))").font(.caption).foregroundStyle(.secondary)
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

private struct UpdatesTab: View {
    let admin: AdminModel
    @State private var confirming: UpdateItem?
    private var busy: Bool { admin.updates.applying != nil || !admin.updates.queued.isEmpty }
    private func lastCheck(_ ms: Double?) -> String {
        ms.map { Date(timeIntervalSince1970: $0 / 1000).formatted(date: .abbreviated, time: .shortened) } ?? "—"
    }
    var body: some View {
        Form {
            Section {
                LabeledContent("Son kontrol", value: lastCheck(admin.updates.last_check))
                if let applying = admin.updates.applying { LabeledContent("Kuruluyor", value: applying) }
                if !admin.updates.queued.isEmpty { LabeledContent("Sırada", value: admin.updates.queued.joined(separator: ", ")) }
                if admin.updates.manifest_changed { Text("components.json değişti, commit edilmedi").font(.caption).foregroundStyle(.orange) }
                if !admin.updates.last_error.isEmpty { Text(admin.updates.last_error).font(.caption).foregroundStyle(.orange).textSelection(.enabled) }
                HStack {
                    if !admin.updatesMessage.isEmpty { Text(admin.updatesMessage).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
                    Spacer()
                    Button("Şimdi kontrol et") { Task { await admin.checkUpdates() } }.disabled(admin.updates.checking)
                }
            }
            Section("Güncellemeler") {
                ForEach(admin.updates.updates) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(item.name).bold()
                            Text("\(item.current) → \(item.latest)").monospaced()
                            if let size = item.size_mb { Text("\(size) MB").foregroundStyle(.secondary) }
                            if item.breaking == true { Text("uyumsuz").font(.caption).foregroundStyle(.red) }
                            if item.security == true { Text("güvenlik").font(.caption).foregroundStyle(.orange) }
                            if item.affects_our_flags == true { Text("ayarları etkiler").font(.caption).foregroundStyle(.orange) }
                            if item.status != "pending" { Text(item.status == "skipped" ? "atlandı" : item.status == "snoozed" ? "ertelendi" : item.status).font(.caption).foregroundStyle(.secondary) }
                            Spacer()
                            Button("Güncelle") {
                                if item.size_mb != nil { confirming = item } else { Task { await admin.applyUpdate(item.id) } }
                            }.disabled(busy)
                            Button("Atla") { Task { await admin.skipUpdate(item) } }.disabled(item.status == "skipped")
                        }
                        if let summary = item.summary { Text(summary).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                    }
                }
            }
            if let run = admin.updates.last_run, !run.components.isEmpty {
                Section("Son çalışma") {
                    ForEach(run.components) { item in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack { Text(item.name); Text("\(item.from) → \(item.to)").monospaced(); Spacer()
                                Text(item.status).font(.caption).foregroundStyle(item.status == "verified" ? Color.secondary : Color.orange) }
                            Text(item.steps.joined(separator: " › ")).font(.caption).monospaced().foregroundStyle(.secondary).textSelection(.enabled)
                            if let reason = item.reason { Text(reason).font(.caption).foregroundStyle(.orange).textSelection(.enabled) }
                        }
                    }
                }
            }
            if !admin.updates.rollback.isEmpty {
                Section("Geri al") {
                    ForEach(admin.updates.rollback) { item in
                        HStack {
                            Text(item.name); Text("→ \(item.version)").monospaced()
                            Spacer()
                            Button("Geri al") { Task { await admin.rollbackUpdate(item.id) } }.disabled(busy)
                        }
                    }
                }
            }
        }.formStyle(.grouped)
        .task { await admin.loadUpdates() }
        .confirmationDialog("\(confirming?.name ?? "") • \(confirming?.size_mb ?? 0) MB",
                            isPresented: Binding(get: { confirming != nil }, set: { if !$0 { confirming = nil } })) {
            Button("İndir ve kur") {
                if let item = confirming { Task { await admin.applyUpdate(item.id) } }
                confirming = nil
            }
            Button("İptal", role: .cancel) { confirming = nil }
        }
    }
}

private struct TasksTab: View {
    let admin: AdminModel
    @State private var deleting: TaskRow?
    var body: some View {
        Form {
            Section {
                Toggle("Asistan görevleri", isOn: Binding(get: { admin.tasksEnabled }, set: { on in Task { await admin.setTasks(on); await admin.loadTasks() } }))
                HStack {
                    if !admin.tasksMessage.isEmpty { Text(admin.tasksMessage).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
                    Spacer()
                    Button("Yenile") { Task { await admin.loadTasks() } }
                }
            }
            Section("Bildirim") {
                Toggle("Zil sesi", isOn: Binding(get: { admin.announceChime }, set: { on in Task { await admin.setAnnounce(chime: on) } }))
                Stepper("Erteleme: \(admin.announceSnooze) dk", value: Binding(get: { admin.announceSnooze }, set: { minutes in Task { await admin.setAnnounce(snooze: minutes) } }), in: 5...240, step: 5)
            }
            Section("Heartbeat") {
                Toggle("Arka plan kontrol listesi", isOn: Binding(get: { admin.heartbeatEnabled }, set: { on in Task { await admin.saveHeartbeat(["enabled": on]) } }))
                Stepper("Aralık: \(admin.heartbeatInterval) dk", value: Binding(get: { admin.heartbeatInterval }, set: { minutes in Task { await admin.saveHeartbeat(["interval_minutes": minutes]) } }), in: 15...240, step: 15)
                Stepper("Başlangıç: \(admin.heartbeatStart):00", value: Binding(get: { admin.heartbeatStart }, set: { hour in Task { await admin.saveHeartbeat(["start_hour": hour]) } }), in: 0...23)
                Stepper("Bitiş: \(admin.heartbeatEnd):00", value: Binding(get: { admin.heartbeatEnd }, set: { hour in Task { await admin.saveHeartbeat(["end_hour": hour]) } }), in: 1...24)
                VStack(alignment: .leading) {
                    Text("Kontrol listesi").font(.caption).foregroundStyle(.secondary)
                    TextEditor(text: Binding(get: { admin.heartbeatContent }, set: { admin.heartbeatContent = $0 })).font(.system(.body, design: .monospaced)).frame(minHeight: 80)
                    HStack {
                        if !admin.heartbeatMessage.isEmpty { Text(admin.heartbeatMessage).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
                        Spacer()
                        Button("Kaydet") { Task { await admin.saveHeartbeatChecklist() } }.disabled(admin.heartbeatContent == admin.heartbeatOriginal)
                    }
                }
            }
            Section("Görevler") {
                ForEach(admin.tasks.tasks) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(item.name).bold()
                            Text(item.schedule).foregroundStyle(.secondary)
                            Spacer()
                            Text(item.next).monospaced().foregroundStyle(item.status == "active" ? Color.primary : Color.secondary)
                        }
                        if let last = item.last, !last.isEmpty {
                            HStack(alignment: .top) {
                                Text(item.last_run ?? "").font(.caption).monospaced().foregroundStyle(.secondary)
                                Text(last).font(.caption).foregroundStyle(item.last_status == "failed" ? Color.orange : Color.secondary).lineLimit(2).textSelection(.enabled)
                            }
                        }
                        HStack {
                            if item.status == "active" { Button("Duraklat") { Task { await admin.taskAction(item.id, "pause") } } }
                            else { Button("Sürdür") { Task { await admin.taskAction(item.id, "resume") } } }
                            Button("Şimdi çalıştır") { Task { await admin.taskAction(item.id, "run") } }.disabled(!admin.tasksEnabled)
                            Spacer()
                            Button("Sil", role: .destructive) { deleting = item }
                        }
                    }
                }
            }
        }.formStyle(.grouped)
        .task { await admin.loadTasks(); await admin.loadReminders(); await admin.loadHeartbeat() }
        .confirmationDialog(deleting?.name ?? "", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
            Button("Sil", role: .destructive) {
                if let item = deleting { Task { await admin.taskAction(item.id, "delete") } }
                deleting = nil
            }
            Button("İptal", role: .cancel) { deleting = nil }
        }
    }
}

private struct MemoryTab: View {
    let admin: AdminModel
    @State private var drafts: [Int: String] = [:]
    @State private var adding: [String: String] = [:]
    @State private var clearing: String?
    private func rows(_ kind: String) -> [MemoryItem] { admin.memory.items.filter { $0.kind == kind } }
    @ViewBuilder private func section(_ title: String, _ kind: String) -> some View {
        Section(title) {
            ForEach(rows(kind)) { item in
                HStack {
                    TextField("", text: Binding(get: { drafts[item.id] ?? item.text }, set: { drafts[item.id] = $0 }))
                    if let draft = drafts[item.id], draft != item.text {
                        Button("Kaydet") { Task { await admin.editMemory(item.id, text: draft); drafts[item.id] = nil } }
                    }
                    if item.source == "extracted" { Text("oto").font(.caption).foregroundStyle(.secondary) }
                    Button { Task { await admin.pinMemory(item.id, !item.pinned) } } label: { Image(systemName: item.pinned ? "pin.fill" : "pin") }
                        .buttonStyle(.borderless).help("Sabitle")
                    Button(role: .destructive) { Task { await admin.deleteMemory(item.id) } } label: { Image(systemName: "trash") }
                        .buttonStyle(.borderless).help("Sil")
                }
            }
            HStack {
                TextField("Yeni", text: Binding(get: { adding[kind] ?? "" }, set: { adding[kind] = $0 }))
                Button("Ekle") { let text = adding[kind] ?? ""; Task { await admin.addMemory(kind: kind, text: text); adding[kind] = "" } }
                    .disabled((adding[kind] ?? "").trimmingCharacters(in: .whitespaces).isEmpty)
                Button("Tümünü sil", role: .destructive) { clearing = kind }.disabled(rows(kind).isEmpty)
            }
        }
    }
    var body: some View {
        Form {
            Section {
                Toggle("Hafıza", isOn: Binding(get: { admin.memory.enabled }, set: { value in Task { await admin.setMemory(enabled: value) } }))
                Toggle("Otomatik öğren", isOn: Binding(get: { admin.memory.extract }, set: { value in Task { await admin.setMemory(extract: value) } }))
                    .disabled(!admin.memory.enabled)
                if !admin.memoryMessage.isEmpty { Text(admin.memoryMessage).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
            }
            section("Asistan", "assistant")
            section("Kişisel", "personal")
            Section("Konular") {
                ForEach(admin.memory.topics) { topic in TopicRow(admin: admin, topic: topic) }
                if !admin.memory.topics.isEmpty {
                    Button("Tümünü sil", role: .destructive) { clearing = "topics" }
                }
            }
            Section("Sohbet özetleri") {
                ForEach(admin.memory.episodes) { episode in
                    HStack(alignment: .top) {
                        Text(Date(timeIntervalSince1970: episode.ended / 1000).formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
                        Text(episode.summary).textSelection(.enabled)
                        Spacer()
                        Button(role: .destructive) { Task { await admin.deleteEpisode(episode.id) } } label: { Image(systemName: "trash") }.buttonStyle(.borderless)
                    }
                }
                if !admin.memory.episodes.isEmpty {
                    Button("Tümünü sil", role: .destructive) { clearing = "episodes" }
                }
            }
        }.formStyle(.grouped)
        .task { await admin.loadMemory() }
        .confirmationDialog("Tümünü sil?", isPresented: Binding(get: { clearing != nil }, set: { if !$0 { clearing = nil } })) {
            Button("Sil", role: .destructive) { if let kind = clearing { Task { await admin.clearMemory(kind) } }; clearing = nil }
            Button("İptal", role: .cancel) { clearing = nil }
        }
    }
}

/** One topic: name, state, decisions and open items (one per line) editable; star = active topic. */
private struct TopicRow: View {
    let admin: AdminModel
    let topic: MemoryTopic
    @State private var title: String?
    @State private var state: String?
    @State private var decisions: String?
    @State private var open: String?
    private var changed: Bool { title != nil || state != nil || decisions != nil || open != nil }
    private func lines(_ text: String) -> [String] {
        text.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                TextField("Ad", text: Binding(get: { title ?? topic.title }, set: { title = $0 })).font(.headline)
                if topic.status == "done" { Text("kapalı").font(.caption).foregroundStyle(.secondary) }
                Button { Task { await admin.activateTopic(topic.id, !topic.active) } } label: { Image(systemName: topic.active ? "star.fill" : "star") }
                    .buttonStyle(.borderless).help("Aktif")
                Button(role: .destructive) { Task { await admin.deleteTopic(topic.id) } } label: { Image(systemName: "trash") }
                    .buttonStyle(.borderless).help("Sil")
            }
            TextField("Durum", text: Binding(get: { state ?? topic.state }, set: { state = $0 }), axis: .vertical).lineLimit(1...4)
            LabeledContent("Kararlar") {
                TextEditor(text: Binding(get: { decisions ?? topic.decisions.joined(separator: "\n") }, set: { decisions = $0 })).frame(minHeight: 40)
            }
            LabeledContent("Açık işler") {
                TextEditor(text: Binding(get: { open ?? topic.open.joined(separator: "\n") }, set: { open = $0 })).frame(minHeight: 40)
            }
            if changed {
                HStack {
                    Button("Kaydet") {
                        let value = (title ?? topic.title, state ?? topic.state, lines(decisions ?? topic.decisions.joined(separator: "\n")), lines(open ?? topic.open.joined(separator: "\n")))
                        Task { await admin.saveTopic(topic.id, title: value.0, state: value.1, decisions: value.2, open: value.3); title = nil; state = nil; decisions = nil; open = nil }
                    }
                    Button("Vazgeç") { title = nil; state = nil; decisions = nil; open = nil }
                }
            }
        }.padding(.vertical, 2)
    }
}

private struct ActivitySection: View {
    let admin: AdminModel
    var body: some View {
        Section {
            Table(admin.activity) {
                TableColumn("Saat", value: \.time).width(70)
                TableColumn("İstek", value: \.request).width(75)
                TableColumn("Olay", value: \.event).width(150)
                TableColumn("Ayrıntı", value: \.details)
            }.frame(height: 220)
        } header: {
            HStack {
                Text("Etkinlik")
                Spacer()
                Button("Log dosyasını aç…") { admin.actions.openActivityLog() }
            }
        }
    }
}

private struct SettingsTab: View {
    @Bindable var admin: AdminModel
    @Bindable var ring: RingModel
    var body: some View {
        Form {
            Section("Etkileşim") {
                LabeledContent("Kısayol") {
                    HStack { Text(ring.shortcutLabel).monospaced(); Button("Kısayolu değiştir…") { admin.actions.changeShortcut() } }
                }
                LabeledContent("Dikte kısayolu") {
                    HStack { Text(ring.dictationLabel).monospaced(); Button("Değiştir…") { admin.actions.changeDictationShortcut() } }
                }
                LabeledContent("Talimatlar") {
                    Button("Talimatları düzenle…") { admin.actions.editInstructions() }
                }
            }
            Section("Mod") {
                Toggle("Yerel kullan", isOn: Binding(get: { ring.offline }, set: { ring.setOffline($0) }))
                    .disabled(ring.changingMode || ring.phase == .starting)
                LabeledContent("Uzak model") {
                    HStack {
                        Picker("", selection: Binding(get: { admin.remote.model }, set: { model in Task { await admin.setRemoteModel(model) } })) {
                            Text("Pi varsayılanı").tag("")
                            ForEach(admin.remote.models, id: \.self) { Text($0).tag($0) }
                            if !admin.remote.model.isEmpty && !admin.remote.models.contains(admin.remote.model) { Text(admin.remote.model).tag(admin.remote.model) }
                        }.labelsHidden().fixedSize()
                        Button("Dene") { Task { await admin.testRemoteModel() } }.disabled(admin.remoteTesting)
                    }
                }
                if !admin.remoteMessage.isEmpty {
                    Text(admin.remoteMessage).font(.caption).foregroundStyle(admin.remoteMessage.hasPrefix("✓") ? .green : .red).textSelection(.enabled)
                }
                Toggle("Sesli yanıt", isOn: $ring.readAloud)
                Toggle("Menü çubuğu simgesi", isOn: Binding(get: { admin.menuBarIcon }, set: { admin.actions.setMenuBarIcon($0) }))
                Toggle("Anımsatıcılar", isOn: Binding(get: { admin.remindersEnabled }, set: { on in Task { await admin.setReminders(on) } }))
                TextField("Asistan listesi", text: $admin.tasksList)
                    .onSubmit { Task { await admin.setTasksList(admin.tasksList) } }
                    .disabled(!admin.remindersEnabled)
                if !admin.remindersMessage.isEmpty { Text(admin.remindersMessage).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
            }
            MailSection(admin: admin)
            Section("Dosyalar") {
                Toggle("Dosya arama", isOn: Binding(get: { admin.files.enabled }, set: { on in Task { await admin.setFiles(on) } }))
                TextEditor(text: $admin.fileRoots).font(.system(.body, design: .monospaced)).frame(minHeight: 60)
                HStack {
                    if !admin.filesMessage.isEmpty { Text(admin.filesMessage).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
                    Spacer()
                    Button("Kaydet") { Task { await admin.saveFileRoots() } }.disabled(admin.fileRoots == admin.fileRootsOriginal)
                }
                Text("Atlanan klasörler").font(.caption)
                TextEditor(text: $admin.fileSkip).font(.system(.body, design: .monospaced)).frame(minHeight: 60)
                HStack {
                    Spacer()
                    Button("Kaydet") { Task { await admin.saveFileSkip() } }.disabled(admin.fileSkip == admin.fileSkipOriginal)
                }
            }
            Section("Bitwarden") {
                Toggle("Bitwarden", isOn: Binding(get: { admin.vault.enabled }, set: { on in Task { await admin.setVault(on) } }))
                LabeledContent("Durum", value: "\(admin.vault.state == "unlocked" ? "Açık" : admin.vault.state == "unauthenticated" ? "Giriş yok" : admin.vault.state == "locked" ? "Kilitli" : admin.vault.state) • \(admin.vault.server)")
                TextField("E-posta", text: $admin.vaultEmail).textContentType(.username)
                    .onSubmit { Task { await admin.saveVaultEmail() } }
                SecureField("Ana parola", text: $admin.vaultPassword)
                    .onSubmit { Task { await admin.unlockVault() } }
                if admin.vault.state == "unauthenticated" { TextField("2FA kodu", text: $admin.vaultCode) }
                HStack {
                    if !admin.vaultMessage.isEmpty { Text(admin.vaultMessage).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
                    Spacer()
                    if admin.vaultEmail != admin.vault.email { Button("Kaydet") { Task { await admin.saveVaultEmail() } } }
                    Button("Kilidi aç") { Task { await admin.unlockVault() } }
                        .disabled(!admin.vault.enabled || admin.vaultPassword.isEmpty || admin.vaultBusy || admin.vaultEmail != admin.vault.email)
                    Button("Kilitle") { Task { await admin.lockVault() } }.disabled(admin.vault.state != "unlocked")
                }
            }
            Section("Okunuşlar") {
                TextEditor(text: $admin.pronunciations).font(.system(.body, design: .monospaced)).frame(minHeight: 120)
                HStack {
                    if !admin.pronunciationsMessage.isEmpty { Text(admin.pronunciationsMessage).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
                    Spacer()
                    Button("Kaydet") { Task { await admin.savePronunciations() } }.disabled(admin.pronunciations == admin.pronunciationsOriginal)
                }
            }
            Section("Uygulama") {
                Button("Yeniden başlat") { admin.actions.restart() }
            }
        }.formStyle(.grouped)
    }
}

/// Mail = the connected Gmail accounts (read tools and organizing), the OAuth client and triage settings.
/// No explanatory text: fields, buttons and one-line warnings only.
private struct MailSection: View {
    @Bindable var admin: AdminModel
    var body: some View {
        Section("Mail") {
            TextField("Client ID", text: $admin.gmailClientId, prompt: Text(admin.gmail.client_configured ? "Kayıtlı" : ""))
            HStack {
                SecureField("Client secret", text: $admin.gmailClientSecret)
                Button("Kaydet") { Task { await admin.saveGmailClient() } }
                    .disabled(admin.gmailClientId.isEmpty || admin.gmailClientSecret.isEmpty)
            }
            ForEach(admin.gmail.accounts) { account in
                VStack(alignment: .leading, spacing: 2) {
                    HStack {
                        Text(account.email)
                        Spacer()
                        Button("Bağlantıyı kes") { Task { await admin.disconnectGmail(account.email) } }
                    }
                    if account.error == "gmail_auth_expired" {
                        Text("Yeniden bağlan").font(.caption).foregroundStyle(.red)
                    } else if !account.error.isEmpty {
                        Text(account.error).font(.caption).foregroundStyle(.red)
                    }
                    if !account.missing_labels.isEmpty {
                        Text("Eksik etiket: \(account.missing_labels.joined(separator: ", "))").font(.caption).foregroundStyle(.orange)
                    }
                }
            }
            HStack {
                if admin.gmail.connecting { Text("Tarayıcıda onay bekleniyor…").foregroundStyle(.secondary) }
                Spacer()
                Button("Hesap ekle") { Task { await admin.connectGmail() } }.disabled(!admin.gmail.client_configured || admin.gmail.connecting)
            }
            Toggle("Mail düzenleme", isOn: Binding(get: { admin.gmail.triage_enabled }, set: { on in Task { await admin.setGmailTriage(on) } }))
                .disabled(!admin.gmail.connected)
            TextField("Acil gönderenler", text: $admin.gmailUrgent, prompt: Text("boss@work.com, @*.gov.uk"))
                .onSubmit { Task { await admin.saveGmailUrgent() } }
            VStack(alignment: .leading) {
                Text("Önem tanımı").font(.caption).foregroundStyle(.secondary)
                TextEditor(text: $admin.gmailImportance).font(.system(.body, design: .monospaced)).frame(minHeight: 90)
                HStack {
                    Spacer()
                    Button("Kaydet") { Task { await admin.saveImportance() } }.disabled(admin.gmailImportance == admin.gmailImportanceOriginal)
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
                    Button("Yeni skill…") { newName = ""; newDescription = ""; creatingSkill = true }
                    Button("Yeni extension…") { newName = ""; creatingExtension = true }
                }
                List(selection: $selectionKey) {
                    Section("Skills (.pi/skills)") { ForEach(admin.resources?.skills ?? []) { rows($0) } }
                    Section("Extensions (.pi/extensions)") { ForEach(admin.resources?.extensions ?? []) { rows($0) } }
                }
            }.frame(minWidth: 240, idealWidth: 280)
            VStack(alignment: .leading, spacing: 8) {
                if admin.selectedPath.isEmpty {
                    Text("Görmek veya düzenlemek için bir dosya seç").foregroundStyle(.secondary)
                    Spacer()
                } else {
                    Text("\(admin.selectedKind == "skill" ? ".pi/skills" : ".pi/extensions")/\(admin.selectedPath)").font(.headline).textSelection(.enabled)
                    if !admin.loadError.isEmpty {
                        Text("Dosya yüklenemedi: \(admin.loadError)").foregroundStyle(.red).textSelection(.enabled)
                        Spacer()
                    } else {
                        TextEditor(text: $admin.content).font(.system(.body, design: .monospaced))
                    }
                    HStack {
                        Spacer()
                        if let resource = admin.selectedResource {
                            Button("Sil…", role: .destructive) { deleting = resource }.disabled(resource.loading == "built-in")
                        }
                        Button("Geri al") { admin.content = admin.original }.disabled(!dirty)
                        Button("Kaydet") { Task { await admin.save() } }.disabled(!dirty).keyboardShortcut("s", modifiers: .command)
                    }
                }
                if !admin.message.isEmpty { Text(admin.message).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
            }.padding(.leading, 8).frame(minWidth: 380)
        }
        .onChange(of: selectionKey) { _, key in selectionChanged(key) }
        // Keep the List in sync when the model selects a file itself (create skill/extension); equal keys stop the loop.
        .onChange(of: currentKey, initial: true) { _, key in if selectionKey != key { selectionKey = key } }
        .confirmationDialog("Kaydedilmemiş değişiklikler atılsın mı?", isPresented: $confirmDiscard) {
            Button("Değişiklikleri at", role: .destructive) { if let key = pendingKey { load(key) }; pendingKey = nil }
            Button("İptal", role: .cancel) { pendingKey = nil; selectionKey = currentKey }
        }
        .confirmationDialog("\(deleting?.name ?? "") silinsin mi?",
                            isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
            Button("Sil", role: .destructive) { if let resource = deleting { Task { await admin.delete(resource) } }; deleting = nil }
            Button("İptal", role: .cancel) { deleting = nil }
        }
        .sheet(isPresented: $creatingSkill) {
            form(title: "Yeni skill", description: true) { Task { await admin.createSkill(name: newName, description: newDescription) } }
        }
        .sheet(isPresented: $creatingExtension) {
            form(title: "Yeni extension", description: false) { Task { await admin.createExtension(name: newName) } }
        }
    }

    @ViewBuilder private func rows(_ resource: AdminResource) -> some View {
        let header = VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(resource.name).bold()
                if resource.valid == false { Text("geçersiz").font(.caption).foregroundStyle(.orange) }
                if resource.loading == "built-in" { Text("yerleşik").font(.caption).foregroundStyle(.secondary) }
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
        Button("Sil…", role: .destructive) { deleting = resource }.disabled(resource.loading == "built-in")
    }

    private func form(title: String, description: Bool, create: @escaping () -> Void) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline)
            TextField("Ad (küçük harf, rakam, tire)", text: $newName)
            if description { TextField("Kısa açıklama", text: $newDescription) }
            HStack {
                Spacer()
                Button("İptal") { creatingSkill = false; creatingExtension = false }.keyboardShortcut(.cancelAction)
                Button("Oluştur") { creatingSkill = false; creatingExtension = false; create() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(newName.isEmpty || (description && newDescription.trimmingCharacters(in: .whitespaces).isEmpty))
            }
        }.padding(16).frame(width: 420)
    }
}
