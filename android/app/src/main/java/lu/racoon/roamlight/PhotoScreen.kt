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
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation.NavHostController
import kotlin.math.max
import kotlin.math.min

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

    Box(Modifier.fillMaxSize().background(Color.Black)) {
        HorizontalPager(state = pager, modifier = Modifier.fillMaxSize()) { i ->
            ZoomableImage(photos[i].id)
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

/**
 * ⚠ The large view asks for 1200 and not 2800: on a phone you cannot see the
 *   difference, and on a mobile network the picture arrives in a second
 *   instead of three.
 */
@Composable
private fun ZoomableImage(id: Int) {
    var scale by remember(id) { mutableFloatStateOf(1f) }
    var dx by remember(id) { mutableFloatStateOf(0f) }
    var dy by remember(id) { mutableFloatStateOf(0f) }

    Box(
        Modifier
            .fillMaxSize()
            .pointerInput(id) {
                detectTransformGestures { _, pan, zoom, _ ->
                    scale = min(max(scale * zoom, 1f), 5f)
                    if (scale > 1f) { dx += pan.x; dy += pan.y } else { dx = 0f; dy = 0f }
                }
            }
            .pointerInput(id) {
                detectTapGestures(onDoubleTap = {
                    if (scale > 1f) { scale = 1f; dx = 0f; dy = 0f } else scale = 2.5f
                })
            },
        contentAlignment = Alignment.Center,
    ) {
        RemoteImage(
            id, 1200,
            Modifier
                .fillMaxSize()
                .graphicsLayer(scaleX = scale, scaleY = scale,
                               translationX = dx, translationY = dy),
            contentScale = ContentScale.Fit,
        )
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
