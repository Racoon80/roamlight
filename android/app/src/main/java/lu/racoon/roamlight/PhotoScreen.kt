//  Eng Foto, grouss -- an de Link fir en Album ze deelen.

package lu.racoon.roamlight

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation.NavHostController

@Composable
fun PhotoScreen(nav: NavHostController, id: Int) {
    val photos = remember { Viewing.photos }
    if (photos.isEmpty()) {
        // The process was killed while this was open -- go back to the grid
        // rather than show an empty black page.
        LaunchedEffect(Unit) { nav.popBackStack() }
        return
    }
    val start = remember { photos.indexOfFirst { it.id == id }.coerceAtLeast(0) }
    val pager = rememberPagerState(initialPage = start) { photos.size }

    // ⚠ The zoom belongs to the page being looked at and lives HERE, not
    //   inside the image: the pager has to know about it. While a photograph
    //   is zoomed, a sideways drag must move the PHOTOGRAPH and must not turn
    //   the page -- otherwise you end up looking at two photographs at once,
    //   half of each.
    var scale by remember { mutableFloatStateOf(1f) }
    var dx by remember { mutableFloatStateOf(0f) }
    var dy by remember { mutableFloatStateOf(0f) }
    var page by remember { mutableStateOf(IntSize.Zero) }

    // A new photograph starts unzoomed.
    LaunchedEffect(pager.currentPage) { scale = 1f; dx = 0f; dy = 0f }

    fun clamp() {
        val p = photos.getOrNull(pager.currentPage)
        val w = page.width.toFloat()
        val h = page.height.toFloat()
        if (w <= 0f || h <= 0f) return
        val r = p?.ratio ?: 1f
        // What the picture really occupies inside the page, ContentScale.Fit
        // being what it uses.
        val shownW = if (r > w / h) w else h * r
        val shownH = if (r > w / h) w / r else h
        val maxX = ((shownW * scale - w) / 2f).coerceAtLeast(0f)
        val maxY = ((shownH * scale - h) / 2f).coerceAtLeast(0f)
        dx = dx.coerceIn(-maxX, maxX)
        dy = dy.coerceIn(-maxY, maxY)
    }

    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black)
            .onSizeChanged { page = it }
    ) {
        HorizontalPager(
            state = pager,
            // ⚠ This is the fix for the second half of the bug: while the
            //   photograph is zoomed the pager does not take the drag at all.
            userScrollEnabled = scale <= 1.001f,
            modifier = Modifier.fillMaxSize(),
        ) { i ->
            Box(
                Modifier
                    .fillMaxSize()
                    // Clipped per page, so a zoomed photograph cannot spill
                    // over its neighbour.
                    .clipToBounds()
                    .pointerInput(i) {
                        detectTransformGestures { _, panBy, zoomBy, _ ->
                            if (i != pager.currentPage) return@detectTransformGestures
                            scale = (scale * zoomBy).coerceIn(1f, 5f)
                            if (scale <= 1.001f) {
                                scale = 1f; dx = 0f; dy = 0f
                            } else {
                                dx += panBy.x; dy += panBy.y
                                clamp()
                            }
                        }
                    }
                    .pointerInput(i) {
                        detectTapGestures(onDoubleTap = {
                            if (scale > 1.001f) { scale = 1f; dx = 0f; dy = 0f }
                            else { scale = 2.5f; clamp() }
                        })
                    },
                contentAlignment = Alignment.Center,
            ) {
                val mine = i == pager.currentPage
                RemoteImage(
                    photos[i].id, 1200,
                    rev = photos[i].rev ?: 0,
                    modifier = Modifier
                        .fillMaxSize()
                        .graphicsLayer(
                            scaleX = if (mine) scale else 1f,
                            scaleY = if (mine) scale else 1f,
                            translationX = if (mine) dx else 0f,
                            translationY = if (mine) dy else 0f,
                        ),
                    contentScale = ContentScale.Fit,
                )
            }
        }
        Caption(photos[pager.currentPage], Modifier.align(Alignment.BottomCenter))
    }
}

@Composable
private fun Caption(p: Photo, modifier: Modifier = Modifier) {
    val date = p.takenAt?.take(10)
    val place = p.place?.takeIf { it.isNotEmpty() }
    if (date == null && place == null) return
    Column(
        modifier
            .padding(bottom = 28.dp)
            .background(Color.Black.copy(alpha = 0.35f), RoundedCornerShape(50))
            .padding(horizontal = 12.dp, vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        if (date != null) {
            Text(date, color = Color.White.copy(alpha = 0.85f),
                 fontSize = 12.sp, fontFamily = FontFamily.Monospace)
        }
        if (place != null) {
            Text(place, color = Color.White.copy(alpha = 0.85f), fontSize = 11.sp)
        }
    }
}

// MARK: - Den Deel-Link

@Composable
fun ShareCard(link: ShareResult, onDone: () -> Unit, onSend: (String) -> Unit) {
    // ⚠ The password goes WITH the link in the same message. A family sends the
    //   link over one channel anyway -- and a link without its password is just
    //   a wall to the person opening it.
    val message = "${link.url}\n\nPassword: ${link.password}"

    Column(
        Modifier.fillMaxWidth().background(Ink.ground).padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(18.dp),
    ) {
        Text("A link for someone without an account.", color = Ink.inkSoft)

        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("LINK", color = Ink.inkMute, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
            Text(link.url, color = Ink.ink, fontSize = 13.sp, fontFamily = FontFamily.Monospace)
        }
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("PASSWORD", color = Ink.inkMute, fontSize = 11.sp,
                 fontFamily = FontFamily.Monospace)
            Text(link.password, color = Ink.safelight, fontSize = 20.sp,
                 fontFamily = FontFamily.Monospace)
        }
        link.expiresAt?.let {
            Text("Runs out ${it.take(10)}", color = Ink.inkMute, fontSize = 12.sp)
        }

        Button(onClick = { onSend(message) }, modifier = Modifier.fillMaxWidth()) {
            Text("Send")
        }
        TextButton(onClick = onDone, modifier = Modifier.fillMaxWidth()) { Text("Done") }
    }
}
