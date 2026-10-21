package com.villabridge.android

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (
            intent?.action == Intent.ACTION_BOOT_COMPLETED ||
            intent?.action == Intent.ACTION_LOCKED_BOOT_COMPLETED ||
            intent?.action == Intent.ACTION_MY_PACKAGE_REPLACED ||
            intent?.action == QUICKBOOT_POWERON
        ) {
            if (RuntimeStateStore.desired(context) != RuntimeStateStore.DesiredState.RUNNING) return
            runCatching { NodeRuntimeService.start(context) }
                .onFailure { Log.e("VillaBoot", "Unable to start Villa Bridge after boot", it) }
        }
    }

    private companion object {
        /** Bazi Android tureviyle gelen hizli acilis yayini; `Intent` sabitlerinde yok. */
        const val QUICKBOOT_POWERON = "android.intent.action.QUICKBOOT_POWERON"
    }
}
