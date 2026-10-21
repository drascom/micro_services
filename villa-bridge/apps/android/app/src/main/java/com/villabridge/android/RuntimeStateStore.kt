package com.villabridge.android

import android.content.Context
import android.util.Base64
import java.io.File
import java.security.SecureRandom

object RuntimeStateStore {
    enum class DesiredState { RUNNING, STOPPED }

    private const val STATE_FILE = "villa-runtime-desired-state"
    private const val TOKEN_FILE = "villa-runtime-control-token"

    fun desired(context: Context): DesiredState {
        val value = runCatching { stateFile(context).readText().trim() }.getOrNull()
        return if (value == DesiredState.STOPPED.name) DesiredState.STOPPED else DesiredState.RUNNING
    }

    fun setDesired(context: Context, state: DesiredState) {
        writeAtomically(stateFile(context), state.name)
    }

    fun controlToken(context: Context): String {
        val file = tokenFile(context)
        runCatching { file.readText().trim() }
            .getOrNull()
            ?.takeIf { it.length >= 32 }
            ?.let { return it }

        val bytes = ByteArray(32).also(SecureRandom()::nextBytes)
        val token = Base64.encodeToString(bytes, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
        writeAtomically(file, token)
        return token
    }

    private fun stateFile(context: Context): File =
        File(context.createDeviceProtectedStorageContext().filesDir, STATE_FILE)

    private fun tokenFile(context: Context): File =
        File(context.createDeviceProtectedStorageContext().filesDir, TOKEN_FILE)

    private fun writeAtomically(destination: File, value: String) {
        destination.parentFile?.mkdirs()
        val temporary = File(destination.parentFile, "${destination.name}.new")
        temporary.writeText(value)
        check(temporary.renameTo(destination) || run {
            destination.delete()
            temporary.renameTo(destination)
        }) {
            "Unable to save ${destination.name}"
        }
    }
}
