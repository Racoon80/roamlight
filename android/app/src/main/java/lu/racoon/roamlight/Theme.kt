//  D'Faarwen aus `static/site.css`, sou datt d'App an de Site gläich ausgesinn.

package lu.racoon.roamlight

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

object Ink {
    val ground     = Color(0xFF1A1815)
    val groundWarm = Color(0xFF221F1B)
    val ink        = Color(0xFFF2EDE4)
    val inkSoft    = Color(0xFFC5BCAE)
    val inkMute    = Color(0xFF8D8375)
    val safelight  = Color(0xFF6AA9E0)
}

/**
 * ⚠ Always dark, and `isSystemInDarkTheme()` is deliberately ignored. The site
 *   itself is dark -- a light app next to it would be a different product, and
 *   photographs are looked at against a dark ground for a reason.
 */
@Composable
fun RoamlightTheme(content: @Composable () -> Unit) {
    @Suppress("UNUSED_EXPRESSION") isSystemInDarkTheme()
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Ink.safelight,
            onPrimary = Ink.ground,
            background = Ink.ground,
            onBackground = Ink.ink,
            surface = Ink.groundWarm,
            onSurface = Ink.ink,
            surfaceVariant = Ink.groundWarm,
            onSurfaceVariant = Ink.inkSoft,
            outline = Ink.inkMute,
        ),
        content = content,
    )
}
