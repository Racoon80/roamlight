//  Sichen. Deeselwechte Gitter wéi an engem Album, nëmmen eng aner Ufro.

package lu.racoon.roamlight

import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.navigation.NavHostController
import kotlinx.coroutines.delay

@Composable
fun SearchScreen(nav: NavHostController) {
    var typed by remember { mutableStateOf("") }
    var query by remember { mutableStateOf("") }

    // ⚠ Half a second after the last keystroke, not on every one. Otherwise
    //   "Ostende" is eight searches, and the server answers all eight.
    LaunchedEffect(typed) {
        delay(500)
        query = typed.trim()
    }

    PhotoGrid(
        nav = nav,
        album = null,
        query = query.ifEmpty { null },
        title = "Search",
        header = {
            OutlinedTextField(
                value = typed,
                onValueChange = { typed = it },
                singleLine = true,
                leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
                placeholder = { Text("Place, year, camera, name…") },
                modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
            )
        },
    )
}
