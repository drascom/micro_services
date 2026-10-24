package com.diziflix.app

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.OpenableColumns
import java.io.File
import java.io.FileNotFoundException

/**
 * Yalnız güncelleme APK'sını (cache/updates/<ad>.apk) paket yükleyiciye okutan küçük sağlayıcı (androidx FileProvider yerine;
 * ek bağımlılık yok). Dışa kapalı; erişim yalnız `FLAG_GRANT_READ_URI_PERMISSION` ile. URI: content://<paket>.apk/<ad>.apk
 */
class ApkProvider : ContentProvider() {
    override fun onCreate() = true

    private fun fileFor(uri: Uri): File {
        val name = uri.lastPathSegment ?: throw FileNotFoundException()
        if (!name.matches(Regex("diziflix-[0-9.]+\\.apk"))) throw FileNotFoundException(name)
        val f = File(File(context!!.cacheDir, Updater.DIR), name)
        if (!f.isFile) throw FileNotFoundException(name)
        return f
    }

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor =
        ParcelFileDescriptor.open(fileFor(uri), ParcelFileDescriptor.MODE_READ_ONLY)

    override fun getType(uri: Uri) = Updater.MIME

    override fun query(uri: Uri, projection: Array<out String>?, selection: String?, args: Array<out String>?, sort: String?): Cursor {
        val f = fileFor(uri)
        return MatrixCursor(arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE)).apply { addRow(arrayOf<Any>(f.name, f.length())) }
    }

    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, args: Array<out String>?) = 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?, args: Array<out String>?) = 0
}
