package com.diziflix.app

import android.annotation.SuppressLint
import android.app.Activity
import android.content.pm.ApplicationInfo
import android.graphics.Bitmap
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.util.Log
import android.webkit.ConsoleMessage
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import androidx.webkit.WebViewAssetLoader
import java.io.ByteArrayInputStream

/**
 * İnce WebView kabuğu: `client/` web istemcisini (derlemede assets/client/'e kopyalanır) yükler.
 *
 * Köken: http://appassets.androidplatform.net (WebViewAssetLoader, http izinli) -> LAN'daki http sunucusuna
 * XHR karışık içerik değildir; sunucu CORS `*` verir. localStorage bu köken için kalıcıdır.
 *
 * Tuşlar: oklar/OK/rakamlar WebView'e doğal gider (DPAD_CENTER -> Enter/13). Geri -> JS'e `Escape` (27);
 * kök ekranda istemci `DZAndroid.exit()` çağırır.
 * Güncelleme: Updater (GitHub release) -> `DZUpdate.ready(ver)`; JS `DZAndroid.installUpdate()` ile kurdurur. Kanal ve medya tuşları istemcinin tuş haritasına
 * (client/js/app.js KEYMAP/KEYNAME) birebir key + keyCode ile enjekte edilir.
 */
class MainActivity : Activity() {

    private lateinit var root: FrameLayout
    private lateinit var web: WebView
    private var customView: View? = null
    private var customCallback: WebChromeClient.CustomViewCallback? = null
    private lateinit var updater: Updater
    private val periodic = android.os.Handler(android.os.Looper.getMainLooper())
    private val periodicCheck = object : Runnable {
        override fun run() {
            updater.check(false)
            periodic.postDelayed(this, Updater.INTERVAL_MS)
        }
    }

    private val assetLoader by lazy {
        WebViewAssetLoader.Builder()
            .setDomain(HOST)
            .setHttpAllowed(true)
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()
    }

    @SuppressLint("SetJavaScriptEnabled", "AddJavascriptInterface")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) {
            WebView.setWebContentsDebuggingEnabled(true)
        }

        root = FrameLayout(this)
        root.setBackgroundColor(Color.BLACK)
        web = WebView(this)
        web.setBackgroundColor(Color.BLACK)
        root.addView(web, FrameLayout.LayoutParams(MATCH, MATCH))
        setContentView(root)

        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            mediaPlaybackRequiresUserGesture = false
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            loadWithOverviewMode = true
            useWideViewPort = true
            allowFileAccess = false
            allowContentAccess = false
        }
        web.isFocusable = true
        web.isFocusableInTouchMode = true
        web.addJavascriptInterface(Bridge(), "DZAndroid")
        web.webViewClient = ShellClient()
        web.webChromeClient = ShellChrome()

        if (savedInstanceState == null || web.restoreState(savedInstanceState) == null) {
            web.loadUrl(START_URL)
        }
        web.requestFocus()
        immersive()

        // Otomatik güncelleme: açılışta + 6 saatte bir (Updater kendi 6 saat sınırını da uygular)
        updater = Updater(this) { state, value -> notifyWeb(state, value) }
        periodic.postDelayed(periodicCheck, 5000)
    }

    /** Güncelleme durumunu web'e ilet: ready -> `DZUpdate.ready(ver)`, diğerleri -> `DZUpdate.status(state, value)`. */
    private fun notifyWeb(state: String, value: String) {
        val v = value.replace(Regex("[^0-9A-Za-z._-]"), "")
        val js = if (state == "ready") "window.DZUpdate&&DZUpdate.ready('$v');"
                 else "window.DZUpdate&&DZUpdate.status('$state','$v');"
        web.evaluateJavascript(js, null)
    }

    /* ---------------- tuşlar ---------------- */

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        val code = event.keyCode
        if (code == KeyEvent.KEYCODE_BACK) {
            if (event.action == KeyEvent.ACTION_DOWN) {
                if (customView != null) hideCustom() else sendKey("Escape", 27)
            }
            return true
        }
        val mapped = KEYS[code]
        if (mapped != null) {
            if (event.action == KeyEvent.ACTION_DOWN) sendKey(mapped.first, mapped.second)
            return true
        }
        return super.dispatchKeyEvent(event)
    }

    /** document'e keydown (key + keyCode/which; KeyboardEvent kurucusu keyCode'u ayarlayamaz -> defineProperty). */
    private fun sendKey(key: String, keyCode: Int) {
        val js = "(function(k,c){var d=document,e;try{e=new KeyboardEvent('keydown',{key:k,bubbles:true,cancelable:true});}" +
            "catch(x){e=d.createEvent('Event');e.initEvent('keydown',true,true);e.key=k;}" +
            "try{Object.defineProperty(e,'keyCode',{get:function(){return c;}});Object.defineProperty(e,'which',{get:function(){return c;}});}catch(x){}" +
            "(d.activeElement||d.body||d).dispatchEvent(e);})('" + key + "'," + keyCode + ");"
        web.evaluateJavascript(js, null)
    }

    /* ---------------- JS köprüsü ---------------- */

    private inner class Bridge {
        @JavascriptInterface
        fun exit() {
            runOnUiThread { finish() }
        }

        /** Uygulamanın sürümü (versionName). */
        @JavascriptInterface
        fun version(): String = updater.current

        /** İndirilip doğrulanmış, kurulmayı bekleyen sürüm ("" = yok); sayfa geç yüklenirse JS buradan sorar. */
        @JavascriptInterface
        fun pendingUpdate(): String = updater.readyVersion ?: ""

        /** Elle denetim (6 saat sınırı yok); sonuç `DZUpdate.status/ready` ile gelir. */
        @JavascriptInterface
        fun checkUpdate() {
            runOnUiThread { updater.check(true) }
        }

        /** Kurulum ekranını aç (bilinmeyen kaynak izni yoksa önce ayarlar). */
        @JavascriptInterface
        fun installUpdate() {
            runOnUiThread { updater.install() }
        }
    }

    /* ---------------- WebView istemcileri ---------------- */

    private inner class ShellClient : WebViewClient() {
        override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
            intercept(request.url)

        @Deprecated("API < 21 yolu; minSdk 21'de çağrılmaz ama zararsız")
        override fun shouldInterceptRequest(view: WebView, url: String): WebResourceResponse? =
            intercept(Uri.parse(url))

        private fun intercept(uri: Uri): WebResourceResponse? {
            val path = uri.path ?: ""
            val hit = if (path.contains("/webapis/")) null else assetLoader.shouldInterceptRequest(uri)
            if (hit != null) return hit
            // Kendi alanımızda bulunamayan dosya (ör. Tizen `$WEBAPIS/webapis/webapis.js`): ağa gitmesin, hızlı 404.
            if (uri.host == HOST) {
                return WebResourceResponse("text/plain", "utf-8", 404, "Not Found",
                    emptyMap(), ByteArrayInputStream(ByteArray(0)))
            }
            return null
        }

        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
            request.url.host != HOST && request.isForMainFrame
    }

    private inner class ShellChrome : WebChromeClient() {
        override fun onShowCustomView(view: View, callback: CustomViewCallback) {
            if (customView != null) {
                callback.onCustomViewHidden(); return
            }
            customView = view
            customCallback = callback
            root.addView(view, FrameLayout.LayoutParams(MATCH, MATCH))
            web.visibility = View.GONE
            immersive()
        }

        override fun onHideCustomView() {
            removeCustom()
        }

        /** JS konsolu logcat'e (etiket DZWeb): hata ayıklama için. */
        override fun onConsoleMessage(msg: ConsoleMessage): Boolean {
            Log.d("DZWeb", "${msg.messageLevel()} ${msg.message()} (${msg.sourceId()}:${msg.lineNumber()})")
            return true
        }

        /** Boş poster (varsayılan gri oynat simgesi görünmesin). */
        override fun getDefaultVideoPoster(): Bitmap =
            Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888)
    }

    private fun hideCustom() {
        val cb = customCallback
        removeCustom()
        cb?.onCustomViewHidden()
    }

    private fun removeCustom() {
        val v = customView ?: return
        (v.parent as? ViewGroup)?.removeView(v)
        customView = null
        customCallback = null
        web.visibility = View.VISIBLE
        web.requestFocus()
        immersive()
    }

    /* ---------------- yaşam döngüsü / tam ekran ---------------- */

    @Suppress("DEPRECATION")
    private fun immersive() {
        window.decorView.systemUiVisibility = (View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
            or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
            or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
            or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
            or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
            or View.SYSTEM_UI_FLAG_FULLSCREEN)
        if (Build.VERSION.SDK_INT >= 28) {
            window.attributes = window.attributes.apply {
                layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            }
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) immersive()
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
        if (::updater.isInitialized) updater.onResume()
    }

    override fun onPause() {
        web.onPause()
        super.onPause()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    override fun onDestroy() {
        periodic.removeCallbacks(periodicCheck)
        (web.parent as? ViewGroup)?.removeView(web)
        web.destroy()
        super.onDestroy()
    }

    companion object {
        private const val HOST = WebViewAssetLoader.DEFAULT_DOMAIN
        private const val START_URL = "http://$HOST/assets/client/index.html"
        private const val MATCH = FrameLayout.LayoutParams.MATCH_PARENT

        /** Android tuşu -> (DOM key, keyCode); client/js/app.js KEYNAME + KEYMAP ile birebir. */
        private val KEYS: Map<Int, Pair<String, Int>> = mapOf(
            KeyEvent.KEYCODE_CHANNEL_UP to ("PageUp" to 33),
            KeyEvent.KEYCODE_CHANNEL_DOWN to ("PageDown" to 34),
            KeyEvent.KEYCODE_PAGE_UP to ("PageUp" to 33),
            KeyEvent.KEYCODE_PAGE_DOWN to ("PageDown" to 34),
            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE to ("MediaPlayPause" to 10252),
            KeyEvent.KEYCODE_HEADSETHOOK to ("MediaPlayPause" to 10252),
            KeyEvent.KEYCODE_MEDIA_PLAY to ("MediaPlay" to 415),
            KeyEvent.KEYCODE_MEDIA_PAUSE to ("MediaPause" to 19),
            KeyEvent.KEYCODE_MEDIA_STOP to ("MediaStop" to 413),
            KeyEvent.KEYCODE_MEDIA_FAST_FORWARD to ("MediaFastForward" to 417),
            KeyEvent.KEYCODE_MEDIA_REWIND to ("MediaRewind" to 412),
            KeyEvent.KEYCODE_PROG_RED to ("ColorF0Red" to 403),
            KeyEvent.KEYCODE_PROG_GREEN to ("ColorF1Green" to 404),
            KeyEvent.KEYCODE_PROG_YELLOW to ("ColorF2Yellow" to 405),
            KeyEvent.KEYCODE_PROG_BLUE to ("ColorF3Blue" to 406),
            KeyEvent.KEYCODE_CAPTIONS to ("ColorF2Yellow" to 405),
        )
    }
}
