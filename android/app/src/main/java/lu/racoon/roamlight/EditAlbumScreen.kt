//  Edit album: name, place, the journey, who sees it -- and deleting it.
//
//  ⚠ The same routes the website's workshop uses, and the same rule: an admin
//    any album, anybody else only an album with nothing but their own
//    photographs in it. The server says so when the screen opens
//    (`/api/albums/settings` answers 403 otherwise) and again on every save.

package lu.racoon.roamlight

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.launch

/**
 * @param onDone `true` = the album is gone or has a new name, so the screen
 *   behind has to go back to the list.
 */
@Composable
fun EditAlbumDialog(album: Album, onDismiss: () -> Unit, onDone: (left: Boolean) -> Unit) {
    val api = LocalApi.current
    val scope = rememberCoroutineScope()
    var settings by remember { mutableStateOf<AlbumSettings?>(null) }
    var failed by remember { mutableStateOf<String?>(null) }
    var saving by remember { mutableStateOf(false) }
    var askDelete by remember { mutableStateOf(false) }

    var year by remember { mutableStateOf("") }
    var country by remember { mutableStateOf("") }
    var event by remember { mutableStateOf("") }
    var place by remember { mutableStateOf("") }
    var departure by remember { mutableStateOf("") }
    var transport by remember { mutableStateOf("car") }
    var audience by remember { mutableStateOf(setOf<String>()) }

    LaunchedEffect(album.id) {
        try {
            val s = api.albumSettings(album)
            year = s.year; country = s.country; event = s.event; place = s.place
            departure = s.departure; transport = s.transport
            audience = s.audience.toSet()
            settings = s
        } catch (e: ApiError) {
            failed = if (e.code == 403)
                "Only an administrator, or whoever uploaded every photograph in it, " +
                    "can edit this album."
            else e.message
        } catch (e: Exception) {
            failed = e.message
        }
    }

    val valid = year.length == 4 && year.all { it.isDigit() } &&
        country.isNotBlank() && event.isNotBlank()

    // ⚠ In this order: who sees it and the journey FIRST, the name LAST. Both
    //   are keyed by the album's name; renamed first, they would be written for
    //   a key that no longer exists.
    fun save() {
        val s = settings ?: return
        scope.launch {
            saving = true
            failed = null
            try {
                if (audience != s.audience.toSet()) api.setAudience(album, audience.sorted())
                val dep = departure.trim()
                if (dep != s.departure || transport != s.transport) {
                    api.setJourney(album, dep, transport, s)
                }
                val renamed = year.trim() != s.year || country.trim() != s.country ||
                    event.trim() != s.event
                if (renamed || place.trim() != s.place) {
                    api.editAlbum(album, year.trim(), country.trim(), event.trim(), place.trim())
                }
                onDismiss()
                onDone(renamed)
            } catch (e: Exception) {
                failed = (e as? ApiError)?.message ?: e.message
            }
            saving = false
        }
    }

    if (askDelete) {
        ConfirmRemoval(
            title = "Delete “${album.title.ifEmpty { album.event }}”?",
            action = "Delete ${album.n} photograph${if (album.n == 1) "" else "s"}",
            onDismiss = { askDelete = false },
        ) {
            askDelete = false
            scope.launch {
                saving = true
                try {
                    val r = api.removeAlbum(album)
                    if (r.failed.isEmpty()) { onDismiss(); onDone(true) }
                    else failed = "${r.failed.size} could not be deleted."
                } catch (e: Exception) {
                    failed = (e as? ApiError)?.message ?: e.message
                }
                saving = false
            }
        }
    }

    Dialog(onDismissRequest = onDismiss,
           properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxSize().background(Ink.ground)) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onDismiss) { Text("Cancel", color = Ink.inkSoft) }
                Text("Edit album", color = Ink.ink, fontSize = 18.sp,
                     modifier = Modifier.weight(1f))
                if (saving) {
                    CircularProgressIndicator(Modifier.padding(12.dp).size(20.dp),
                                              strokeWidth = 2.dp, color = Ink.safelight)
                } else {
                    TextButton(onClick = { save() }, enabled = settings != null && valid) {
                        Text("Save", color = Ink.safelight)
                    }
                }
            }
            val s = settings
            if (s == null) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    if (failed != null) Text(failed!!, color = MaterialTheme.colorScheme.error,
                                             modifier = Modifier.padding(24.dp))
                    else CircularProgressIndicator(color = Ink.safelight)
                }
                return@Column
            }
            Column(
                Modifier.fillMaxSize().verticalScroll(rememberScrollState())
                    .padding(horizontal = 20.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Heading("Album")
                Field("Name", event) { event = it }
                Field("Year", year, KeyboardType.Number) { year = it }
                Field("Country", country) { country = it }

                // ⚠ The place IS the destination of the journey when the
                //   photographs carry no GPS -- the site looks it up.
                Heading("Journey")
                Field("Departure (empty = from home)", departure) { departure = it }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    s.transports.forEach { m ->
                        FilterChip(selected = transport == m, onClick = { transport = m },
                                   label = { Text(m.replaceFirstChar { it.uppercase() }) })
                    }
                }
                Field("Destination (place)", place) { place = it }
                if (s.multi && s.legs.length() > 0) {
                    Text("This album has ${s.legs.length()} stops. They are kept; " +
                         "change them on the website.", color = Ink.inkMute, fontSize = 12.sp)
                }

                Heading("Who sees it")
                if (s.people.isEmpty() && s.groups.isEmpty()) {
                    Text("Nobody to choose from yet.", color = Ink.inkMute)
                }
                (s.groups + s.people).forEach { (principal, name) ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text((if (principal.startsWith("group:")) "Group · " else "") + name,
                             color = Ink.ink, modifier = Modifier.weight(1f))
                        Switch(checked = principal in audience, onCheckedChange = { on ->
                            audience = if (on) audience + principal else audience - principal
                        })
                    }
                }
                Text(if (audience.isEmpty()) "Nobody ticked: only the administrators see this album."
                     else "Administrators always see every album.",
                     color = Ink.inkMute, fontSize = 12.sp)

                failed?.let { Text(it, color = MaterialTheme.colorScheme.error) }

                Spacer(Modifier.height(12.dp))
                OutlinedButton(onClick = { askDelete = true }, enabled = !saving,
                               modifier = Modifier.fillMaxWidth()) {
                    Text("Delete album", color = MaterialTheme.colorScheme.error)
                }
                Spacer(Modifier.height(32.dp))
            }
        }
    }
}

@Composable
private fun Heading(text: String) {
    Text(text.uppercase(), color = Ink.inkMute, fontSize = 11.sp,
         modifier = Modifier.padding(top = 12.dp))
}

@Composable
private fun Field(label: String, value: String, type: KeyboardType = KeyboardType.Text,
                  onChange: (String) -> Unit) {
    OutlinedTextField(value = value, onValueChange = onChange, label = { Text(label) },
                      singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = type),
                      modifier = Modifier.fillMaxWidth())
}
