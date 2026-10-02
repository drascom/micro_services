package com.diziflix.app.data

import com.diziflix.app.data.net.UrlUtil
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class UrlUtilTest {
    private val base = "http://192.168.0.61:8090"

    @Test
    fun normalizeBaseUrl_addsSchemeTrimsAndStripsTrailingSlashes() {
        assertEquals("http://192.168.0.61:8090", UrlUtil.normalizeBaseUrl("192.168.0.61:8090"))
        assertEquals("http://192.168.0.61:8090", UrlUtil.normalizeBaseUrl("  http://192.168.0.61:8090/  "))
        assertEquals("https://sunucu.local", UrlUtil.normalizeBaseUrl("https://sunucu.local//"))
        assertEquals("http://sunucu.local:1234", UrlUtil.normalizeBaseUrl("sunucu.local:1234"))
    }

    @Test
    fun normalizeBaseUrl_rejectsInvalidInput() {
        assertNull(UrlUtil.normalizeBaseUrl(null))
        assertNull(UrlUtil.normalizeBaseUrl(""))
        assertNull(UrlUtil.normalizeBaseUrl("   "))
        assertNull(UrlUtil.normalizeBaseUrl("ftp://sunucu"))
        assertNull(UrlUtil.normalizeBaseUrl("http://"))
        assertNull(UrlUtil.normalizeBaseUrl("http://bosluk var"))
    }

    @Test
    fun absolute_prefixesServerPaths_keepsAbsoluteUrls() {
        assertEquals("$base/img/a/card?w=1&h=2", UrlUtil.absolute(base, "/img/a/card?w=1&h=2"))
        assertEquals("$base/img/a", UrlUtil.absolute("$base/", "img/a"))
        assertEquals("https://cdn/x.jpg", UrlUtil.absolute(base, "https://cdn/x.jpg"))
        assertEquals("//cdn/x.jpg", UrlUtil.absolute(base, "//cdn/x.jpg"))
        assertEquals("data:image/png;base64,AAA", UrlUtil.absolute(base, "data:image/png;base64,AAA"))
        assertEquals("", UrlUtil.absolute(base, null))
        assertEquals("", UrlUtil.absolute(base, "  "))
    }

    @Test
    fun sized_keepsExistingDimensions_addsMissingOnes() {
        assertEquals("$base/img/a/portrait?w=300&h=450", UrlUtil.sized(base, "/img/a/portrait?w=300&h=450", 100, 100))
        assertEquals("$base/img/a/portrait?w=300&h=450", UrlUtil.sized(base, "/img/a/portrait", 300, 450))
        assertEquals("$base/img/a?x=1&w=5&h=6", UrlUtil.sized(base, "/img/a?x=1", 5, 6))
        assertEquals("", UrlUtil.sized(base, null, 5, 6))
    }

    @Test
    fun sizedTo_replacesDimensionsAndKeepsOtherParams() {
        assertEquals(
            "$base/img/s:s1/portrait?w=200&h=300",
            UrlUtil.sizedTo(base, "/img/s:s1/portrait?w=300&h=450", 200, 300),
        )
        assertEquals("$base/img/e/still?w=320&h=180", UrlUtil.sizedTo(base, "/img/e/still", 320, 180))
        assertEquals("$base/img/a?foo=1&w=1&h=2", UrlUtil.sizedTo(base, "/img/a?foo=1&w=9&h=9", 1, 2))
        assertEquals("", UrlUtil.sizedTo(base, "", 1, 2))
    }
}
