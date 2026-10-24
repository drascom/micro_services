package com.diziflix.app

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import org.json.JSONArray
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * Otomatik güncelleme (GitHub release): `drascom/micro_services` reposunda `diziflix-v*` etiketli, taslak/ön sürüm olmayan en
 * yüksek sürüm mevcut versionName'den büyükse `diziflix-<ver>.apk` arka planda cache/updates/'e indirilir, release'in
 * `SHA256SUMS` asset'iyle doğrulanır, sonra web'e `DZUpdate.ready(ver)` bildirilir. Kurulum penceresini web açtırır
 * (`DZAndroid.installUpdate()`; yalnız ana ekranda, oynatmada değil). Denetim en çok 6 saatte bir (API sınırı 60/saat/IP);
 * ağ hatası sessiz.
 *
 * onState(state, value): checking | none (value = mevcut sürüm) | ready (value = yeni sürüm) | error
 */
class Updater(private val act: Activity, private val onState: (String, String) -> Unit) {

    private val prefs = act.getSharedPreferences("dz_update", Context.MODE_PRIVATE)
    private val ui = Handler(Looper.getMainLooper())
    @Volatile private var busy = false
    @Volatile var readyVersion: String? = null
        private set
    private var installAfterPermission = false

    val current: String = try {
        act.packageManager.getPackageInfo(act.packageName, 0).versionName ?: "0.0.0"
    } catch (e: Exception) { "0.0.0" }

    /** force=false: son denetimden 6 saat geçmediyse hiçbir şey yapmaz. */
    fun check(force: Boolean) {
        if (busy) return
        val now = System.currentTimeMillis()
        if (!force && now - prefs.getLong(KEY_LAST, 0L) < INTERVAL_MS) return
        busy = true
        prefs.edit().putLong(KEY_LAST, now).apply()
        if (force) post("checking", "")
        Thread {
            try {
                val ver = run()
                if (ver != null) { readyVersion = ver; post("ready", ver) } else post("none", current)
            } catch (e: Exception) {
                Log.d(TAG, "check: ${e.javaClass.simpleName}: ${e.message}")
                post("error", "")
            } finally { busy = false }
        }.start()
    }

    /** Yeni sürüm indirilip doğrulandıysa onun sürümü, güncelse null. */
    private fun run(): String? {
        val rels = JSONArray(httpText(API_URL))
        var best: String? = null
        var apkUrl = ""
        var sumsUrl = ""
        for (i in 0 until rels.length()) {
            val r = rels.getJSONObject(i)
            if (r.optBoolean("draft") || r.optBoolean("prerelease")) continue
            val tag = r.optString("tag_name")
            if (!tag.startsWith(TAG_PREFIX)) continue
            val ver = tag.removePrefix(TAG_PREFIX)
            if (!ver.matches(SEMVER)) continue
            if (best != null && compare(ver, best) <= 0) continue
            var a = ""; var s = ""
            val assets = r.optJSONArray("assets") ?: JSONArray()
            for (j in 0 until assets.length()) {
                val o = assets.getJSONObject(j)
                when (o.optString("name")) {
                    "diziflix-$ver.apk" -> a = o.optString("browser_download_url")
                    "SHA256SUMS" -> s = o.optString("browser_download_url")
                }
            }
            if (a.isEmpty() || s.isEmpty()) continue
            best = ver; apkUrl = a; sumsUrl = s
        }
        if (best == null || compare(best, current) <= 0) return null
        val name = "diziflix-$best.apk"
        val want = httpText(sumsUrl).lineSequence()
            .map { it.trim().split(Regex("\\s+")) }
            .firstOrNull { it.size >= 2 && it[1].trimStart('*') == name }?.get(0)?.lowercase()
            ?: throw IllegalStateException("SHA256SUMS: $name yok")
        val dir = File(act.cacheDir, DIR).apply { mkdirs() }
        dir.listFiles()?.forEach { if (it.name != name) it.delete() }   // eski indirmeler
        val out = File(dir, name)
        if (out.isFile && sha256(out) == want) return best
        val part = File(dir, "$name.part")
        download(apkUrl, part)
        if (sha256(part) != want) { part.delete(); throw IllegalStateException("SHA-256 uyuşmadı") }
        if (!part.renameTo(out)) throw IllegalStateException("rename")
        return best
    }

    /** Kurulum ekranını açar (UI iş parçacığında çağır). Bilinmeyen kaynak izni yoksa ayarlar ekranına yönlendirir;
     *  dönüşte izin verildiyse kurulum kendiliğinden sürer (onResume), verilmediyse tekrar yönlendirmez. */
    fun install(): Boolean {
        val ver = readyVersion ?: return false
        val f = File(File(act.cacheDir, DIR), "diziflix-$ver.apk")
        if (!f.isFile) { readyVersion = null; return false }
        if (Build.VERSION.SDK_INT >= 26 && !act.packageManager.canRequestPackageInstalls()) {
            installAfterPermission = true
            return try {
                act.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${act.packageName}")))
                true
            } catch (e: Exception) { launchInstaller(f) }
        }
        return launchInstaller(f)
    }

    /** MainActivity.onResume: ayarlardan dönüşte izin verildiyse kuruluma devam. */
    fun onResume() {
        if (!installAfterPermission) return
        installAfterPermission = false
        if (Build.VERSION.SDK_INT >= 26 && act.packageManager.canRequestPackageInstalls()) install()
    }

    private fun launchInstaller(f: File): Boolean = try {
        val uri = if (Build.VERSION.SDK_INT >= 24) {
            Uri.parse("content://${act.packageName}.apk/${f.name}")
        } else {
            // API < 24 yükleyicisi content:// okuyamaz: dış önbellekten file://
            val ext = File(act.externalCacheDir ?: throw IllegalStateException("externalCacheDir"), f.name)
            f.copyTo(ext, overwrite = true)
            Uri.fromFile(ext)
        }
        act.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, MIME)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
        true
    } catch (e: Exception) {
        Log.d(TAG, "install: ${e.message}")
        false
    }

    private fun post(state: String, value: String) = ui.post { onState(state, value) }

    private fun open(url: String): HttpURLConnection =
        (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15000
            readTimeout = 30000
            instanceFollowRedirects = true
            setRequestProperty("User-Agent", "diziflix-android/$current")
            setRequestProperty("Accept", if (url.startsWith(API_URL)) "application/vnd.github+json" else "*/*")
        }

    private fun httpText(url: String): String {
        val c = open(url)
        try {
            if (c.responseCode != 200) throw IllegalStateException("HTTP ${c.responseCode}")
            return c.inputStream.bufferedReader().use { it.readText() }
        } finally { c.disconnect() }
    }

    private fun download(url: String, dest: File) {
        val c = open(url)
        try {
            if (c.responseCode != 200) throw IllegalStateException("HTTP ${c.responseCode}")
            c.inputStream.use { inp -> dest.outputStream().use { inp.copyTo(it, 64 * 1024) } }
        } finally { c.disconnect() }
    }

    companion object {
        private const val TAG = "DZUpdate"
        const val DIR = "updates"
        const val MIME = "application/vnd.android.package-archive"
        private const val API_URL = "https://api.github.com/repos/drascom/micro_services/releases?per_page=20"
        private const val TAG_PREFIX = "diziflix-v"
        private const val KEY_LAST = "last_check"
        const val INTERVAL_MS = 6L * 60 * 60 * 1000
        private val SEMVER = Regex("[0-9]+\\.[0-9]+\\.[0-9]+")

        fun compare(a: String, b: String): Int {
            val x = a.split(".").map { it.toIntOrNull() ?: 0 }
            val y = b.split(".").map { it.toIntOrNull() ?: 0 }
            for (i in 0 until maxOf(x.size, y.size)) {
                val d = x.getOrElse(i) { 0 } - y.getOrElse(i) { 0 }
                if (d != 0) return d
            }
            return 0
        }

        fun sha256(f: File): String {
            val md = MessageDigest.getInstance("SHA-256")
            f.inputStream().use { inp ->
                val buf = ByteArray(64 * 1024)
                while (true) { val n = inp.read(buf); if (n < 0) break; md.update(buf, 0, n) }
            }
            return md.digest().joinToString("") { "%02x".format(it) }
        }
    }
}
