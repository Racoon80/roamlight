//  Wien ech sinn, wat ech dierf, a wéi ech nees erauskommen.

package lu.racoon.roamlight

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.net.URI

@Composable
fun DeviceScreen(state: AppState) {
    var asking by remember { mutableStateOf(false) }
    val me = state.me

    Column(
        Modifier.fillMaxSize().background(Ink.ground).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Title("Device")
        Column(Modifier.padding(horizontal = 16.dp),
               verticalArrangement = Arrangement.spacedBy(12.dp)) {

            Text("SIGNED IN", color = Ink.inkMute, fontSize = 11.sp)
            when {
                me != null -> {
                    Row2("Name", me.user)
                    if (me.email.isNotEmpty()) Row2("Email", me.email)
                    Row2("May", listOfNotNull(
                        if (me.may.view) "look" else null,
                        if (me.may.upload) "upload" else null,
                        if (me.may.share) "share" else null,
                        if (me.may.admin) "admin" else null,
                    ).joinToString(" · "))
                }
                state.checking -> CircularProgressIndicator(color = Ink.safelight)
                else -> Text("Not reached", color = Ink.inkMute)
            }
            Row2("Site", hostOf(state.api.site))
            // ⚠ Which version this is. It was nowhere in the app at all, and
            //   that shows the moment somebody says "it does that here": the
            //   first question is which build they are holding, and nobody
            //   could answer it. The build number goes with it -- two people
            //   on "0.1.0" can be on different builds.
            Row2("Version", appVersion(LocalContext.current))

            state.error?.let {
                Text(it, color = MaterialTheme.colorScheme.error, fontSize = 13.sp)
            }

            OutlinedButton(onClick = { state.refresh() }, modifier = Modifier.fillMaxWidth()) {
                Text("Check again")
            }

            Spacer(Modifier.height(8.dp))
            Button(
                onClick = { asking = true },
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.error),
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Take this device off") }

            // ⚠ Say honestly what the button does: it deletes the token HERE.
            //   The device stays on the list on the site until it is revoked
            //   there -- and that is the place that counts once the phone is
            //   gone.
            Text(
                "This forgets the token on this phone. If the phone is lost, take " +
                    "it off the list on the site instead — that works even without " +
                    "the phone.",
                color = Ink.inkMute, fontSize = 12.sp,
            )
            Spacer(Modifier.height(24.dp))
        }
    }

    if (asking) {
        AlertDialog(
            onDismissRequest = { asking = false },
            title = { Text("Take this device off?") },
            confirmButton = {
                TextButton(onClick = { asking = false; state.signOut() }) {
                    Text("Take it off", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = { TextButton(onClick = { asking = false }) { Text("Keep it") } },
            containerColor = Ink.groundWarm,
        )
    }
}

@Composable
private fun Row2(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, color = Ink.inkSoft)
        Text(value, color = Ink.ink)
    }
}

/**
 * `0.1.0 (1)` -- out of the installed package, never typed here.
 *
 * ⚠ Read from the PackageManager and not from `BuildConfig`: generating that
 *   class is a build flag (`buildFeatures { buildConfig = true }`) which this
 *   project does not set, so it may simply not exist. The package always does.
 */
private fun appVersion(context: android.content.Context): String =
    try {
        val info = context.packageManager.getPackageInfo(context.packageName, 0)
        val name = info.versionName ?: "?"
        @Suppress("DEPRECATION")
        val code = if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.P)
            info.longVersionCode else info.versionCode.toLong()
        if (name == code.toString()) name else "$name ($code)"
    } catch (_: Exception) {
        "—"
    }


private fun hostOf(site: String): String =
    try { URI(site).host ?: "—" } catch (_: Exception) { "—" }
