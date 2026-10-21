package com.diziflix.app.domain

import com.diziflix.app.data.model.BootResponse
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.model.RowResponse
import com.diziflix.app.data.repo.DiziflixRepository
import kotlinx.coroutines.CancellationException

/**
 * "İzlemeye Devam Et" satırından kaldırma (uzun basış menüsü) — saf mantık + metinler (birim testli).
 * Sözleşme: `DELETE /api/continue/{item_id}` yumuşak gizlemedir; `item_id` = kartın `id`si (yapım id'si,
 * bölüm kartında da; `episode_id` DEĞİL).
 */
object ContinueLogic {
    const val ROW_ID = "continue"

    const val MENU_REMOVE = "Listeden kaldır"
    const val MENU_CANCEL = "Vazgeç"

    const val MSG_REMOVED = "Listeden kaldırıldı"
    const val MSG_FAILED = "Kaldırılamadı, bağlantıyı kontrol edin"
    const val MSG_OFFLINE = "İnternet bağlantısı gerekli"

    /** Uzun basış menüsü yalnızca Devam Et satırı kartlarında vardır. */
    fun hasRemoveMenu(rowId: String): Boolean = rowId == ROW_ID

    /** Sunucuya gönderilecek kimlik: yapım id'si (bölüm kartında `episode_id` değil). */
    fun removalId(item: Item): String = item.id

    /** [itemId] yapımına ait tüm kartlar çıkar (sunucu da yapım başına gizler). */
    fun withoutItem(items: List<Item>, itemId: String): List<Item> =
        if (items.any { it.id == itemId }) items.filter { it.id != itemId } else items

    /** Yüklenmiş ve boşalmış Devam Et satırı gösterilmez (boş satır başlığı bile kalmaz). */
    fun isHiddenRow(rowId: String, loaded: Boolean, itemCount: Int): Boolean =
        rowId == ROW_ID && loaded && itemCount == 0

    /** Saklı `row:<profil>:continue` kopyasından öğeyi çıkarır (çevrimdışı açılışta geri gelmesin). */
    fun rowWithout(row: RowResponse, itemId: String): RowResponse {
        val items = withoutItem(row.items, itemId)
        if (items === row.items) return row
        val removed = row.items.size - items.size
        return row.copy(items = items, total = (row.total - removed).coerceAtLeast(0))
    }

    /**
     * Saklı `boot:<profil>` kopyasından öğeyi çıkarır. Yüklenmiş satır boşalırsa satır tümden düşer;
     * yüklenmemiş (yalnız sayı taşıyan) satıra dokunulmaz, tembel satırın kendi kopyası ayrıca düzeltilir.
     */
    fun bootWithout(boot: BootResponse, itemId: String): BootResponse {
        var changed = false
        val rows = boot.rows.mapNotNull { row ->
            if (row.id != ROW_ID) return@mapNotNull row
            val items = withoutItem(row.items, itemId)
            if (items === row.items) return@mapNotNull row
            changed = true
            if (row.loaded && items.isEmpty()) return@mapNotNull null
            val removed = row.items.size - items.size
            row.copy(
                items = items,
                count = row.count?.let { (it - removed).coerceAtLeast(0) },
                total = row.total?.let { (it - removed).coerceAtLeast(0) },
            )
        }
        return if (changed) boot.copy(rows = rows) else boot
    }
}

/** Kaldırma denemesinin sonucu (kullanıcıya gösterilecek metin [message]). */
sealed class RemoveOutcome(val message: String) {
    /** Sunucu kabul etti (removed:false da başarıdır: idempotent). */
    data object Removed : RemoveOutcome(ContinueLogic.MSG_REMOVED)

    /** Cihaz çevrimdışı: istek hiç yapılmadı. */
    data object Offline : RemoveOutcome(ContinueLogic.MSG_OFFLINE)

    /** Ağ/sunucu hatası: kart yerinde kalır. */
    data object Failed : RemoveOutcome(ContinueLogic.MSG_FAILED)
}

/**
 * Kaldırma akışı: çevrimdışıysa istek yapmadan [RemoveOutcome.Offline]; aksi halde depo çağrılır
 * (başarıda yerel önbellek deponun içinde düzelir, hatada dokunulmaz).
 */
class ContinueRemover(
    private val repository: DiziflixRepository,
    private val isOnline: () -> Boolean,
) {
    suspend fun remove(profileId: String, item: Item): RemoveOutcome {
        if (!isOnline()) return RemoveOutcome.Offline
        return try {
            repository.removeFromContinue(ContinueLogic.removalId(item), profileId)
            RemoveOutcome.Removed
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            RemoveOutcome.Failed
        }
    }
}
