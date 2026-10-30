//  Your originals, in your own cloud.
//
//  ⚠ What it is for: a photograph uploaded by somebody who is not an
//    administrator keeps no original on the site -- it is converted and the
//    upload thrown away. With a cloud connected (Nextcloud; on the site's side
//    app/cloud.py) every original is copied there first, in the same folders
//    as on the site, and the site lets go only once the copy is read back and
//    matches.
//
//  ⚠ How connecting works: Nextcloud's Login Flow v2. The site asks the cloud
//    for a sign-in page, the phone's browser opens it, the person signs in and
//    approves, and meanwhile this block asks the site every two seconds whether
//    the cloud has handed over the password. The password goes from the cloud
//    straight to the site -- never through the phone.

package lu.racoon.roamlight

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
fun CloudBlock(api: Api) {
    var status by remember { mutableStateOf<CloudStatus?>(null) }
    var working by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var askOff by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    LaunchedEffect(Unit) {
        // ⚠ Ask about a sign-in in flight FIRST. If Android stopped the app
        //   while the person was approving in the browser, nobody was left
        //   asking -- and the site only learns the password by asking. This
        //   one call picks it up when they come back. `none` costs nothing.
        try { api.cloudPoll() } catch (_: Exception) {}
        status = try { api.cloudStatus() } catch (_: Exception) { null }
    }

    val s = status ?: return
    if (!s.available) return

    fun reload() = scope.launch {
        status = try { api.cloudStatus() } catch (_: Exception) { status }
    }

    Spacer(Modifier.height(8.dp))
    Text("YOUR ORIGINALS", color = Ink.inkMute, fontSize = 11.sp)
    if (s.connected) {
        Row2c("Cloud", s.host)
        Row2c("As", s.login)
        Row2c("Folder", s.folder)
        Row2c("Copied", s.copied.toString())
        if (s.waiting > 0) {
            Row2c("Waiting", s.waiting.toString())
            OutlinedButton(
                onClick = {
                    working = true
                    scope.launch {
                        try { status = api.cloudRetry(); message = "On their way." }
                        catch (e: Exception) { message = e.message }
                        working = false
                    }
                },
                enabled = !working, modifier = Modifier.fillMaxWidth(),
            ) { Text("Try again now") }
        }
        if (s.lastError.isNotEmpty()) {
            Text(s.lastError, color = MaterialTheme.colorScheme.error, fontSize = 12.sp)
        }
        OutlinedButton(onClick = { askOff = true }, enabled = !working,
                       modifier = Modifier.fillMaxWidth()) {
            Text("Disconnect", color = MaterialTheme.colorScheme.error)
        }
        Text("Every photograph you upload is copied in full to your cloud, in the " +
             "same folders as on the site.", color = Ink.inkMute, fontSize = 12.sp)
    } else {
        Button(
            onClick = {
                working = true
                message = "Asking your cloud…"
                scope.launch {
                    try {
                        val page = api.cloudStart()
                        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(page)))
                        message = "Sign in and approve in the browser — then come back here."
                        // Nextcloud keeps a Login Flow open for twenty minutes.
                        val until = System.currentTimeMillis() + 20 * 60 * 1000
                        while (System.currentTimeMillis() < until) {
                            delay(2000)
                            val st = try { api.cloudPoll() } catch (_: Exception) { "waiting" }
                            if (st == "connected") { message = "Connected."; break }
                            if (st == "expired" || st == "none") {
                                message = "That took too long — try again."; break
                            }
                        }
                        reload()
                    } catch (e: Exception) {
                        message = e.message
                    }
                    working = false
                }
            },
            enabled = !working, modifier = Modifier.fillMaxWidth(),
        ) { Text("Connect my cloud") }
        Text("Without this the site keeps only a 4000-pixel copy of what you upload " +
             "and throws the original away. Connect ${s.host} and every original " +
             "goes there first.", color = Ink.inkMute, fontSize = 12.sp)
    }
    message?.let { Text(it, color = Ink.inkSoft, fontSize = 12.sp) }

    if (askOff) {
        AlertDialog(
            onDismissRequest = { askOff = false },
            title = { Text("Stop copying your originals to your cloud?") },
            text = { Text("Your files in the cloud stay where they are. Anything not " +
                          "copied yet is discarded after conversion, as before.") },
            confirmButton = {
                TextButton(onClick = {
                    askOff = false; working = true
                    scope.launch {
                        try { status = api.cloudDisconnect(); message = null }
                        catch (e: Exception) { message = e.message }
                        working = false
                    }
                }) { Text("Disconnect", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { askOff = false }) { Text("Keep it") } },
            containerColor = Ink.groundWarm,
        )
    }
}



@Composable
private fun Row2c(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, color = Ink.inkSoft)
        Text(value, color = Ink.ink)
    }
}
