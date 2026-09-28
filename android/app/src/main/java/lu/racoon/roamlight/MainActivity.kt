//  D'Fënster, an d'Navigatioun tëscht de véier Reider.

package lu.racoon.roamlight

import android.content.Intent
import android.os.Bundle
import androidx.lifecycle.ViewModelProvider
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CreateNewFolder
import androidx.compose.material.icons.filled.GridView
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        // A fresh process (Android threw the old one away while the browser
        // was in front) gets the sign-in's answer here, not in onNewIntent.
        if (savedInstanceState == null) { takeSso(intent); takeNotice(intent) }
        setContent {
            RoamlightTheme {
                val state: AppState = viewModel()
                CompositionLocalProvider(
                    LocalApi provides state.api,
                    LocalMayRemove provides (state.me?.may?.upload == true),
                    LocalMayAdmin provides (state.me?.may?.admin == true),
                ) {
                    Surface(color = Ink.ground) { Root(state) }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        takeSso(intent)
        takeNotice(intent)
    }

    /** A tapped notice: its album comes along as an extra (see Notices.kt). */
    private fun takeNotice(intent: Intent?) {
        val key = intent?.getStringExtra(Notices.EXTRA_ALBUM)?.takeIf { it.isNotEmpty() } ?: return
        ViewModelProvider(this)[AppState::class.java].openAlbum = key
        intent.removeExtra(Notices.EXTRA_ALBUM)
    }

    /** roamlight://sso?c=<code> -- the way back from single sign-on. */
    private fun takeSso(intent: Intent?) {
        val uri = intent?.data ?: return
        if (uri.scheme != "roamlight" || uri.host != "sso") return
        ViewModelProvider(this)[AppState::class.java].finishSso(uri, deviceName())
        // ⚠ Consumed. A rotation re-delivers nothing, but an intent left on
        //   the activity would be read again by anybody asking later.
        setIntent(Intent())
    }
}

@Composable
private fun Root(state: AppState) {
    LaunchedEffect(state.token) { state.refresh() }
    if (state.connected) MainScreen(state) else PairScreen(state)
}

private data class Tab(val route: String, val label: String, val icon: ImageVector)

@Composable
private fun MainScreen(state: AppState) {
    val nav = rememberNavController()
    val context = androidx.compose.ui.platform.LocalContext.current

    // ⚠ Only once there IS a connection -- asking before that means asking
    //   somebody who has not seen a single photograph yet. Android 13+ asks the
    //   person; below that the permission is simply there.
    val ask = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission()
    ) { granted -> if (granted) Notices.register(context) }
    LaunchedEffect(Unit) {
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            androidx.core.content.ContextCompat.checkSelfPermission(
                context, android.Manifest.permission.POST_NOTIFICATIONS) !=
            android.content.pm.PackageManager.PERMISSION_GRANTED) {
            ask.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        } else {
            Notices.register(context)
        }
    }

    // A tapped notice lands here. The album is named by its key
    // (`<year>/<country>/<event>`), which is all the album page needs.
    LaunchedEffect(state.openAlbum) {
        val key = state.openAlbum ?: return@LaunchedEffect
        state.openAlbum = null
        val parts = key.split("/", limit = 3)
        if (parts.size == 3) {
            nav.navigate("photos/" + AlbumKey.of(Album(parts[0], parts[1], parts[2], "", 0, null, null)))
        }
    }
    // ⚠ The upload tab only appears when the server says this person may
    //   upload. It is not a hiding place -- the server refuses either way --
    //   but a button that always answers 403 is worse than no button.
    val tabs = buildList {
        add(Tab("albums", "Albums", Icons.Filled.GridView))
        add(Tab("search", "Search", Icons.Filled.Search))
        if (state.me?.may?.upload == true) add(Tab("upload", "New album", Icons.Filled.CreateNewFolder))
        add(Tab("device", "Device", Icons.Filled.PhoneAndroid))
    }

    UpdateOffer()

    Scaffold(
        containerColor = Ink.ground,
        bottomBar = { BottomBar(nav, tabs) },
    ) { pad ->
        NavHost(
            navController = nav,
            startDestination = "albums",
            modifier = Modifier.padding(pad).fillMaxSize().background(Ink.ground),
        ) {
            composable("albums") { AlbumsScreen(nav) }
            composable("search") { SearchScreen(nav) }
            composable("upload") { UploadScreen(nav) }
            composable("device") { DeviceScreen(state) }
            composable("photos/{album}") { entry ->
                PhotosScreen(nav, entry.arguments?.getString("album").orEmpty())
            }
            composable("photo/{id}") { entry ->
                PhotoScreen(nav, entry.arguments?.getString("id")?.toIntOrNull() ?: 0)
            }
        }
    }
}

@Composable
private fun BottomBar(nav: NavHostController, tabs: List<Tab>) {
    val entry by nav.currentBackStackEntryAsState()
    val here = entry?.destination?.route
    NavigationBar(containerColor = Ink.groundWarm) {
        tabs.forEach { t ->
            NavigationBarItem(
                selected = here == t.route,
                onClick = {
                    if (here != t.route) nav.navigate(t.route) {
                        // One entry per tab in the back stack, not one per tap.
                        popUpTo(nav.graph.startDestinationId) { saveState = true }
                        launchSingleTop = true
                        restoreState = true
                    }
                },
                icon = { Icon(t.icon, contentDescription = t.label) },
                label = { Text(t.label) },
                colors = NavigationBarItemDefaults.colors(
                    selectedIconColor = Ink.ground,
                    selectedTextColor = Ink.ink,
                    indicatorColor = Ink.safelight,
                    unselectedIconColor = Ink.inkMute,
                    unselectedTextColor = Ink.inkMute,
                ),
            )
        }
    }
}
