//  D'Albumen, an d'Fotoen dran.

package lu.racoon.roamlight

import androidx.compose.foundation.background
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
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
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.Slideshow
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed as listItemsIndexed
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.draw.shadow
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
    var editing by remember { mutableStateOf<Album?>(null) }
    var reload by remember { mutableIntStateOf(0) }

    // Long-press on an album: "Edit album" -- name, journey, who sees it, and
    // deleting it. The screen itself says when the album is not this person's.
    editing?.let { a ->
        EditAlbumDialog(a, onDismiss = { editing = null }) { reload += 1 }
    }

    LaunchedEffect(reload) {
        try {
            albums = api.albums(); failed = null
        } catch (e: Exception) {
            failed = (e as? ApiError)?.message ?: e.message
        }
        loading = false
    }

    Column(Modifier.fillMaxSize().background(Ink.ground)) {
        when {
            loading -> Center { CircularProgressIndicator(color = Ink.safelight) }
            failed != null -> Center {
                Text(failed!!, color = MaterialTheme.colorScheme.error)
            }
            albums.isEmpty() -> Center { Text("Nothing here yet.", color = Ink.inkMute) }
            // The site's home page: "The albums", then one numbered row per
            // album -- plate, name, country, count, and the cover as a print.
            else -> LazyColumn(contentPadding = PaddingValues(horizontal = 18.dp, vertical = 12.dp)) {
                item {
                    Row(Modifier.fillMaxWidth().padding(top = 16.dp, bottom = 14.dp),
                        verticalAlignment = Alignment.Bottom) {
                        Text("The albums", color = Ink.ink, fontFamily = Type.display,
                             fontSize = 30.sp, modifier = Modifier.weight(1f))
                        Text("${albums.size} ALBUM${if (albums.size == 1) "" else "S"}",
                             color = Ink.inkMute, fontFamily = Type.mono, fontSize = 10.sp,
                             letterSpacing = 1.6.sp)
                    }
                    Box(Modifier.fillMaxWidth().height(1.dp).background(Ink.rule))
                }
                listItemsIndexed(albums, key = { _, a -> a.id }) { i, a ->
                    AlbumRow(a, i + 1, onLongClick = if (LocalMayRemove.current) {
                        { editing = a }
                    } else null) { nav.navigate("photos/${AlbumKey.of(a)}") }
                }
            }
        }
    }
}

/** One album, the way the site's home page lists it (`.index__row`). */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun AlbumRow(album: Album, number: Int, onLongClick: (() -> Unit)?, onClick: () -> Unit) {
    Column(Modifier.combinedClickable(onClick = onClick, onLongClick = onLongClick)) {
        Row(Modifier.fillMaxWidth().padding(vertical = 18.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("PL.\u00A0%03d".format(number), color = Ink.safelight,
                     fontFamily = Type.mono, fontSize = 10.sp, letterSpacing = 1.8.sp)
                Text(album.title.ifEmpty { album.event }, color = Ink.ink,
                     fontFamily = Type.display, fontSize = 25.sp, lineHeight = 29.sp,
                     maxLines = 3, overflow = TextOverflow.Ellipsis)
                // The year is already in the title the site sends.
                Text("${album.country.uppercase()} · ${album.n} PLATE${if (album.n == 1) "" else "S"}",
                     color = Ink.inkMute, fontFamily = Type.mono, fontSize = 9.5.sp,
                     letterSpacing = 1.4.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            if (album.cover != null) {
                // The cover as a print, turned a little -- the site shows it
                // like that when the pointer passes over the row.
                Box(Modifier
                    .rotate(-1.5f)
                    .shadow(8.dp, RoundedCornerShape(2.dp))
                    .background(Ink.paper)
                    .padding(3.dp)
                    .size(78.dp, 54.dp)) {
                    RemoteImage(album.cover, 400, Modifier.fillMaxSize(), rev = album.coverRev ?: 0)
                }
            }
        }
        Box(Modifier.fillMaxWidth().height(1.dp).background(Ink.rule))
    }
}

// MARK: - Ewechhuelen

/**
 * Said BEFORE anything is deleted, not after.
 *
 * ⚠ "Delete" means something different depending on where a photograph came
 *   from, and the person pressing the button cannot see which it is. A
 *   photograph uploaded from a phone exists ONLY on the site -- its source is
 *   thrown away after conversion -- so it is gone for good. One from the family
 *   library stays in the library; the site only forgets it (and does not pick
 *   it up again on the next scan).
 */
const val REMOVAL_WARNING = "Photographs uploaded from a phone exist only on the site and " +
    "are gone for good. Photographs from the family library stay in the library; " +
    "only the site forgets them."

@Composable
fun ConfirmRemoval(title: String, action: String, onDismiss: () -> Unit, onConfirm: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(REMOVAL_WARNING) },
        confirmButton = {
            TextButton(onClick = onConfirm) {
                Text(action, color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun AlbumCard(album: Album, onLongClick: (() -> Unit)? = null, onClick: () -> Unit) {
    Column(
        Modifier.combinedClickable(onClick = onClick, onLongClick = onLongClick),
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

@OptIn(ExperimentalFoundationApi::class)
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
    var total by remember(album?.id, query) { mutableIntStateOf(0) }
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
            total = p.total
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

    // Ewechhuelen: eng Foto (laang drécken) oder de ganzen Album (Menü).
    val mayRemove = LocalMayRemove.current
    var doomedPhoto by remember(album?.id) { mutableStateOf<Photo?>(null) }
    var editing by remember(album?.id) { mutableStateOf(false) }
    var slideshow by remember(album?.id, query) { mutableStateOf(false) }
    if (slideshow) {
        SlideshowDialog(album, query, photos, total) { slideshow = false }
    }
    // ⚠ A dialog, not a line of text: on 28.09.2026 the site refused two
    //   deletions, the refusal was a small line nobody saw, and the photographs
    //   simply seemed not to go.
    var problem by remember(album?.id) { mutableStateOf<String?>(null) }
    problem?.let { msg ->
        AlertDialog(
            onDismissRequest = { problem = null },
            confirmButton = { TextButton(onClick = { problem = null }) { Text("OK") } },
            title = { Text("Not deleted") },
            text = { Text(msg) },
        )
    }
    if (editing && album != null) {
        EditAlbumDialog(album, onDismiss = { editing = false }) { left ->
            // Renamed or deleted: this album is not in the list under this
            // name any more -- back to the list, which loads afresh.
            if (left) nav.popBackStack() else reloadNow += 1
        }
    }
    doomedPhoto?.let { p ->
        ConfirmRemoval(
            title = "Delete this ${if (p.isVideo) "video" else "photograph"}?",
            action = "Delete",
            onDismiss = { doomedPhoto = null },
        ) {
            doomedPhoto = null
            scope.launch {
                try {
                    val r = api.removePhotos(listOf(p.id))
                    if (r.removed > 0) {
                        photos = photos.filter { it.id != p.id }
                        total = maxOf(0, total - 1)
                    } else {
                        problem = r.failed.firstOrNull()?.ifEmpty { null }
                            ?: "The site did not delete it."
                    }
                } catch (e: Exception) {
                    problem = (e as? ApiError)?.message ?: e.message
                }
            }
        }
    }

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
            val before = total
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
                // ⚠ The TOTAL, not how many are on screen. A reload replaces the
                //   list with the first page; in a big album the count on
                //   screen FALLS, and a new photograph sorts to the last page
                //   anyway. Comparing what is visible meant the wait always ran
                //   its full thirty seconds and then said they had not
                //   arrived -- while they were already there.
                //
                // ⚠ `return@repeat` is CONTINUE, not break. The loop used to
                //   run all twenty rounds even when it was long since done.
                sent = "$done sent — the site is converting."
                var arrived = false
                for (round in 0 until 20) {
                    reloadNow += 1
                    kotlinx.coroutines.delay(1500)
                    if (total >= before + done) { arrived = true; break }
                }
                sent = if (arrived) "$done added."
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
            // An album carries its title in the page, in Bodoni (see AlbumHead);
            // the search keeps its plain one.
            Box(Modifier.weight(1f)) { if (album == null) Title(title) }
            if (photos.isNotEmpty()) {
                IconButton(onClick = { slideshow = true }) {
                    Icon(Icons.Filled.Slideshow, contentDescription = "Slideshow",
                         tint = Ink.safelight)
                }
            }
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
                if (mayRemove) {
                    IconButton(onClick = { editing = true }) {
                        Icon(Icons.Filled.Tune, contentDescription = "Edit album",
                             tint = Ink.inkSoft)
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
        Collage(
            photos = photos,
            modifier = Modifier.weight(1f),
            head = {
                if (album != null) AlbumHead(album, total)
            },
            onLastRow = {
                // The next page is asked for when the LAST row appears -- not
                // at a scroll offset, so a fast swipe does not fire twice.
                if (page < pages && claimed.add(page + 1)) want = page + 1
            },
        ) { i, p ->
            Print(p, Modifier.fillMaxSize().combinedClickable(
                onClick = {
                    Viewing.open(photos, i)
                    nav.navigate("photo/${p.id}")
                },
                onLongClick = if (mayRemove && p.mayRemove != false) {
                    { doomedPhoto = p }
                } else null,
            ))
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

/** The site's album head: a plate with year and country, the title in Bodoni,
 *  and how many plates. */
@Composable
private fun AlbumHead(a: Album, total: Int) {
    Column(Modifier.fillMaxWidth().padding(start = 18.dp, end = 18.dp, bottom = 10.dp),
           verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("${a.year} · ${a.country.uppercase()}", color = Ink.safelight,
             fontFamily = Type.mono, fontSize = 10.sp, letterSpacing = 1.8.sp)
        Text(a.title.ifEmpty { a.event }, color = Ink.ink, fontFamily = Type.display,
             fontSize = 34.sp, lineHeight = 38.sp)
        if (total > 0) {
            Text("$total PLATE${if (total == 1) "" else "S"}", color = Ink.inkMute,
                 fontFamily = Type.mono, fontSize = 10.sp, letterSpacing = 1.6.sp)
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
