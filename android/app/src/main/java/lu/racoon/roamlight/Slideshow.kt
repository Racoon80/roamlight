//  The slideshow: one photograph after another, full screen.
//
//  ⚠ The timing, and why (the same as the iPhone):
//    * 5 seconds a photograph (3 and 8 to choose from, remembered).
//    * a 1-second CROSSFADE, one photograph straight into the next -- built by
//      hand: the one before stays fully there UNDERNEATH while the next fades in
//      on top. Fading both at once dips into black halfway, every time.
//    * a slow drift and zoom while a photograph stands (Ken Burns), up to 6 %,
//      alternating direction. Not on a video: it moves by itself.
//    * a video plays to its end, then the show goes on.
//    * the screen stays on while the show runs, and only then.

package lu.racoon.roamlight

import android.content.Context
import android.net.Uri
import android.widget.VideoView
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.SkipPrevious
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private const val FADE_MS = 1000

@Composable
fun SlideshowDialog(album: Album?, query: String?, start: List<Photo>, total: Int,
                    onClose: () -> Unit) {
    val api = LocalApi.current
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("roamlight", Context.MODE_PRIVATE) }
    var seconds by remember { mutableFloatStateOf(prefs.getFloat("slide_seconds", 5f)) }
    var photos by remember { mutableStateOf(start) }
    var index by remember { mutableIntStateOf(0) }
    var previous by remember { mutableStateOf<Int?>(null) }
    var paused by remember { mutableStateOf(false) }
    var controls by remember { mutableStateOf(true) }
    val scope = rememberCoroutineScope()

    // The screen stays on -- only while the show runs.
    val view = LocalView.current
    DisposableEffect(Unit) {
        view.keepScreenOn = true
        onDispose { view.keepScreenOn = false }
    }

    fun step(by: Int) {
        if (photos.isEmpty()) return
        // Round and round: after the last comes the first again.
        val next = (index + by + photos.size) % photos.size
        if (next == index) return
        val was = index
        previous = was
        index = next
        scope.launch {
            delay(FADE_MS + 100L)
            if (previous == was) previous = null
        }
    }

    // The rest of the album, page by page, while the show is already running.
    LaunchedEffect(Unit) {
        if (album == null && query == null) return@LaunchedEffect
        var page = 1
        while (true) {
            val res = runCatching { api.photos(album, page, query) }.getOrNull() ?: break
            val known = photos.mapTo(HashSet()) { it.id }
            photos = photos + res.photos.filter { it.id !in known }
            if (res.page >= res.pages) break
            page += 1
        }
    }
    LaunchedEffect(Unit) { delay(2500); controls = false }

    // The clock. Restarted whenever the photograph, the pause or the speed
    // changes -- one effect, cancelled and begun again, never two running.
    LaunchedEffect(index, paused, seconds, photos.size) {
        val p = photos.getOrNull(index) ?: return@LaunchedEffect
        // The next photograph is fetched while this one stands -- otherwise
        // the crossfade would fade into an empty frame.
        photos.getOrNull((index + 1) % photos.size)?.takeIf { !it.isVideo }?.let { n ->
            launch { ImageStore.load(api, n.id, 2000, n.rev ?: 0) }
        }
        if (paused || p.isVideo) return@LaunchedEffect   // a video says when it is done
        delay((seconds * 1000).toLong())
        step(1)
    }

    Dialog(onDismissRequest = onClose,
           properties = DialogProperties(usePlatformDefaultWidth = false,
                                         decorFitsSystemWindows = false)) {
        Box(Modifier.fillMaxSize().background(Color.Black)
                .clickable(interactionSource = remember { MutableInteractionSource() },
                           indication = null) { controls = !controls }) {
            // Bottom first: the photograph before, then the one fading in.
            listOfNotNull(previous, index).distinct().forEach { i ->
                val p = photos.getOrNull(i) ?: return@forEach
                key(p.id) {
                    FadeIn {
                        if (p.isVideo) SlideVideo(p) { if (i == index) step(1) }
                        else KenBurns(p, i, seconds + FADE_MS / 1000f)
                    }
                }
            }

            AnimatedVisibility(controls, enter = fadeIn(), exit = fadeOut()) {
                Box(Modifier.fillMaxSize().systemBarsPadding().padding(16.dp)) {
                    Row(Modifier.fillMaxWidth().align(Alignment.TopCenter),
                        verticalAlignment = Alignment.CenterVertically) {
                        IconButton(onClick = onClose,
                                   modifier = Modifier.background(Color(0x73000000), RoundedCornerShape(50))) {
                            Icon(Icons.Filled.Close, contentDescription = "Close", tint = Color.White)
                        }
                        Spacer(Modifier.weight(1f))
                        photos.getOrNull(index)?.takenAt?.take(10)?.let { Pill(it) }
                        Spacer(Modifier.weight(1f))
                        Pill("${index + 1} / ${maxOf(total, photos.size)}")
                    }
                    Row(Modifier.align(Alignment.BottomCenter).padding(bottom = 12.dp)
                            .background(Color(0x80000000), RoundedCornerShape(50))
                            .padding(horizontal = 10.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        IconButton(onClick = { step(-1) }) {
                            Icon(Icons.Filled.SkipPrevious, "Back", tint = Color.White)
                        }
                        IconButton(onClick = { paused = !paused }) {
                            Icon(if (paused) Icons.Filled.PlayArrow else Icons.Filled.Pause,
                                 if (paused) "Play" else "Pause", tint = Color.White)
                        }
                        IconButton(onClick = { step(1) }) {
                            Icon(Icons.Filled.SkipNext, "Next", tint = Color.White)
                        }
                        // 3, 5 or 8 seconds -- remembered for next time.
                        listOf(3f, 5f, 8f).forEach { s ->
                            TextButton(onClick = {
                                seconds = s
                                prefs.edit().putFloat("slide_seconds", s).apply()
                            }) {
                                Text("${s.toInt()} s", fontFamily = Type.mono, fontSize = 12.sp,
                                     fontWeight = if (seconds == s) FontWeight.Medium else FontWeight.Normal,
                                     color = if (seconds == s) Ink.safelight else Color.White.copy(alpha = 0.8f))
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun Pill(text: String) {
    Text(text, color = Color.White, fontFamily = Type.mono, fontSize = 11.sp, letterSpacing = 1.2.sp,
         modifier = Modifier.background(Color(0x73000000), RoundedCornerShape(50))
             .padding(horizontal = 10.dp, vertical = 6.dp))
}

/** Fades its content in once, when it first appears -- and never out. */
@Composable
private fun FadeIn(content: @Composable () -> Unit) {
    val alpha = remember { Animatable(0f) }
    LaunchedEffect(Unit) { alpha.animateTo(1f, tween(FADE_MS, easing = FastOutSlowInEasing)) }
    Box(Modifier.fillMaxSize().graphicsLayer { this.alpha = alpha.value }) { content() }
}

/** A photograph that drifts slowly while it stands -- in and right, then out and left. */
@Composable
private fun KenBurns(p: Photo, index: Int, seconds: Float) {
    val t = remember { Animatable(0f) }
    LaunchedEffect(Unit) { t.animateTo(1f, tween((seconds * 1000).toInt(), easing = LinearEasing)) }
    val even = index % 2 == 0
    RemoteImage(p.id, 2000, Modifier.fillMaxSize().graphicsLayer {
        val k = t.value
        val s = if (even) 1f + 0.06f * k else 1.06f - 0.06f * k
        scaleX = s; scaleY = s
        translationX = (if (even) 10f else -10f) * k * density
        translationY = (if (even) -6f else 6f) * k * density
    }, rev = p.rev ?: 0, contentScale = ContentScale.Fit)
}

/** A video in the show: plays once, then says so. */
@Composable
private fun SlideVideo(p: Photo, done: () -> Unit) {
    val api = LocalApi.current
    var url by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(p.id) {
        url = runCatching { api.videoUrl(p.id) }.getOrNull()
        if (url == null) done()
    }
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        val u = url
        if (u == null) {
            RemoteImage(p.id, 1200, Modifier.fillMaxSize(), rev = p.rev ?: 0,
                        contentScale = ContentScale.Fit)
        } else {
            AndroidView(factory = { ctx ->
                VideoView(ctx).apply {
                    setVideoURI(Uri.parse(u))
                    setOnCompletionListener { done() }
                    setOnErrorListener { _, _, _ -> done(); true }
                    start()
                }
            }, onRelease = { it.stopPlayback() })
        }
    }
}
