package com.villabridge.android

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class NodeRuntimeService : Service() {
    private var multicastLock: WifiManager.MulticastLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var wakeLock: PowerManager.WakeLock? = null
    @Volatile private var lastPublishedHostState: String? = null
    private val healthExecutor = Executors.newSingleThreadScheduledExecutor()
    private val destroyed = AtomicBoolean(false)
    private val runtimeStarted = AtomicBoolean(false)
    private val healthScheduled = AtomicBoolean(false)
    private val deliberateStop = AtomicBoolean(false)
    private val restartPending = AtomicBoolean(false)

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startForeground(NOTIFICATION_ID, notification(getString(R.string.service_starting)))
        acquireLocks()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (
            intent?.action == ACTION_STOP ||
            RuntimeStateStore.desired(this) == RuntimeStateStore.DesiredState.STOPPED
        ) {
            requestStop()
            return START_NOT_STICKY
        }
        startRuntime()
        return START_STICKY
    }

    private fun startRuntime() {
        if (!runtimeStarted.compareAndSet(false, true)) return
        deliberateStop.set(false)
        healthExecutor.execute {
            val startAccepted = runCatching {
                NodeRuntime.start(this, RuntimeStateStore.controlToken(this), ::handleRuntimeExit)
            }
                .onFailure {
                    Log.e(TAG, "Villa Bridge core failed to start", it)
                    showStatus(getString(R.string.service_error))
                }
                .isSuccess

            if (
                startAccepted &&
                !destroyed.get() &&
                healthScheduled.compareAndSet(false, true)
            ) {
                healthExecutor.scheduleWithFixedDelay(
                    ::refreshHealth,
                    2,
                    10,
                    TimeUnit.SECONDS
                )
            }
        }
    }

    private fun requestStop() {
        if (!deliberateStop.compareAndSet(false, true)) return
        RuntimeStateStore.setDesired(this, RuntimeStateStore.DesiredState.STOPPED)
        showStatus(getString(R.string.service_stopping))
        healthExecutor.execute {
            if (runtimeStarted.get()) {
                runCatching { requestGracefulShutdown() }
                    .onFailure { Log.w(TAG, "Graceful runtime shutdown request failed", it) }
            }
        }
        Handler(Looper.getMainLooper()).postDelayed(::finishDeliberateStop, STOP_TIMEOUT_MS)
    }

    private fun requestGracefulShutdown() {
        val connection = URI(SHUTDOWN_URL).toURL().openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 1_500
            connection.readTimeout = 2_500
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty(
                "Authorization",
                "Bearer ${RuntimeStateStore.controlToken(this)}"
            )
            connection.setFixedLengthStreamingMode(0)
            connection.outputStream.use { }
            check(connection.responseCode == HttpURLConnection.HTTP_ACCEPTED) {
                "Runtime rejected shutdown with HTTP ${connection.responseCode}"
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun finishDeliberateStop() {
        if (!deliberateStop.get()) return
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
        Handler(Looper.getMainLooper()).postDelayed(
            { android.os.Process.killProcess(android.os.Process.myPid()) },
            100
        )
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        destroyed.set(true)
        healthExecutor.shutdownNow()
        multicastLock?.takeIf { it.isHeld }?.release()
        wifiLock?.takeIf { it.isHeld }?.release()
        wakeLock?.takeIf { it.isHeld }?.release()
        super.onDestroy()
    }

    /**
     * Uc kilit birlikte tutulur ve servis yasadigi surece birakilmaz:
     *  - MULTICAST: mDNS olmadan Matter ve Home Assistant kesfi calismaz.
     *  - WI-FI: ekran kapaliyken Wi-Fi radyosunun uykuya dalmasini engeller. API 29'dan itibaren
     *    `FULL_HIGH_PERF` yok sayiliyor; oradan sonra dogru kip `FULL_LOW_LATENCY`.
     *  - PARTIAL WAKE: islemci ekran kapaliyken de calisir; koordinator baglantisi kopmaz.
     */
    @SuppressLint("WakelockTimeout")
    private fun acquireLocks() {
        val wifiManager = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        multicastLock = wifiManager.createMulticastLock("villa-bridge-matter").apply {
            setReferenceCounted(false)
            acquire()
        }
        val wifiMode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            WifiManager.WIFI_MODE_FULL_LOW_LATENCY
        } else {
            @Suppress("DEPRECATION")
            WifiManager.WIFI_MODE_FULL_HIGH_PERF
        }
        wifiLock = wifiManager.createWifiLock(wifiMode, "villa-bridge-wifi").apply {
            setReferenceCounted(false)
            acquire()
        }
        val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "VillaBridge::Core").apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    @SuppressLint("WakelockTimeout")
    private fun reacquireLostLocks() {
        if (destroyed.get()) return
        runCatching {
            multicastLock?.takeIf { !it.isHeld }?.acquire()
            wifiLock?.takeIf { !it.isHeld }?.acquire()
            wakeLock?.takeIf { !it.isHeld }?.acquire()
        }.onFailure { Log.w(TAG, "Villa Bridge locks could not be re-acquired", it) }
    }

    private fun refreshHealth() {
        // Geri cekilme suresi isliyorken yoklama susar: yoksa on saniyede bir "baslatiliyor"
        // yazip "N sn icinde yeniden basliyor" bildirimini eziyor olurdu.
        if (restartPending.get()) return
        if (NodeRuntime.state == NodeRuntime.State.FAILED) {
            showStatus(getString(R.string.service_error))
            return
        }
        val healthy = runCatching {
            val connection = URI("http://127.0.0.1:8092/api/ready").toURL()
                .openConnection() as HttpURLConnection
            try {
                connection.connectTimeout = 1_500
                connection.readTimeout = 1_500
                connection.requestMethod = "GET"
                connection.responseCode == HttpURLConnection.HTTP_OK
            } finally {
                connection.disconnect()
            }
        }.getOrDefault(false)
        if (healthy) {
            NodeRuntime.markRunning()
            RuntimeWatchdog.noteHealthy(this)
            runCatching { publishHostState(RuntimeWatchdog.report(this)) }
                .onFailure { Log.d(TAG, "Host lifecycle state could not be published", it) }
        }
        // Kilitler servisin butun omru boyunca duruyor olmali; sistem ya da bir surucu birini
        // biraktiysa (Wi-Fi kapanip acilmasi bunu yapabilir) sessizce yeniden alinir.
        reacquireLostLocks()
        showStatus(getString(if (healthy) R.string.service_running else R.string.service_starting))
    }

    /**
     * Beklenmedik cikis. Yeniden baslatma karari `RuntimeWatchdog`'un: gecikme ustel buyur,
     * art arda basarisizlikta bir ust sinir vardir. Node ayni surecte JNI ile calistigi icin
     * yeniden baslatmanin tek yolu bu sureci oldurmektir; `START_STICKY` servisi geri getirir.
     * Gecikme oldurmeden ONCE beklenir, yoksa sistem bizi hemen geri baslatir ve geri cekilme
     * hic uygulanmamis olur.
     */
    private fun handleRuntimeExit(result: Int) {
        if (destroyed.get()) return
        if (
            deliberateStop.get() ||
            RuntimeStateStore.desired(this) == RuntimeStateStore.DesiredState.STOPPED
        ) {
            Log.i(TAG, "Villa Bridge core stopped with code $result")
            Handler(Looper.getMainLooper()).post(::finishDeliberateStop)
            return
        }
        Log.e(TAG, "Villa Bridge core exited unexpectedly with code $result")

        restartPending.set(true)
        val decision = RuntimeWatchdog.noteFailure(this, result)
        if (!decision.restart) {
            Log.e(TAG, "Runtime restart abandoned after ${decision.attempt} consecutive failures")
            showStatus(getString(R.string.service_exhausted), ongoing = false)
            Handler(Looper.getMainLooper()).post {
                stopForeground(STOP_FOREGROUND_DETACH)
                stopSelf()
            }
            return
        }
        val seconds = (decision.delayMs / 1_000).toInt()
        Log.w(TAG, "Runtime restart ${decision.attempt} scheduled in $seconds s")
        showStatus(getString(R.string.service_restarting, seconds, decision.attempt))
        Handler(Looper.getMainLooper()).postDelayed(
            { android.os.Process.killProcess(android.os.Process.myPid()) },
            decision.delayMs
        )
    }

    /**
     * Konagin yasam dongusu durumu tanilamaya birakilir: calisma zamani geri geldiginde
     * kac kez ve neden yeniden baslatildigi `/api/android/diagnostics` uzerinden gorunur.
     * Yalnizca degisince gonderilir — saglik yoklamasi on saniyede bir kosuyor.
     */
    private fun publishHostState(report: RuntimeWatchdog.Report) {
        val payload = JSONObject()
            .put("restarts", report.failures)
            .put("lastExitCode", report.lastExitCode)
            .put("lastFailureAt", report.lastFailureAt)
            .put("nextDelayMs", report.nextDelayMs)
            .put("exhausted", report.exhausted)
            .put("maxRestarts", RuntimeWatchdog.MAX_FAILURES)
            .put("maxDelayMs", RuntimeWatchdog.MAX_DELAY_MS)
            .toString()
        if (payload == lastPublishedHostState) return
        val connection = URI(HOST_STATE_URL).toURL().openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 1_500
            connection.readTimeout = 2_500
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("content-type", "application/json")
            connection.setRequestProperty(
                "Authorization",
                "Bearer ${RuntimeStateStore.controlToken(this)}"
            )
            connection.outputStream.use { it.write(payload.toByteArray()) }
            if (connection.responseCode == HttpURLConnection.HTTP_OK) {
                lastPublishedHostState = payload
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.service_channel_name),
            NotificationManager.IMPORTANCE_LOW
        )
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun showStatus(text: String, ongoing: Boolean = true) {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(NOTIFICATION_ID, notification(text, ongoing))
    }

    private fun notification(text: String, ongoing: Boolean = true): Notification {
        val launchIntent = Intent(this, MainActivity::class.java)
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            launchIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder
            .setSmallIcon(R.drawable.ic_villa_bridge)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setContentIntent(pendingIntent)
            .setOngoing(ongoing)
            .setOnlyAlertOnce(true)
            .build()
    }

    companion object {
        private const val TAG = "VillaCoreService"
        private const val CHANNEL_ID = "villa_bridge_core"
        private const val NOTIFICATION_ID = 81
        private const val STOP_TIMEOUT_MS = 6_000L
        private const val SHUTDOWN_URL = "http://127.0.0.1:8092/api/android/runtime/shutdown"
        private const val HOST_STATE_URL = "http://127.0.0.1:8092/api/android/runtime/host-state"
        private const val ACTION_START = "com.villabridge.android.action.START_RUNTIME"
        private const val ACTION_STOP = "com.villabridge.android.action.STOP_RUNTIME"

        /**
         * Acik bir baslatma istegi: kullanici dugmeye bastigi ya da cihaz yeniden acildigi
         * icin gelinir. Vazgecilmis bir defter ancak burada temizlenir; `START_STICKY` ile
         * gelen sistem yeniden baslatmalari bu yoldan GECMEZ, yoksa ust sinir hic isler bir
         * kural olmazdi.
         */
        fun start(context: Context) {
            RuntimeStateStore.setDesired(context, RuntimeStateStore.DesiredState.RUNNING)
            RuntimeWatchdog.reset(context)
            startServiceCommand(context, ACTION_START)
        }

        fun stop(context: Context) {
            RuntimeStateStore.setDesired(context, RuntimeStateStore.DesiredState.STOPPED)
            startServiceCommand(context, ACTION_STOP)
        }

        private fun startServiceCommand(context: Context, action: String) {
            val intent = Intent(context, NodeRuntimeService::class.java).setAction(action)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }
}
