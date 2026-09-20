//  En neien Album uleeën.
//
//  ⚠ This tab used to be a general upload form, with a picker for choosing an
//    album you already have. That was one thing done twice: adding to an album
//    that exists belongs INSIDE that album, where you are already looking at
//    it, and that is where the `+` in its bar does it. What was missing was the
//    other half — making a new album at all — and there was no way to do that
//    from the app.
//
//  ⚠ The server has no "create album" call, and does not need one: an album IS
//    the year, the country and the name. Name three that do not exist yet, send
//    photographs with them, and the album exists. Exactly what the web form
//    does, and the same road: batch → file → chunk → done → commit.

package lu.racoon.roamlight

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.ArrowForward
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation.NavHostController
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.Calendar

@Composable
fun UploadScreen(nav: NavHostController) {
    val api = LocalApi.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val thisYear = remember { Calendar.getInstance().get(Calendar.YEAR).toString() }

    var name by remember { mutableStateOf("") }
    var year by remember { mutableStateOf(thisYear) }
    var country by remember { mutableStateOf("") }
    var place by remember { mutableStateOf("") }
    var picked by remember { mutableStateOf<List<Uri>>(emptyList()) }

    var albums by remember { mutableStateOf<List<Album>>(emptyList()) }
    var places by remember { mutableStateOf<List<String>>(emptyList()) }

    var running by remember { mutableStateOf(false) }
    var done by remember { mutableIntStateOf(0) }
    var made by remember { mutableStateOf<Album?>(null) }
    var note by remember { mutableStateOf<String?>(null) }
    var failed by remember { mutableStateOf<String?>(null) }
    /**
     * ⚠ One line per photograph that would not go up -- collected, not thrown.
     * The run carries on, and at the end the person is told the whole truth
     * instead of the first half of it.
     */
    var trouble by remember { mutableStateOf<List<String>>(emptyList()) }
    /** What the site says while it files them away ("143 of 247"). */
    var filing by remember { mutableStateOf<String?>(null) }

    /**
     * ⚠ Swallows its errors on purpose: these lists are a convenience. If they
     *   cannot be fetched the fields still work and an album can still be made
     *   -- an empty picker must not become a locked form.
     */
    suspend fun refreshLists() {
        runCatching { albums = api.albums() }
        runCatching { places = api.facets().places }
    }
    LaunchedEffect(Unit) { refreshLists() }

    val picker = rememberLauncherForActivityResult(
        ActivityResultContracts.PickMultipleVisualMedia(100)
    ) { picked = it }

    // ⚠ Compared on the same key the server uses (`<year>/<country>/<name>`),
    //   so "already there" here means exactly what it means over there.
    val existing = albums.firstOrNull {
        it.year == year.trim() && it.country == country.trim() && it.event == name.trim()
    }
    val named = name.isNotBlank() && year.isNotBlank() && country.isNotBlank()
    val ready = named && picked.isNotEmpty() && !running

    val years = remember(albums) { (albums.map { it.year } + thisYear).distinct().sortedDescending() }
    val countries = remember(albums) { albums.map { it.country }.distinct().sorted() }

    Column(
        Modifier.fillMaxSize().background(Ink.ground).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Title("New album")
        Column(Modifier.padding(horizontal = 16.dp),
               verticalArrangement = Arrangement.spacedBy(14.dp)) {

            Label("The album")
            Combo("Name of the album", name, emptyList()) { name = it }
            Combo("Year", year, years, KeyboardType.Number) { year = it }
            Combo("Country", country, countries) { country = it }
            Combo("Place / town (for the map)", place, places) { place = it }

            // ⚠ Say the truth about what will happen. Naming an album that is
            //   already there does NOT fail and does not make a second one --
            //   the photographs simply go into the one that exists. Letting
            //   somebody find that out afterwards would be a surprise.
            Hint(
                when {
                    existing != null ->
                        "${existing.title} already exists — these will go into it, " +
                            "next to the ${existing.n} already there."
                    named -> "New album: ${year.trim()} / ${country.trim()} / ${name.trim()}"
                    else -> "The year, the country and the name are what make an album."
                }
            )

            Label("Photographs")
            OutlinedButton(
                onClick = {
                    picker.launch(PickVisualMediaRequest(
                        ActivityResultContracts.PickVisualMedia.ImageAndVideo))
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(if (picked.isEmpty()) "Pick photographs" else "${picked.size} picked")
            }

            Button(
                onClick = {
                    scope.launch {
                        running = true; failed = null; note = null; made = null; done = 0
                        trouble = emptyList(); filing = null
                        val wasNew = existing == null
                        try {
                            val batch = api.newBatch()
                            picked.forEachIndexed { i, uri ->
                                // ⚠ One photograph that will not go up must NOT
                                //   take the other 246 with it. This used to sit
                                //   inside the one `try` around everything: a
                                //   single chunk failing (a handover from wifi to
                                //   the mobile network is enough) ended the whole
                                //   run there and then.
                                try {
                                    val data = withContext(Dispatchers.IO) {
                                        context.contentResolver.openInputStream(uri)
                                            ?.use { it.readBytes() }
                                    } ?: return@forEachIndexed
                                    // ⚠ There HAS to be a name: the server hangs
                                    //   the extension off it and tells from that
                                    //   what kind of file it is.
                                    val ext = context.contentResolver.getType(uri)
                                        ?.substringAfterLast('/')?.substringBefore(';')
                                        ?.let { if (it == "jpeg") "jpg" else it } ?: "jpg"
                                    val fid = api.addFile(batch, "IMG_${i + 1}.$ext", data.size)
                                    var offset = 0
                                    var tries = 0
                                    while (offset < data.size) {
                                        val end = minOf(offset + Api.CHUNK, data.size)
                                        try {
                                            offset = api.sendChunk(
                                                batch, fid, offset,
                                                data.copyOfRange(offset, end))
                                            tries = 0
                                        } catch (e: Exception) {
                                            // ⚠ Three goes, a moment apart. A
                                            //   mobile network drops a packet; that
                                            //   is not a reason to lose an album.
                                            tries += 1
                                            if (tries >= 3) throw e
                                            delay(1000L * tries)
                                        }
                                    }
                                    api.finishFile(batch, fid)
                                    done += 1
                                } catch (e: Exception) {
                                    trouble = trouble +
                                        ((e as? ApiError)?.message ?: e.message ?: "failed")
                                }
                            }
                            if (done == 0) {
                                failed = trouble.firstOrNull() ?: "Nothing could be sent."
                                running = false
                                return@launch
                            }
                            api.commit(batch, year.trim(), country.trim(),
                                       name.trim(), place.trim())

                            // ⚠ The commit answers at once; the site files them
                            //   away in its own time. So ask how far it has got
                            //   instead of waiting for an answer that is not
                            //   coming. It gives up ASKING after five minutes,
                            //   not the upload -- the photographs are on the site
                            //   either way and the album turns up on its own.
                            // ⚠ A `for` with `break`, not `repeat { return@repeat }`:
                            //   `return@repeat` leaves the LAMBDA, which is a
                            //   `continue` -- the loop would race through its
                            //   turns with no pause, asking the site as fast as
                            //   it can.
                            var lastFailed = emptyList<String>()
                            for (turn in 0 until 150) {
                                val st = runCatching { api.uploadStatus(batch) }.getOrNull()
                                if (st != null) {
                                    filing = "${st.settled} of ${st.total}"
                                    lastFailed = st.failed
                                    if (st.isDone) break
                                }
                                delay(2000)
                            }
                            // ⚠ The LAST answer only. A file that has gone wrong
                            //   is in every answer from then on, so adding them up
                            //   would report one bad photograph a hundred times.
                            trouble = trouble + lastFailed
                            filing = null

                            note = "$done sent" +
                                (if (wasNew) " — the album is being made."
                                 else " — the site is converting them.")
                            picked = emptyList()

                            // ⚠ The album is only really there once the site has
                            //   converted the first photograph: until then it is
                            //   a folder with nothing on the site in it, and it
                            //   does not appear in the list. So wait for it --
                            //   otherwise "Create the album" ends with nothing
                            //   to show for it.
                            // ⚠ `for`/`break` for the same reason as above: this
                            //   said `return@repeat`, which only ends that one
                            //   turn of the lambda. So once the album HAD been
                            //   found it kept going round, nineteen more times,
                            //   with no pause between them.
                            for (turn in 0 until 20) {
                                refreshLists()
                                val found = albums.firstOrNull {
                                    it.year == year.trim() && it.country == country.trim() &&
                                        it.event == name.trim()
                                }
                                if (found != null) {
                                    made = found
                                    note = "$done photograph${if (done == 1) "" else "s"} " +
                                        "in ${found.title}."
                                    break
                                }
                                delay(1500)
                            }
                            if (made == null) {
                                note = "$done sent. The album appears once the site has " +
                                    "converted them."
                            }
                        } catch (e: Exception) {
                            failed = (e as? ApiError)?.message ?: e.message
                        }
                        if (trouble.isNotEmpty()) {
                            note = (note ?: "") + " ${trouble.size} did not go up."
                            failed = trouble.take(3).joinToString(" · ")
                        }
                        filing = null
                        running = false
                    }
                },
                enabled = ready,
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (running) {
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp,
                                                  color = Ink.ground)
                        Text("$done of ${picked.size}…")
                    }
                } else {
                    Text(if (existing == null) "Create the album" else "Add to the album")
                }
            }

            // ⚠ While the site is filing them away: say so, and say how far.
            //   The photographs are already there at this point -- a screen that
            //   said nothing is what made people think it had gone wrong.
            filing?.let { Text("Filing them away… $it", color = Ink.inkSoft, fontSize = 13.sp) }
            note?.let { Text(it, color = Ink.inkSoft, fontSize = 13.sp) }
            failed?.let { Text(it, color = MaterialTheme.colorScheme.error, fontSize = 13.sp) }
            made?.let { a ->
                OutlinedButton(onClick = { nav.navigate("photos/${AlbumKey.of(a)}") },
                               modifier = Modifier.fillMaxWidth()) {
                    Icon(Icons.Filled.ArrowForward, contentDescription = null)
                    Spacer(Modifier.width(8.dp))
                    Text("Open ${a.title}")
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

// MARK: - Klengt Handwierksgeschir

@Composable
private fun Label(text: String) {
    Text(text.uppercase(), color = Ink.inkMute, fontSize = 11.sp,
         modifier = Modifier.padding(top = 6.dp))
}

@Composable
private fun Hint(text: String) {
    Text(text, color = Ink.inkSoft, fontSize = 12.sp)
}

/**
 * A text field you can also pick from.
 * ⚠ Typing stays possible on every one of them -- that IS how a new album is
 *   made.
 */
@Composable
private fun Combo(label: String, value: String, options: List<String>,
                  keyboard: KeyboardType = KeyboardType.Text,
                  onChange: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        OutlinedTextField(
            value = value,
            onValueChange = onChange,
            label = { Text(label) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = keyboard),
            trailingIcon = {
                if (options.isNotEmpty()) {
                    IconButton(onClick = { open = true }) {
                        Icon(Icons.Filled.ArrowDropDown, contentDescription = "Pick")
                    }
                }
            },
            modifier = Modifier.fillMaxWidth(),
        )
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            options.forEach { o ->
                DropdownMenuItem(text = { Text(o) },
                                 onClick = { open = false; onChange(o) })
            }
        }
    }
}
