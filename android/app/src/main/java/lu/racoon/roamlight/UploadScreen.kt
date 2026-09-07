//  Eroplueden: Fotoen aus der Galerie huelen an dem Server seng Schrëtt goen
//  (batch -> file -> chunk -> done -> commit).
//
//  ⚠ This is **exactly** the path the web form takes. No second way into the
//    server, no shortcut for the app -- otherwise there would be two places
//    where a virus scan or a duplicate check could be forgotten.
//
//  ⚠ And, like the web form, it is "pick one or type a new one". An album is
//    created by naming one that does not exist yet -- so the existing ones have
//    to be visible, or the only way to add to last year's album is to remember
//    exactly how it was spelled.

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
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.Calendar

@Composable
fun UploadScreen() {
    val api = LocalApi.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val thisYear = remember { Calendar.getInstance().get(Calendar.YEAR).toString() }

    var picked by remember { mutableStateOf<List<Uri>>(emptyList()) }
    var year by remember { mutableStateOf(thisYear) }
    var event by remember { mutableStateOf("") }
    var country by remember { mutableStateOf("") }
    var place by remember { mutableStateOf("") }

    var albums by remember { mutableStateOf<List<Album>>(emptyList()) }
    var places by remember { mutableStateOf<List<String>>(emptyList()) }

    var running by remember { mutableStateOf(false) }
    var done by remember { mutableIntStateOf(0) }
    var total by remember { mutableIntStateOf(0) }
    var note by remember { mutableStateOf<String?>(null) }
    var failed by remember { mutableStateOf<String?>(null) }

    /**
     * ⚠ Swallows its errors on purpose: these lists are a convenience. If they
     *   cannot be fetched the four fields still work and an upload still goes
     *   through -- an empty picker must not become a locked form.
     */
    LaunchedEffect(Unit) {
        runCatching { albums = api.albums() }
        runCatching { places = api.facets().places }
    }

    val picker = rememberLauncherForActivityResult(
        ActivityResultContracts.PickMultipleVisualMedia(100)
    ) { picked = it }

    /**
     * The album the four fields point at, if it already exists.
     * ⚠ Compared on the same key the server uses (`<year>/<country>/<name>`),
     *   so "new" here means exactly what "new" means over there.
     */
    val existing = albums.firstOrNull {
        it.year == year.trim() && it.country == country.trim() && it.event == event.trim()
    }
    val isNew = year.isNotBlank() && country.isNotBlank() && event.isNotBlank() &&
        existing == null
    val ready = picked.isNotEmpty() && year.isNotBlank() && event.isNotBlank() &&
        country.isNotBlank() && !running

    val years = remember(albums) { (albums.map { it.year } + thisYear).distinct().sortedDescending() }
    val countries = remember(albums) { albums.map { it.country }.distinct().sorted() }

    Column(
        Modifier.fillMaxSize().background(Ink.ground)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Title("Upload")
        Column(Modifier.padding(horizontal = 16.dp),
               verticalArrangement = Arrangement.spacedBy(14.dp)) {

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

            Label("Album")
            Picker(
                shown = existing?.title ?: "Choose an album",
                options = buildList {
                    add("New album…" to Album(thisYear, "", "", "", 0, null, null))
                    albums.forEach { add("${it.title} · ${it.country} · ${it.n}" to it) }
                },
            ) { a ->
                year = a.year.ifEmpty { thisYear }
                country = a.country
                event = a.event
                if (a.event.isEmpty()) place = ""
            }
            Hint(
                if (albums.isEmpty())
                    "Nothing here yet — type the four fields below and it is created."
                else "Pick one, or fill in the fields below to make a new one."
            )

            Label("Where they belong")
            // ⚠ The same four fields as on the site, in the same order: year,
            //   name, country, place. The server lays out the folder.
            Combo("Year", year, years, KeyboardType.Number) { year = it }
            Combo("Name of the album", event, emptyList()) { event = it }
            Combo("Country", country, countries) { country = it }
            Combo("Place / town (for the map)", place, places) { place = it }

            Hint(
                when {
                    isNew -> "New album: ${year.trim()} / ${country.trim()} / ${event.trim()}"
                    existing != null ->
                        "Adding to ${existing.title} (${existing.n} already there)"
                    else -> "Year, country and name decide where the photographs go."
                }
            )

            Button(
                onClick = {
                    scope.launch {
                        running = true; failed = null; note = null; done = 0
                        total = picked.size
                        val wasNew = isNew
                        try {
                            val batch = api.newBatch()
                            picked.forEachIndexed { i, uri ->
                                val data = withContext(Dispatchers.IO) {
                                    context.contentResolver.openInputStream(uri)
                                        ?.use { it.readBytes() }
                                } ?: return@forEachIndexed
                                // ⚠ There HAS to be a name: the server hangs the
                                //   extension off it and tells from that what
                                //   kind of file it is.
                                val ext = context.contentResolver.getType(uri)
                                    ?.substringAfterLast('/')?.substringBefore(';')
                                    ?.let { if (it == "jpeg") "jpg" else it } ?: "jpg"
                                val fid = api.addFile(batch, "IMG_${i + 1}.$ext", data.size)
                                var offset = 0
                                while (offset < data.size) {
                                    val end = minOf(offset + Api.CHUNK, data.size)
                                    api.sendChunk(batch, fid, offset,
                                                  data.copyOfRange(offset, end))
                                    offset = end
                                }
                                api.finishFile(batch, fid)
                                done += 1
                            }
                            api.commit(batch, year.trim(), country.trim(),
                                       event.trim(), place.trim())
                            note = "$done photograph${if (done == 1) "" else "s"} sent" +
                                (if (wasNew) " into the new album “${event.trim()}”." else ".") +
                                " The site is converting them now."
                            picked = emptyList()
                            // The new album has to turn up in the picker straight
                            // away -- otherwise the next upload would look like it
                            // has to be created a second time.
                            runCatching { albums = api.albums() }
                        } catch (e: Exception) {
                            failed = (e as? ApiError)?.message ?: e.message
                        }
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
                        Text("$done of $total…")
                    }
                } else {
                    Text(if (isNew) "Create album and upload" else "Upload")
                }
            }

            note?.let { Text(it, color = Ink.inkSoft, fontSize = 13.sp) }
            failed?.let { Text(it, color = MaterialTheme.colorScheme.error, fontSize = 13.sp) }
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

/** One tap fills all the fields, the same as choosing an album in the web form. */
@Composable
private fun Picker(shown: String, options: List<Pair<String, Album>>,
                   onPick: (Album) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(onClick = { open = true }, modifier = Modifier.fillMaxWidth()) {
            Text(shown, modifier = Modifier.weight(1f), maxLines = 1)
            Icon(Icons.Filled.ArrowDropDown, contentDescription = null)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            options.forEach { (label, album) ->
                DropdownMenuItem(
                    text = { Text(label) },
                    onClick = { open = false; onPick(album) },
                )
            }
        }
    }
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
