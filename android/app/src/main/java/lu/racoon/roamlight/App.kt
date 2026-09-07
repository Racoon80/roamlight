//  D'App. Si kann véier Saachen: sech umellen, Fotoe kucken, deelen an
//  eroplueden. Alles anescht ass dem Server seng Aarbecht -- d'App rechent
//  näischt aus, si weist a gëtt weider.

package lu.racoon.roamlight

import android.app.Application
import androidx.compose.runtime.*
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.launch

class RoamlightApp : Application()

/** The one API instance the whole tree uses, so nothing has to pass it down. */
val LocalApi = staticCompositionLocalOf<Api> { error("no Api in the tree") }

class AppState(app: Application) : AndroidViewModel(app) {
    private val store = Store(app)
    val api = Api(store)

    var token by mutableStateOf(store.token)
        private set
    var me by mutableStateOf<Me?>(null)
        private set
    var error by mutableStateOf<String?>(null)
    var checking by mutableStateOf(false)
        private set

    val connected: Boolean get() = token != null

    /**
     * After pairing, and at every start: who am I, and what may I.
     *
     * ⚠ A 401 means the device was revoked on the site. Then do NOT keep
     *   retrying: throw the token away and ask again. Otherwise the app hangs
     *   in a loop of errors nobody can lift.
     */
    fun refresh() {
        if (token == null) return
        viewModelScope.launch {
            checking = true
            try {
                me = api.me()
                error = null
            } catch (e: ApiError) {
                if (e.code == 401) signOut() else error = e.message
            } finally {
                checking = false
            }
        }
    }

    fun connect(code: String, name: String, site: String, done: (Boolean) -> Unit = {}) {
        viewModelScope.launch {
            checking = true
            try {
                val p = api.pair(code.trim(), name, site.trim())
                store.token = p.token
                token = p.token
                error = null
                checking = false
                refresh()
                done(true)
            } catch (e: Exception) {
                error = (e as? ApiError)?.message ?: e.message
                checking = false
                done(false)
            }
        }
    }

    fun signIn(site: String, user: String, password: String, name: String,
               done: (Boolean) -> Unit = {}) {
        viewModelScope.launch {
            checking = true
            try {
                val p = api.signIn(site.trim(), user.trim(), password, name)
                store.token = p.token
                token = p.token
                error = null
                checking = false
                refresh()
                done(true)
            } catch (e: Exception) {
                error = (e as? ApiError)?.message ?: e.message
                checking = false
                done(false)
            }
        }
    }

    fun signOut() {
        store.token = null
        token = null
        me = null
        ImageStore.clear()
    }
}
