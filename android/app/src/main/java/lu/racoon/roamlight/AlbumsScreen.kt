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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation.NavHostController

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
            else -> LazyVerticalGrid(
                columns = GridCells.Adaptive(150.dp),
                contentPadding = PaddingValues(12.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(albums, key = { it.id }) { a ->
                    AlbumCard(a) { nav.navigate("photos/${AlbumKey.of(a)}") }
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
                RemoteImage(album.cover, 400, Modifier.fillMaxSize())
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

    LaunchedEffect(album?.id, query, want) {
        if (want > pages && page > 0) return@LaunchedEffect
        loading = true
        try {
            val p = api.photos(album, page = want, query = query)
            pages = p.pages
            page = p.page
            // ⚠ Also dedupe by id: a photograph added while somebody scrolls
            //   shifts every page by one, and page 2 then repeats the last
            //   picture of page 1.
            val have = photos.mapTo(HashSet()) { it.id }
            photos = photos + p.photos.filter { have.add(it.id) }
            failed = null
        } catch (e: Exception) {
            failed = (e as? ApiError)?.message ?: e.message
        }
        loading = false
    }

    Column(Modifier.fillMaxSize().background(Ink.ground)) {
        Title(title)
        header()
        if (failed != null) {
            Center { Text(failed!!, color = MaterialTheme.colorScheme.error) }
            return@Column
        }
        LazyVerticalGrid(
            columns = GridCells.Adaptive(110.dp),
            contentPadding = PaddingValues(3.dp),
            horizontalArrangement = Arrangement.spacedBy(3.dp),
            verticalArrangement = Arrangement.spacedBy(3.dp),
            modifier = Modifier.weight(1f),
        ) {
            itemsIndexed(photos, key = { _, p -> p.id }) { i, p ->
                Box(
                    Modifier
                        .fillMaxWidth()          // pinned to the column -- see AlbumCard
                        .height(110.dp)
                        .clickable {
                            Viewing.open(photos, i)
                            nav.navigate("photo/${p.id}")
                        }
                ) {
                    RemoteImage(p.id, 400, Modifier.fillMaxSize())
                    if (p.isVideo) {
                        Text("▶", color = Ink.ink, fontSize = 20.sp,
                             modifier = Modifier.align(Alignment.Center))
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
