import Foundation
import Darwin

struct SystemMemory {
    let usedBytes: UInt64
    let totalBytes: UInt64
    var label: String {
        let unit = 1024.0 * 1024 * 1024
        return String(format: "%.1f / %.0f GB", Double(usedBytes) / unit, Double(totalBytes) / unit)
    }
    static func read() -> SystemMemory? {
        var stats = vm_statistics64_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<vm_statistics64_data_t>.size / MemoryLayout<integer_t>.size)
        let host = mach_host_self()
        defer { mach_port_deallocate(mach_task_self_, host) }
        let result = withUnsafeMutablePointer(to: &stats) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                host_statistics64(host, HOST_VM_INFO64, $0, &count)
            }
        }
        var pageSize: vm_size_t = 0
        guard result == KERN_SUCCESS, host_page_size(host, &pageSize) == KERN_SUCCESS else { return nil }
        // Application pages + wired memory + compressed storage; exclude reclaimable cache.
        let pages = UInt64(stats.internal_page_count) + UInt64(stats.wire_count) + UInt64(stats.compressor_page_count)
        let purgeable = UInt64(stats.purgeable_count)
        let total = ProcessInfo.processInfo.physicalMemory
        let used = min(total, (pages > purgeable ? pages - purgeable : 0) * UInt64(pageSize))
        return SystemMemory(usedBytes: used, totalBytes: total)
    }
}
