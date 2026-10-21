package com.villabridge.android

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.util.concurrent.atomic.AtomicBoolean
import java.util.zip.GZIPInputStream

object NodeRuntime {
    enum class State { STOPPED, STARTING, RUNNING, FAILED }

    private const val TAG = "VillaNode"
    private val started = AtomicBoolean(false)
    private var loadFailure: Throwable? = null
    @Volatile var state = State.STOPPED
        private set
    @Volatile var failureMessage: String? = null
        private set

    init {
        runCatching {
            System.loadLibrary("node")
            System.loadLibrary("villa_node")
        }.onFailure {
            loadFailure = it
            state = State.FAILED
            failureMessage = it.message
            Log.e(TAG, "Node.js Mobile libraries could not be loaded", it)
        }
    }

    fun start(context: Context, controlToken: String, onExit: (Int) -> Unit): Boolean {
        loadFailure?.let { throw IllegalStateException("Node.js Mobile is unavailable", it) }
        if (!started.compareAndSet(false, true)) return false
        state = State.STARTING
        failureMessage = null
        val storage = context.createDeviceProtectedStorageContext()
        val projectDirectory: File
        val dataDirectory: File
        try {
            projectDirectory = AssetProjectInstaller.install(context, storage)
            dataDirectory = File(storage.filesDir, "villa-data").apply { mkdirs() }
        } catch (error: Throwable) {
            started.set(false)
            state = State.FAILED
            failureMessage = error.message
            throw error
        }

        Thread({
            val result = startNode(
                arrayOf(
                    "node",
                    File(projectDirectory, "main.cjs").absolutePath,
                    "--core-host=127.0.0.1",
                    "--diagnostics-port=8092",
                    "--mqtt-host=0.0.0.0",
                    "--mqtt-port=1883",
                    "--control-token=$controlToken",
                    "--data-dir=${dataDirectory.absolutePath}"
                )
            )
            state = if (result == 0) State.STOPPED else State.FAILED
            failureMessage = if (result == 0) null else "Node.js runtime exited with code $result"
            Log.e(TAG, "Node.js runtime exited with code $result")
            onExit(result)
        }, "villa-node-runtime").apply {
            isDaemon = false
            start()
        }
        return true
    }

    fun markRunning() {
        if (state == State.STARTING) state = State.RUNNING
    }

    private external fun startNode(arguments: Array<String>): Int
}

private object AssetProjectInstaller {
    private const val ASSET_PATH = "nodejs-project"
    private const val ASSET_ARCHIVE = "nodejs-project.tgz"
    private const val PREFS_NAME = "villa_node_assets"
    private const val PREFS_APK_TIME = "apk_update_time"
    private const val TAR_BLOCK_SIZE = 512

    fun install(assetContext: Context, storageContext: Context): File {
        val destination = File(storageContext.filesDir, ASSET_PATH)
        val preferences = storageContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val apkUpdateTime = packageUpdateTime(assetContext)
        if (destination.isDirectory && preferences.getLong(PREFS_APK_TIME, -1L) == apkUpdateTime) {
            return destination
        }

        val staging = File(storageContext.filesDir, "$ASSET_PATH.new")
        check(!staging.exists() || staging.deleteRecursively()) {
            "Unable to clear the staged Node.js project"
        }
        check(extractAssetArchive(assetContext, staging)) {
            "Unable to unpack the Node.js project"
        }
        check(!destination.exists() || destination.deleteRecursively()) {
            "Unable to replace the previous Node.js project"
        }
        check(staging.renameTo(destination)) {
            "Unable to activate the Node.js project"
        }
        preferences.edit().putLong(PREFS_APK_TIME, apkUpdateTime).apply()
        return destination
    }

    private fun packageUpdateTime(context: Context): Long {
        val packageInfo = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.packageManager.getPackageInfo(
                context.packageName,
                PackageManager.PackageInfoFlags.of(0)
            )
        } else {
            @Suppress("DEPRECATION")
            context.packageManager.getPackageInfo(context.packageName, 0)
        }
        return packageInfo.lastUpdateTime
    }

    private fun extractAssetArchive(context: Context, destination: File): Boolean = runCatching {
        val root = destination.canonicalFile
        root.mkdirs()
        GZIPInputStream(context.assets.open(ASSET_ARCHIVE)).use { input ->
            val header = ByteArray(TAR_BLOCK_SIZE)
            while (readFully(input, header)) {
                if (header.all { it == 0.toByte() }) break
                val name = tarString(header, 0, 100)
                val prefix = tarString(header, 345, 155)
                val relativePath = if (prefix.isEmpty()) name else "$prefix/$name"
                check(relativePath == ASSET_PATH || relativePath.startsWith("$ASSET_PATH/")) {
                    "Unexpected archive path: $relativePath"
                }
                val strippedPath = relativePath.removePrefix(ASSET_PATH).trimStart('/')
                val target = File(root, strippedPath).canonicalFile
                check(target == root || target.path.startsWith("${root.path}${File.separator}")) {
                    "Archive path escapes the project directory"
                }
                val size = tarOctal(header, 124, 12)
                when (header[156].toInt().toChar()) {
                    '5' -> target.mkdirs()
                    '0', '\u0000' -> {
                        target.parentFile?.mkdirs()
                        FileOutputStream(target).use { output ->
                            copyExactly(input, output, size)
                        }
                    }
                    else -> skipExactly(input, size)
                }
                val padding = (TAR_BLOCK_SIZE - size % TAR_BLOCK_SIZE) % TAR_BLOCK_SIZE
                skipExactly(input, padding)
            }
        }
        File(root, "main.cjs").isFile &&
            File(root, "asset-manifest.json").isFile &&
            File(root, "villa-bridge/dist/index.js").isFile
    }.getOrDefault(false)

    private fun readFully(input: InputStream, buffer: ByteArray): Boolean {
        var offset = 0
        while (offset < buffer.size) {
            val count = input.read(buffer, offset, buffer.size - offset)
            if (count < 0) {
                if (offset == 0) return false
                error("Unexpected end of Android asset archive")
            }
            offset += count
        }
        return true
    }

    private fun tarString(header: ByteArray, offset: Int, length: Int): String {
        val end = (offset until offset + length)
            .firstOrNull { header[it] == 0.toByte() } ?: offset + length
        return header.copyOfRange(offset, end).toString(Charsets.UTF_8).trim()
    }

    private fun tarOctal(header: ByteArray, offset: Int, length: Int): Long {
        val value = tarString(header, offset, length).trim()
        return if (value.isEmpty()) 0L else value.toLong(8)
    }

    private fun copyExactly(input: InputStream, output: FileOutputStream, byteCount: Long) {
        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
        var remaining = byteCount
        while (remaining > 0) {
            val count = input.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
            check(count > 0) { "Unexpected end of Android asset archive" }
            output.write(buffer, 0, count)
            remaining -= count
        }
    }

    private fun skipExactly(input: InputStream, byteCount: Long) {
        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
        var remaining = byteCount
        while (remaining > 0) {
            val skipped = input.skip(remaining)
            if (skipped > 0) {
                remaining -= skipped
                continue
            }
            val count = input.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
            check(count > 0) { "Unexpected end of Android asset archive" }
            remaining -= count
        }
    }
}
