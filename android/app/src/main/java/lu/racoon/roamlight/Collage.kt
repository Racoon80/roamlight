//  The album as the website shows it: prints scattered on a table.
//
//  ⚠ A port of the collage in `static/site.js` and `.snap` in `site.css`, with
//    their numbers -- change one side and the other should follow:
//      * rows that fill the width, each photograph at its own proportions (the
//        last row keeps the target height instead of being blown up),
//      * target row height 170 dp on a phone, 250 on a tablet,
//      * the prints overlap by 2 × 7 dp,
//      * each is turned by up to ±5°, and which one lies on top is a fixed
//        "random" per position -- the same formula as site.js, so the same album
//        looks the same every time,
//      * a white border (4 dp on a phone, 6 on a tablet) and a shadow.

package lu.racoon.roamlight

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import kotlin.math.floor
import kotlin.math.sin

/** The fixed "random" of site.js: `sin(i * 99.13 + 0.7) * 10000`, fractional part. */
fun collageRandom(i: Int): Double {
    val x = sin(i * 99.13 + 0.7) * 10000
    return x - floor(x)
}

private const val MARGIN = 7f          // == MARGIN in site.js

private class Placed(val index: Int, val photo: Photo, val x: Float, val w: Float)
private class CollageRow(val items: List<Placed>, val h: Float)

private fun rows(photos: List<Photo>, width: Float): List<CollageRow> {
    if (width <= 0f) return emptyList()
    val targetH = if (width < 640f) 170f else 250f
    val gap = -2 * MARGIN
    val out = mutableListOf<CollageRow>()
    var row = mutableListOf<Triple<Int, Photo, Float>>()
    var sumAR = 0f

    fun flush(last: Boolean) {
        if (row.isEmpty()) return
        var h = (width - gap * (row.size - 1)) / sumAR
        if (last) h = minOf(h, targetH)
        // A short last row sits in the middle, as `justify-content: center`.
        val used = row.sumOf { (it.third * h).toDouble() }.toFloat() + gap * (row.size - 1)
        var x = (width - used) / 2f
        val placed = row.map { (i, p, ar) ->
            Placed(i, p, x, ar * h).also { x += ar * h + gap }
        }
        out += CollageRow(placed, h)
        row = mutableListOf(); sumAR = 0f
    }
    photos.forEachIndexed { i, p ->
        val ar = if (p.width == null || p.height == null) 1.5f else p.ratio.coerceIn(0.2f, 5f)
        row += Triple(i, p, ar); sumAR += ar
        if (sumAR * targetH + gap * (row.size - 1) >= width) flush(false)
    }
    flush(true)
    return out
}

@Composable
fun Collage(
    photos: List<Photo>,
    modifier: Modifier = Modifier,
    head: @Composable () -> Unit = {},
    onLastRow: () -> Unit = {},
    cell: @Composable (index: Int, photo: Photo) -> Unit,
) {
    BoxWithConstraints(modifier.fillMaxWidth()) {
        // Room for the corners of a turned print at the screen's edge.
        val side = 14.dp
        val w = (maxWidth - side * 2).value
        val all = rows(photos, w)
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 32.dp)) {
            item { head() }
            items(all, key = { it.items.first().photo.id }) { r ->
                // ⚠ The row takes up its height MINUS the overlap; the prints
                //   are drawn at full size and spill onto the row above, the
                //   way the site's negative margin does it. Later rows are
                //   drawn later, so they lie on top.
                Box(Modifier.fillMaxWidth().padding(horizontal = side)
                        .height(Dp(r.h - 2 * MARGIN))) {
                    r.items.forEach { it ->
                        Box(Modifier
                            .offset(x = Dp(it.x), y = Dp(-MARGIN))
                            .size(Dp(it.w), Dp(r.h))
                            .zIndex(((collageRandom(it.index + 7) * 8).toInt() + 1).toFloat())
                            .rotate(((collageRandom(it.index) * 2 - 1) * 5).toFloat())) {
                            cell(it.index, it.photo)
                        }
                    }
                }
                if (r === all.last()) LaunchedEffect(photos.size) { onLastRow() }
            }
        }
    }
}

/** One print: the photograph inside its white border, with the shadow. */
@Composable
fun Print(p: Photo, modifier: Modifier = Modifier) {
    Box(modifier
        .shadow(10.dp, RoundedCornerShape(2.dp))
        .background(Ink.paper, RoundedCornerShape(2.dp))
        .padding(4.dp)) {
        RemoteImage(p.id, 800, Modifier.fillMaxSize(), rev = p.rev ?: 0)
        if (p.isVideo) {
            // The site's play mark: a dark disc with a light triangle.
            Box(Modifier.align(Alignment.Center).size(34.dp)
                    .background(Color(0x8C14120F), CircleShape)
                    .border(1.dp, Ink.paper.copy(alpha = 0.55f), CircleShape),
                contentAlignment = Alignment.Center) {
                Text("▶", color = Ink.paper, fontSize = 13.sp)
            }
            p.durationS?.takeIf { it > 0 }?.let { s ->
                Text("%d:%02d".format(s / 60, s % 60), color = Ink.paper,
                     fontFamily = Type.mono, fontSize = 9.sp, letterSpacing = 0.6.sp,
                     modifier = Modifier.align(Alignment.BottomEnd).padding(6.dp)
                         .background(Color(0xB314120F), RoundedCornerShape(2.dp))
                         .padding(horizontal = 5.dp, vertical = 2.dp))
            }
        }
    }
}
