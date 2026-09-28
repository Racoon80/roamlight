//  D'Faarwen aus `static/site.css`, sou datt d'App an de Site gläich ausgesinn.

package lu.racoon.roamlight

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight

object Ink {
    val ground     = Color(0xFF1A1815)
    val groundWarm = Color(0xFF221F1B)
    val ink        = Color(0xFFF2EDE4)
    val inkSoft    = Color(0xFFC5BCAE)
    val inkMute    = Color(0xFF8D8375)
    val safelight  = Color(0xFF6AA9E0)
    val rule       = Color(0x24F2EDE4)
    /** The white border of a print in the collage (`.snap` in site.css). */
    val paper      = Color(0xFFF4EFE6)
}

/** The site's three typefaces (static/fonts, converted to TTF). */
object Type {
    /** Bodoni Moda -- headings and album titles (`--display`). */
    val display = FontFamily(Font(R.font.bodoni_moda))
    /** Spectral -- running text (`--body`). */
    val body = FontFamily(Font(R.font.spectral),
                          Font(R.font.spectral_light_italic, FontWeight.Light, FontStyle.Italic))
    /** IBM Plex Mono -- plates, counts, dates (`--mono`). */
    val mono = FontFamily(Font(R.font.ibm_plex_mono),
                          Font(R.font.ibm_plex_mono_medium, FontWeight.Medium))
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
