package com.diziflix.app.data

import com.diziflix.app.data.model.DetailExtras
import com.diziflix.app.data.net.ApiJson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/** Detay yanıtındaki hedefli `actions[]` (API.md): var/yok/boş ayrımı TV eylem düğmelerini belirler. */
class DetailActionsParsingTest {

    private fun extras(json: String) = ApiJson.instance.decodeFromString(DetailExtras.serializer(), json)

    @Test
    fun actions_parsedWithKindEpisodeAndPosition() {
        val e = extras(
            """{"actions":[{"kind":"resume_episode","item_id":"x","episode_id":"x:s1:e2","position":1240},{"kind":"play_trailer","item_id":"x"}]}""",
        )
        val actions = e.actions
        assertNotNull(actions)
        assertEquals(listOf("resume_episode", "play_trailer"), actions!!.map { it.kind })
        assertEquals("x:s1:e2", actions[0].episodeId)
        assertEquals(1240.0, actions[0].position, 0.0)
        assertNull(actions[1].episodeId)
    }

    @Test
    fun actions_absentIsNull_emptyIsEmpty() {
        assertNull(extras("""{"cast":[]}""").actions)
        assertEquals(emptyList<Any>(), extras("""{"actions":[]}""").actions)
        assertNull(extras("""{"actions":null}""").actions)
    }
}
