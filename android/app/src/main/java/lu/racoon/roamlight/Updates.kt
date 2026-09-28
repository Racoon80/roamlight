//  New versions of the app, from the family's own site (no Play Store).
//
//  ⚠ The site says which version it hands out (/api/app/android). If that is
//    newer than this one, the app offers it: download with the device token,
//    then hand the file to Android's own installer -- which shows the person
//    what is being installed and asks. Nothing is installed silently.
//  ⚠ Android installs an update only if it is signed with the SAME key as the
//    app already there. A file that was swapped on the way is refused by the
//    phone itself.

package lu.racoon.roamlight

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.foundation.layout.padding
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class Release(val code: Long, val name: String)

fun installedVersion(context: Context): Long = try {
    val info = context.packageManager.getPackageInfo(context.packageName, 0)
    @Suppress("DEPRECATION")
    if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else info.versionCode.toLong()
} catch (_: Exception) { 0 }

/** Asks once when the app opens; offers the update if there is one. */
@Composable
fun UpdateOffer() {
    val api = LocalApi.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var offer by remember { mutableStateOf<Release?>(null) }
    var busy by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        // Quietly: a site with no Android app, or no answer, is not an error.
        val r = runCatching { api.androidRelease() }.getOrNull() ?: return@LaunchedEffect
        if (r.code > installedVersion(context)) offer = r
    }

    val r = offer ?: return
    AlertDialog(
        onDismissRequest = { if (!busy) offer = null },
        title = { Text("Roamlight ${r.name} is here") },
        text = {
            if (busy) CircularProgressIndicator(Modifier.padding(8.dp), color = Ink.safelight)
            else Text(problem ?: "A newer version of the app is on your family's site. " +
                "Android will ask you to confirm the installation.")
        },
        confirmButton = {
            TextButton(enabled = !busy, onClick = {
                // ⚠ Android 8+: the person has to allow THIS app to install
                //   apps, once. Send them to that switch, then they tap again.
                if (Build.VERSION.SDK_INT >= 26 && !context.packageManager.canRequestPackageInstalls()) {
                    context.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        Uri.parse("package:${context.packageName}")))
                    problem = "Allow Roamlight to install apps, come back, and tap Update again."
                    return@TextButton
                }
                scope.launch {
                    busy = true
                    try {
                        val file = withContext(Dispatchers.IO) {
                            File(context.cacheDir, "updates").apply { mkdirs() }
                                .resolve("roamlight.apk").also { it.writeBytes(api.androidApk()) }
                        }
                        val uri = FileProvider.getUriForFile(context,
                            "${context.packageName}.updates", file)
                        context.startActivity(Intent(Intent.ACTION_VIEW)
                            .setDataAndType(uri, "application/vnd.android.package-archive")
                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or
                                      Intent.FLAG_ACTIVITY_NEW_TASK))
                        offer = null
                    } catch (e: Exception) {
                        problem = (e as? ApiError)?.message ?: e.message
                    }
                    busy = false
                }
            }) { Text("Update") }
        },
        dismissButton = {
            TextButton(enabled = !busy, onClick = { offer = null }) { Text("Later") }
        },
    )
}
