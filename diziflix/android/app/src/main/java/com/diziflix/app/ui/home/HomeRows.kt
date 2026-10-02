package com.diziflix.app.ui.home

import com.diziflix.app.domain.ContinueLogic

/** Ana sayfa satır listesi dönüşümleri (saf; birim testli). */
internal object HomeRows {

    /** Yüklenmiş ve boşalmış Devam Et satırını listeden düşürür (başka satırlara dokunmaz; değişmezse aynı liste). */
    fun pruneHidden(rows: List<HomeRow>): List<HomeRow> =
        if (rows.none { ContinueLogic.isHiddenRow(it.id, it.loaded, it.items.size) }) {
            rows
        } else {
            rows.filterNot { ContinueLogic.isHiddenRow(it.id, it.loaded, it.items.size) }
        }

    /**
     * Başarılı kaldırmadan sonra [itemId] yapımının kartlarını YALNIZCA Devam Et satırından çıkarır;
     * satır boşalırsa satır gizlenir. Diğer satırlar (aynı yapım başka yerde görünebilir) aynen kalır.
     */
    fun withoutContinueCard(rows: List<HomeRow>, itemId: String): List<HomeRow> {
        if (rows.none { it.id == ContinueLogic.ROW_ID && it.items.any { card -> card.id == itemId } }) return rows
        val next = rows.map { row ->
            if (row.id != ContinueLogic.ROW_ID) return@map row
            val items = ContinueLogic.withoutItem(row.items, itemId)
            if (items === row.items) return@map row
            val removed = row.items.size - items.size
            row.copy(
                items = items,
                count = row.count?.let { (it - removed).coerceAtLeast(0) },
                total = row.total?.let { (it - removed).coerceAtLeast(0) },
            )
        }
        return pruneHidden(next)
    }
}
