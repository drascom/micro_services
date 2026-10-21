package com.diziflix.app

import android.app.Application
import coil.ImageLoader
import coil.ImageLoaderFactory
import coil.memory.MemoryCache

class DiziflixApp : Application(), ImageLoaderFactory {

    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }

    /**
     * Coil: görseller /img üzerinden küçük boyutlarda gelir (bkz. UrlUtil; istek başına tam hedef boyut).
     *  - Coil kendi disk önbelleğini yönettiği için ortak istemcinin OkHttp önbelleği kapatılır (çifte önbellek yok);
     *    disk önbelleği varsayılan (cacheDir/image_cache) korunur.
     *  - respectCacheHeaders(false): disk önbelleğindeki görsel 24 saat sonra da yeniden doğrulanmadan
     *    kullanılır; çevrimdışı açılışta afiş/avatarlar kaybolmaz, 304 gidiş-gelişleri de olmaz.
     *    (Değişen afiş: Ayarlar > Önbelleği temizle ile yenilenir.)
     *  - crossfade kapalı: kaydırırken her kart için ek animasyon/yeniden çizim yok.
     *  - bellek önbelleği: uygulama yığınının %25'i.
     */
    override fun newImageLoader(): ImageLoader {
        return ImageLoader.Builder(this)
            .okHttpClient(container.http.newBuilder().cache(null).build())
            .memoryCache { MemoryCache.Builder(this).maxSizePercent(0.25).build() }
            .respectCacheHeaders(false)
            .crossfade(false)
            .build()
    }
}
