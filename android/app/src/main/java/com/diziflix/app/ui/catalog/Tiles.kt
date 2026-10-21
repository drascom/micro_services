package com.diziflix.app.ui.catalog

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.diziflix.app.data.model.Item
import com.diziflix.app.domain.CatalogLogic
import com.diziflix.app.domain.SearchSourceLogic
import com.diziflix.app.domain.SourceTag
import com.diziflix.app.ui.common.PosterCard
import com.diziflix.app.ui.theme.DzColors

/** Katalog/arama ızgarasındaki poster + yıl · tür + durum notu. */
@Composable
fun CatalogTile(
    item: Item,
    isSearch: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier) {
        PosterCard(item = item, onClick = onClick)
        val yearText = item.year?.toString() ?: "Yıl bilinmiyor"
        Text(
            yearText + " · " + (if (item.isSeries) "Dizi" else "Film"),
            color = DzColors.Muted,
            style = MaterialTheme.typography.labelSmall,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        val note = CatalogLogic.statusNote(item, isSearch)
        if (note != null) {
            Text(
                note,
                color = DzColors.Warning,
                style = MaterialTheme.typography.labelSmall,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
        if (isSearch) SourceTags(item)
    }
}

/**
 * Arama sonucundaki kaynak etiketleri (`source_options`): ilk 2 + "+N"; `broken` uyarı işaretli, `unknown` soluk.
 * Etiketler yalnızca bilgidir (kartın dokunma davranışı değişmez).
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SourceTags(item: Item) {
    val tags = remember(item.sourceOptions) { SearchSourceLogic.tags(item.sourceOptions) }
    if (tags.isEmpty()) return
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
        modifier = Modifier.padding(top = 3.dp),
    ) {
        tags.forEach { tag ->
            val color = when (tag.kind) {
                SourceTag.Kind.Warn -> DzColors.Warning
                SourceTag.Kind.Faint -> DzColors.Muted.copy(alpha = 0.55f)
                SourceTag.Kind.More -> DzColors.Muted
                SourceTag.Kind.Normal -> DzColors.OnSurface
            }
            Text(
                tag.text,
                color = color,
                style = MaterialTheme.typography.labelSmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier
                    .background(DzColors.SurfaceHigh, RoundedCornerShape(4.dp))
                    .padding(horizontal = 5.dp, vertical = 1.dp),
            )
        }
    }
}
