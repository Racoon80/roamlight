//  D'Albumen, an d'Fotoen dran.

package lu.racoon.roamlight

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation.NavHostController
import kotlinx.coroutines.withContext
import kotlinx.coroutines.launch
import kotlinx.coroutines.Dispatchers
import androidx.compose.ui.platform.LocalContext
import androidx.compose.material.icons.filled.AddCircleOutline
import androidx.compose.material.icons.Icons
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.compose.rememberLauncherForActivityResult

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AlbumsScreen(nav: NavHostController) {
    val api = LocalApi.current
    var albums by remember { mutableStateOf<List<Album>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var failed by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        try {
            albums = api.albums(); failed = null
        } catch (e: Exception) {
            failed = (e as? ApiError)?.message ?: e.message
        }
        loading = false
    }

    Column(Modifier.fillMaxSize().background(Ink.ground)) {
        Title("Albums")
        when {
            loading -> Center { CircularProgressIndicator(color = Ink.safelight) }
            failed != null -> Center {
                Text(failed!!, color = MaterialTheme.colorScheme.error)
            }
            albums.isEmpty() -> Center { Text("Nothing here yet.", color = Ink.inkMute) }
            // ⚠ The gap lives INSIDE the cell, and the grid is told nothing
            //   about it. On iOS the same grid gave a phone its 12 pt between
            //   the cards and let them touch on an iPad: an adaptive grid
            //   decides for itself how to hand out the width it has left over,
            //   and a cell that fills its width eats the spacing on a wide
            //   screen. Padding inside the card cannot be handed out.
            else -> LazyVerticalGrid(
                columns = GridCells.Adaptive(150.dp),
                contentPadding = PaddingValues(6.dp),
            ) {
                items(albums, key = { it.id }) { a ->
                    Box(Modifier.padding(6.dp)) {
                        AlbumCard(a) { nav.navigate("photos/${AlbumKey.of(a)}") }
                    }
                }
            }
        }
    }
}

@Composable
private fun AlbumCard(album: Album, onClick: () -> Unit) {
    Column(
        Modifier.clickable(onClick = onClick),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Box(
            Modifier
                // ⚠ The width is pinned to the column, not left to the picture.
                //   A landscape photograph scaled to fill is wider than its cell;
                //   without this the card covers the one beside it and the grid
                //   looks as if it had no spacing at all. (The same bug bit the
                //   iOS app -- see AlbumsView.swift.)
                .fillMaxWidth()
                .height(118.dp)
                .clip(RoundedCornerShape(4.dp))
                .background(Ink.groundWarm)
        ) {
            if (album.cover != null) {
                RemoteImage(album.cover, 400, Modifier.fillMaxSize(), rev = album.coverRev ?: 0)
            }
        }
        // ⚠ TWO lines, always -- also when the title needs only one. Otherwise a
        //   short title makes the card shorter, the line underneath climbs up,
        //   and the counts in one row sit at three different heights.
        Text(
            album.title,
            color = Ink.ink,
            fontSize = 15.sp,
            lineHeight = 19.sp,
            minLines = 2,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            "${album.year} · ${album.n} photograph${if (album.n == 1) "" else "s"}",
            color = Ink.inkMute,
            fontSize = 11.sp,
            fontFamily = FontFamily.Monospace,
            maxLines = 1,
        )
    }
}

// MARK: - D'Fotoen an engem Album

@Composable
fun PhotosScreen(nav: NavHostController, key: String) {
    val album = remember(key) { AlbumKey.parse(key) }
    if (album == null) {
        LaunchedEffect(Unit) { nav.popBackStack() }
        return
    }
    PhotoGrid(nav, album, query = null, title = album.title.ifEmpty { album.event })
}

@Composable
fun PhotoGrid(
    nav: NavHostController,
    album: Album?,
    query: String?,
    title: String,
    header: @Composable () -> Unit = {},
) {
    val api = LocalApi.current
    var photos by remember(album?.id, query) { mutableStateOf<List<Photo>>(emptyList()) }
    var page by remember(album?.id, query) { mutableIntStateOf(0) }
    var pages by remember(album?.id, query) { mutableIntStateOf(1) }
    var loading by remember(album?.id, query) { mutableStateOf(false) }
    var failed by remember(album?.id, query) { mutableStateOf<String?>(null) }
    // ⚠ Which pages have been asked for, claimed the moment the last picture
    //   comes into view. Without it a fast scroll fires the same request
    //   several times, every answer is appended, and the same photographs come
    //   round again for ever.
    val claimed = remember(album?.id, query) { mutableSetOf<Int>() }
    var want by remember(album?.id, query) { mutableIntStateOf(1) }
    // Erhéijen heescht: kuck nach eng Kéier no, och wann d'Säitennummer
    // déiselwecht bleift.
    var reloadNow by remember(album?.id, query) { mutableIntStateOf(0) }

    LaunchedEffect(album?.id, query, want, reloadNow) {
        if (reloadNow == 0 && want > pages && page > 0) return@LaunchedEffect
        loading = true
        try {
            val asked = if (reloadNow > 0) 1 else want
            val p = api.photos(album, page = asked, query = query)
            pages = p.pages
            page = p.page
            if (asked == 1) {
                photos = p.photos
                claimed.clear()
                claimed.add(1)
            } else {
                // ⚠ Also dedupe by id: a photograph added while somebody
                //   scrolls shifts every page by one, and page 2 then repeats
                //   the last picture of page 1.
                val have = photos.mapTo(HashSet()) { it.id }
                photos = photos + p.photos.filter { have.add(it.id) }
            }
            failed = null
        } catch (e: Exception) {
            failed = (e as? ApiError)?.message ?: e.message
        }
        loading = false
    }

    // Fotoen an DËSEN Album bäisetzen. ⚠ Fir jiddereen deen den Album kucke
    // kann, net nëmme fir en Auteur -- genee wéi op der Websäit an op iOS.
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var sending by remember(album?.id) { mutableStateOf(false) }
    var sent by remember(album?.id) { mutableStateOf<String?>(null) }

    // ⚠ Once per album, like the website. Kept for as long as the app runs, so
    //   walking in and out of the same album does not replay it every time --
    //   that is charming the first time and tiresome the third.
    var journey by remember(album?.id) { mutableStateOf<Journey?>(null) }
    LaunchedEffect(album?.id) {
        val a = album ?: return@LaunchedEffect
        if (!Played.add(a.id)) return@LaunchedEffect
        // Swallowed on purpose: an album with no journey, or a site that cannot
        // look the place up, must still open. The opening is a gift, not a gate.
        runCatching { journey = api.journey(a) }
    }

    val picker = rememberLauncherForActivityResult(
        ActivityResultContracts.PickMultipleVisualMedia(30)
    ) { uris ->
        if (uris.isEmpty() || album == null) return@rememberLauncherForActivityResult
        scope.launch {
            sending = true
            sent = null
            var done = 0
            val before = photos.size
            try {
                uris.forEachIndexed { i, uri ->
                    val data = withContext(Dispatchers.IO) {
                        context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    } ?: return@forEachIndexed
                    val ext = context.contentResolver.getType(uri)
                        ?.substringAfterLast('/')?.substringBefore(';')
                        ?.let { if (it == "jpeg") "jpg" else it } ?: "jpg"
                    api.contribute(album, "IMG_${i + 1}.$ext", data)
                    done += 1
                }
                // ⚠ One reload is not enough, and that is not a race -- it is
                //   how the site works. A photograph is stored, then CONVERTED,
                //   and it only counts as being on the site once that has
                //   finished. Ask straight away and the album comes back
                //   without it, which is why it looked as though it only
                //   arrived after leaving the album and coming back in.
                sent = "$done sent — the site is converting."
                repeat(20) {
                    reloadNow += 1
                    kotlinx.coroutines.delay(1500)
                    if (photos.size >= before + done) return@repeat
                }
                sent = if (photos.size >= before + done) "$done added."
                       else "$done sent. They will appear once the site has converted them."
            } catch (e: Exception) {
                sent = (e as? ApiError)?.message ?: e.message
            }
            sending = false
        }
    }

    journey?.let { j ->
        JourneyOverlay(j) { journey = null }
        return
    }

    Column(Modifier.fillMaxSize().background(Ink.ground)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.weight(1f)) { Title(title) }
            if (album != null) {
                if (sending) {
                    CircularProgressIndicator(Modifier.padding(16.dp).size(20.dp),
                                              strokeWidth = 2.dp, color = Ink.safelight)
                } else {
                    IconButton(onClick = {
                        picker.launch(PickVisualMediaRequest(
                            ActivityResultContracts.PickVisualMedia.ImageAndVideo))
                    }) {
                        Icon(Icons.Filled.AddCircleOutline, contentDescription = "Add photographs",
                             tint = Ink.safelight)
                    }
                }
            }
        }
        sent?.let {
            Text(it, color = Ink.inkSoft, fontSize = 12.sp,
                 modifier = Modifier.padding(horizontal = 16.dp, vertical = 2.dp))
        }
        header()
        if (failed != null) {
            Center { Text(failed!!, color = MaterialTheme.colorScheme.error) }
            return@Column
        }
        // Selwechte Grond wéi bei den Albumen: den Ofstand steet an der Zell.
        LazyVerticalGrid(
            columns = GridCells.Adaptive(110.dp),
            contentPadding = PaddingValues(1.5.dp),
            modifier = Modifier.weight(1f),
        ) {
            itemsIndexed(photos, key = { _, p -> p.id }) { i, p ->
                Box(
                    Modifier
                        .padding(1.5.dp)
                        .fillMaxWidth()          // pinned to the column -- see AlbumCard
                        .height(110.dp)
                        .clickable {
                            Viewing.open(photos, i)
                            nav.navigate("photo/${p.id}")
                        }
                ) {
                    RemoteImage(p.id, 400, Modifier.fillMaxSize(), rev = p.rev ?: 0)
                    // ⚠ A video has to look like a video, and say how long it
                    //   runs -- the same mark as the iOS app carries.
                    if (p.isVideo) {
                        Row(
                            Modifier
                                .align(Alignment.BottomEnd)
                                .padding(4.dp)
                                .background(Color.Black.copy(alpha = 0.55f),
                                            RoundedCornerShape(50))
                                .padding(horizontal = 5.dp, vertical = 2.dp),
                            horizontalArrangement = Arrangement.spacedBy(3.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text("▶", color = Color.White, fontSize = 9.sp)
                            p.durationS?.takeIf { it > 0 }?.let { s ->
                                Text("%d:%02d".format(s / 60, s % 60),
                                     color = Color.White, fontSize = 9.sp,
                                     fontFamily = FontFamily.Monospace)
                            }
                        }
                    }
                }
                // The next page is asked for when the LAST picture appears --
                // not at a scroll offset, so a fast swipe does not fire twice.
                //
                // ⚠ In a LaunchedEffect and not in the composable body: writing
                //   state while composing schedules another composition, which
                //   writes again -- the grid would never settle.
                LaunchedEffect(i, photos.size) {
                    if (i == photos.lastIndex && page < pages && claimed.add(page + 1)) {
                        want = page + 1
                    }
                }
            }
        }
        if (loading) {
            LinearProgressIndicator(
                modifier = Modifier.fillMaxWidth(),
                color = Ink.safelight,
                trackColor = Ink.groundWarm,
            )
        }
    }
}

// MARK: - Klengt Handwierksgeschir

@Composable
fun Title(text: String) {
    Text(
        text,
        color = Ink.ink,
        fontSize = 26.sp,
        modifier = Modifier.padding(start = 16.dp, top = 20.dp, bottom = 8.dp, end = 16.dp),
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
    )
}

@Composable
fun Center(content: @Composable () -> Unit) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { content() }
}
