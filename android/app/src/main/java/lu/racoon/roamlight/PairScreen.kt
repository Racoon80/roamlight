//  Sech umellen: de QR-Code vun der Säit `/app` scannen.
//
//  ⚠ The QR code holds no token but a pairing code (five minutes, one use), in
//    the form `https://<site>/app/pair#c=<code>`. The app takes BOTH the site's
//    address and the code out of it: nobody types a URL.

package lu.racoon.roamlight

import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import java.net.URI

@Composable
fun PairScreen(state: AppState) {
    var site by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var typing by remember { mutableStateOf(false) }

    val scanner = rememberLauncherForActivityResult(ScanContract()) { result ->
        val text = result.contents ?: return@rememberLauncherForActivityResult
        val (c, s) = parsePairing(text)
        if (c.isNotEmpty()) {
            state.connect(c, deviceName(), s.ifEmpty { site })
        } else {
            state.error = "That is not a Roamlight code."
        }
    }

    Column(
        Modifier.fillMaxSize().background(Ink.ground)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 32.dp, vertical = 48.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(22.dp),
    ) {
        Spacer(Modifier.height(24.dp))
        Text("Roamlight", color = Ink.ink, fontSize = 34.sp)
        Text(
            "Open the site on a computer, go to Phone & tablet, and press " +
                "\"Show the code\".",
            color = Ink.inkSoft, textAlign = TextAlign.Center,
        )

        Button(
            onClick = {
                state.error = null
                scanner.launch(ScanOptions().apply {
                    setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                    setPrompt("Point the camera at the code")
                    setBeepEnabled(false)
                    setOrientationLocked(false)
                })
            },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Scan the code", modifier = Modifier.padding(vertical = 6.dp))
        }

        TextButton(onClick = { typing = !typing }) {
            Text(if (typing) "Hide" else "Type it instead", color = Ink.inkSoft)
        }

        if (typing) {
            OutlinedTextField(
                value = site, onValueChange = { site = it },
                label = { Text("Site") },
                singleLine = true,
                placeholder = { Text("https://photos.example.com") },
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.None, autoCorrectEnabled = false),
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = code, onValueChange = { code = it },
                label = { Text("Code") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.None, autoCorrectEnabled = false),
                modifier = Modifier.fillMaxWidth(),
            )
            Button(
                onClick = { state.connect(code, deviceName(), site) },
                enabled = code.isNotBlank() && site.isNotBlank() && !state.checking,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Connect") }
        }

        if (state.checking) CircularProgressIndicator(color = Ink.safelight)
        state.error?.let {
            Text(it, color = MaterialTheme.colorScheme.error,
                 textAlign = TextAlign.Center, fontSize = 13.sp)
        }

        Spacer(Modifier.height(24.dp))
        Text("This device will see exactly what you see on the site.",
             color = Ink.inkMute, fontSize = 12.sp, textAlign = TextAlign.Center)
    }
}

private fun deviceName(): String =
    listOf(Build.MANUFACTURER.replaceFirstChar { it.uppercase() }, Build.MODEL)
        .filter { it.isNotBlank() }.distinct().joinToString(" ")
        .ifEmpty { "Android" }

/**
 * Pull the code and the site out of what the camera read.
 *
 * ⚠ The code sits in the FRAGMENT (`#c=…`), and a fragment never goes to the
 *   server -- which is the point: the QR code can be photographed off a screen
 *   without the code ever appearing in anybody's access log. A bare code with
 *   no URL around it is also accepted, for the "type it instead" box.
 */
internal fun parsePairing(text: String): Pair<String, String> {
    val t = text.trim()
    return try {
        val u = URI(t)
        val scheme = u.scheme ?: return t to ""
        val code = u.fragment.orEmpty()
            .split("&").firstOrNull { it.startsWith("c=") }?.removePrefix("c=").orEmpty()
        val port = if (u.port > 0) ":${u.port}" else ""
        val site = "$scheme://${u.host}$port"
        if (code.isEmpty() || u.host == null) t to "" else code to site
    } catch (_: Exception) {
        t to ""
    }
}
