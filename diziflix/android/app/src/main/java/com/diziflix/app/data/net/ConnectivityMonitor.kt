package com.diziflix.app.data.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Varsayılan ağın durumunu [ConnectivityManager] geri çağırmasıyla izler ([online]). Ağ geri gelince
 * ekranlar kendini yeniler, bekleyen ilerleme gönderilir. Kayıt başarısız olursa (kısıtlı cihaz /
 * izin) durum belirsiz kabul edilip true kalır; uygulama yine çalışır, yalnızca otomatik yenileme olmaz.
 */
class ConnectivityMonitor(context: Context) {

    private val manager = context.applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager

    private val _online = MutableStateFlow(currentlyOnline())

    /** true: cihazın internet yeteneği olan varsayılan ağı var. */
    val online: StateFlow<Boolean> = _online.asStateFlow()

    init {
        try {
            manager?.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    _online.value = true
                }

                override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                    _online.value = caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                }

                override fun onLost(network: Network) {
                    _online.value = false
                }
            })
        } catch (e: RuntimeException) {
            // SecurityException / TooManyRequestsException: geri çağırma yok, durum sorgusu yeter
        }
    }

    private fun currentlyOnline(): Boolean {
        val cm = manager ?: return true
        return try {
            val network = cm.activeNetwork ?: return false
            cm.getNetworkCapabilities(network)?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) ?: false
        } catch (e: SecurityException) {
            true
        }
    }
}
