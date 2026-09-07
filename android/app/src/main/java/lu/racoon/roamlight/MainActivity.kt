//  D'Fënster, an d'Navigatioun tëscht de véier Reider.

package lu.racoon.roamlight

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowUpward
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
        setContent {
            RoamlightTheme {
                val state: AppState = viewModel()
                CompositionLocalProvider(LocalApi provides state.api) {
                    Surface(color = Ink.ground) { Root(state) }
                }
            }
        }
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
    // ⚠ The upload tab only appears when the server says this person may
    //   upload. It is not a hiding place -- the server refuses either way --
    //   but a button that always answers 403 is worse than no button.
    val tabs = buildList {
        add(Tab("albums", "Albums", Icons.Filled.GridView))
        add(Tab("search", "Search", Icons.Filled.Search))
        if (state.me?.may?.upload == true) add(Tab("upload", "Upload", Icons.Filled.ArrowUpward))
        add(Tab("device", "Device", Icons.Filled.PhoneAndroid))
    }

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
            composable("upload") { UploadScreen() }
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
