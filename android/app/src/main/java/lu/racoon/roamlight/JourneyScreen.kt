//  D'Ouverture vun engem Album: wéi ee bis dohinner komm ass.
//
//  ⚠ The website plays this once when an album opens, and the app did not have
//    it at all. It is not decoration: an album is a journey, and the first
//    thing you see should say where it went.
//
//  ⚠ The tiles come THROUGH the site (`/tiles/…`), not from openstreetmap.org.
//    The website fetches them directly -- a decision with a price, and one the
//    app does not have to repeat: a phone asking for tiles tells a stranger
//    roughly where the family's albums are.

package lu.racoon.roamlight

import android.graphics.BitmapFactory
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.PI
import kotlin.math.atan
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.floor
import kotlin.math.ln
import kotlin.math.sin
import kotlin.math.tan

private const val TILE = 256.0

/** Web Mercator -- the projection every slippy map uses. 0…1 on both axes, so
 *  the zoom is only a multiplication afterwards. */
private fun project(lat: Double, lon: Double): Pair<Double, Double> {
    val la = lat.coerceIn(-85.05112878, 85.05112878) * PI / 180
    return Pair((lon + 180) / 360, (1 - ln(tan(la) + 1 / cos(la)) / PI) / 2)
}

private fun unproject(x: Double, y: Double): Pair<Double, Double> {
    val lon = x * 360 - 180
    val n = PI - 2 * PI * y
    return Pair(180 / PI * atan(0.5 * (exp(n) - exp(-n))), lon)
}

/**
 * The points of one leg: the real road when there is one, otherwise an arc.
 *
 * ⚠ A straight line between two far-apart places looks wrong on a flat map -- a
 *   flight is drawn as a curve on paper for the same reason.
 */
private fun legPoints(from: DoubleArray, leg: Journey.Leg): List<DoubleArray> {
    if (leg.route.size > 1) return leg.route
    val a = project(from[0], from[1])
    val b = project(leg.to[0], leg.to[1])
    val dx = b.first - a.first
    val dy = b.second - a.second
    val steps = 48
    return (0..steps).map { i ->
        val t = i.toDouble() / steps
        val lift = sin(t * PI) * 0.16
        val (lat, lon) = unproject(a.first + dx * t - dy * lift,
                                   a.second + dy * t + dx * lift)
        doubleArrayOf(lat, lon)
    }
}

private fun glyph(transport: String) = when (transport) {
    "plane" -> "✈"
    "train" -> "🚂"
    "bus" -> "🚌"
    else -> "🚗"
}

@Composable
fun JourneyOverlay(journey: Journey, onDone: () -> Unit) {
    val api = LocalApi.current
    var size by remember { mutableStateOf(IntSize.Zero) }
    var zoom by remember { mutableIntStateOf(5) }
    var originX by remember { mutableDoubleStateOf(0.0) }
    var originY by remember { mutableDoubleStateOf(0.0) }
    val tiles = remember { mutableStateMapOf<String, ImageBitmap>() }
    var start by remember { mutableStateOf(false) }

    val progress by animateFloatAsState(
        targetValue = if (start) 1f else 0f,
        animationSpec = tween(durationMillis = 3400, easing = LinearEasing),
        label = "journey",
    )

    val legs = remember(journey) {
        val out = mutableListOf<List<DoubleArray>>()
        var from = journey.from
        for (leg in journey.legs) {
            out.add(legPoints(from, leg))
            from = leg.to
        }
        out
    }

    // Choose the closest zoom at which the whole journey still fits, then fetch
    // exactly the tiles that are on screen.
    LaunchedEffect(size, journey) {
        if (size.width == 0 || size.height == 0) return@LaunchedEffect
        val pts = mutableListOf(journey.from)
        legs.forEach { pts.addAll(it) }
        val xs = pts.map { project(it[0], it[1]).first }
        val ys = pts.map { project(it[0], it[1]).second }
        val minX = xs.min(); val maxX = xs.max()
        val minY = ys.min(); val maxY = ys.max()
        var z = 2
        for (cand in 10 downTo 2) {
            val scale = (1 shl cand) * TILE
            if ((maxX - minX) * scale <= size.width * 0.82 &&
                (maxY - minY) * scale <= size.height * 0.82) { z = cand; break }
        }
        zoom = z
        val scale = (1 shl z) * TILE
        originX = (minX + maxX) / 2 * scale - size.width / 2.0
        originY = (minY + maxY) / 2 * scale - size.height / 2.0

        val span = 1 shl z
        val x0 = floor(originX / TILE).toInt()
        val x1 = floor((originX + size.width) / TILE).toInt()
        val y0 = floor(originY / TILE).toInt()
        val y1 = floor((originY + size.height) / TILE).toInt()

        // ⚠ The drawing starts NOW, not once every tile is in. Fifteen tiles
        //   fetched one after another is fifteen round trips before anything
        //   moves -- on a slow link that is a black screen for several seconds
        //   and then a journey. The path is what matters; the map fills in
        //   underneath it as the tiles land.
        start = true

        // ⚠ And they are fetched side by side, not in a queue.
        coroutineScope {
            for (tx in x0..x1) for (ty in y0..y1) {
                if (ty < 0 || ty >= span) continue
                val wx = ((tx % span) + span) % span
                val key = "$z/$wx/$ty"
                if (tiles.containsKey(key)) continue
                launch {
                    runCatching {
                        val d = api.tile(z, wx, ty)
                        BitmapFactory.decodeByteArray(d, 0, d.size)?.let {
                            tiles[key] = it.asImageBitmap()
                        }
                    }
                }
            }
        }
    }

    // ⚠ The animation does not decide when it is over -- this does. A journey
    //   nobody can leave is a trap.
    LaunchedEffect(start) {
        if (!start) return@LaunchedEffect
        delay(4200)
        onDone()
    }

    // ⚠ Not black behind the tiles. A tile that has not arrived yet leaves a
    //   hole, and a black hole in the middle of a map reads as broken; the
    //   site's own warm ground reads as map that has not painted yet. The
    //   difference is a colour, and it is the whole difference.
    Box(Modifier.fillMaxSize().background(Ink.groundWarm).onSizeChanged { size = it }) {
        Canvas(Modifier.fillMaxSize()) { drawJourney(this, journey, legs, tiles, zoom,
                                                     originX, originY, progress) }
        TextButton(onClick = onDone, modifier = Modifier.align(Alignment.TopEnd).padding(12.dp)) {
            Text("skip ×", color = Color.White.copy(alpha = 0.8f), fontSize = 13.sp)
        }
        journey.departure?.takeIf { it.isNotEmpty() }?.let { d ->
            Text(
                d + "  " + journey.legs.joinToString("") { glyph(it.transport) },
                color = Color.White.copy(alpha = 0.85f),
                fontSize = 12.sp,
                fontFamily = FontFamily.Monospace,
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(bottom = 22.dp)
                    .background(Color.Black.copy(alpha = 0.45f), RoundedCornerShape(50))
                    .padding(horizontal = 12.dp, vertical = 6.dp),
            )
        }
    }
}

private fun drawJourney(
    scope: DrawScope, journey: Journey, legs: List<List<DoubleArray>>,
    tiles: Map<String, ImageBitmap>, zoom: Int,
    originX: Double, originY: Double, progress: Float,
) = with(scope) {
    val scale = (1 shl zoom) * TILE
    fun at(lat: Double, lon: Double): Offset {
        val p = project(lat, lon)
        return Offset((p.first * scale - originX).toFloat(),
                      (p.second * scale - originY).toFloat())
    }

    // 1. The tiles, behind everything
    val span = 1 shl zoom
    val x0 = floor(originX / TILE).toInt()
    val x1 = floor((originX + size.width) / TILE).toInt()
    val y0 = floor(originY / TILE).toInt()
    val y1 = floor((originY + size.height) / TILE).toInt()
    for (tx in x0..x1) for (ty in y0..y1) {
        if (ty < 0 || ty >= span) continue
        val wx = ((tx % span) + span) % span
        val img = tiles["$zoom/$wx/$ty"] ?: continue
        drawImage(
            image = img,
            dstOffset = IntOffset((tx * TILE - originX).toInt(), (ty * TILE - originY).toInt()),
            dstSize = IntSize(TILE.toInt(), TILE.toInt()),
            alpha = 0.55f,
        )
    }

    // 2. The line, as far as it has got
    val total = legs.sumOf { maxOf(it.size - 1, 0) }
    if (total == 0) return@with
    var walked = 0
    var head: Offset? = null
    var headGlyph = "🚗"
    val ink = Color(0xFF6AA9E0)
    legs.forEachIndexed { i, pts ->
        val path = Path()
        var drawn = 0
        pts.forEachIndexed { k, p ->
            if ((walked + k).toFloat() / total > progress) return@forEachIndexed
            val o = at(p[0], p[1])
            if (k == 0) path.moveTo(o.x, o.y) else path.lineTo(o.x, o.y)
            drawn = k
        }
        if (drawn > 0) {
            drawPath(path, ink, style = Stroke(
                width = 2.5f * density,
                pathEffect = PathEffect.dashPathEffect(
                    floatArrayOf(7f * density, 6f * density))))
            head = at(pts[drawn][0], pts[drawn][1])
            headGlyph = glyph(journey.legs[minOf(i, journey.legs.size - 1)].transport)
        }
        walked += maxOf(pts.size - 1, 0)
    }

    // 3. The places, and the vehicle on the line
    val dots = mutableListOf(at(journey.from[0], journey.from[1]))
    journey.legs.forEach { dots.add(at(it.to[0], it.to[1])) }
    dots.forEach { drawCircle(ink, radius = 4f * density, center = it) }
    head?.let { h ->
        drawIntoCanvas { c ->
            val paint = android.graphics.Paint().apply {
                textSize = 26f * density
                textAlign = android.graphics.Paint.Align.CENTER
                isAntiAlias = true
            }
            c.nativeCanvas.drawText(headGlyph, h.x, h.y + 9f * density, paint)
        }
    }
}
