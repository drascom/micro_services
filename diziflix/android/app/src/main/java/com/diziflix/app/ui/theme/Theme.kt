package com.diziflix.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

object DzColors {
    val Background = Color(0xFF0F0F10)
    val Surface = Color(0xFF1A1A1D)
    val SurfaceHigh = Color(0xFF26262B)
    val Primary = Color(0xFFE50914)
    val OnSurface = Color(0xFFF2F2F2)
    val Muted = Color(0xFFA8A8B0)
    val Warning = Color(0xFFF5C518)
    /** Soluk altın (opak): seçili sezon arka planı. Beyaz metinle ~4.6:1; yarı saydam sarı koyu zeminde zeytin/siyah görünüyordu. */
    val WarningSoft = Color(0xFF85752A)
}

private val DarkScheme = darkColorScheme(
    primary = DzColors.Primary,
    onPrimary = Color.White,
    primaryContainer = DzColors.Primary,
    onPrimaryContainer = Color.White,
    secondary = DzColors.SurfaceHigh,
    onSecondary = DzColors.OnSurface,
    secondaryContainer = DzColors.SurfaceHigh,
    onSecondaryContainer = DzColors.OnSurface,
    background = DzColors.Background,
    onBackground = DzColors.OnSurface,
    surface = DzColors.Surface,
    onSurface = DzColors.OnSurface,
    surfaceVariant = DzColors.SurfaceHigh,
    onSurfaceVariant = DzColors.Muted,
    error = Color(0xFFFF6B6B),
    outline = Color(0xFF3A3A42),
)

/** Uygulama her zaman koyu temadır (TV arayüzü kendi temasını kullanır: `ui/tv/TvTheme`). */
@Composable
fun DiziflixTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = DarkScheme, content = content)
}
