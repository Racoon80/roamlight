//  D'App. Si kann véier Saachen: sech umellen, Fotoe kucken, deelen an
//  eroplueden. Alles anescht ass dem Server seng Aarbecht -- d'App rechent
//  näischt aus, si weist a gëtt weider.

package lu.racoon.roamlight

import android.app.Application
import androidx.compose.runtime.*
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.launch

class RoamlightApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Notices.setUp(this)
    }
}

/** A typed address, made into one: `family.example.org` -> `https://family.example.org`. */
fun siteUrl(typed: String): String {
    val s = typed.trim().trimEnd('/')
    return if (s.isEmpty() || s.contains("://")) s else "https://$s"
}

private fun b64url(b: ByteArray): String =
    android.util.Base64.encodeToString(b,
        android.util.Base64.URL_SAFE or android.util.Base64.NO_PADDING or android.util.Base64.NO_WRAP)

/** The one API instance the whole tree uses, so nothing has to pass it down. */
val LocalApi = staticCompositionLocalOf<Api> { error("no Api in the tree") }

/**
 * May take photographs and albums off the site. ⚠ Only whether to SHOW the
 * button: the server still refuses what is not this person's own.
 */
val LocalMayRemove = compositionLocalOf { false }

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
    /** The album a tapped notice asked for (its `<year>/<country>/<event>`). */
    var openAlbum by mutableStateOf<String?>(null)

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

    /**
     * Single sign-on, first half: open the site's own sign-in in the browser.
     *
     * ⚠ The app is NOT a second client at the provider. The browser goes to
     *   `/auth/oidc/login` like the website does, the provider sends it back to
     *   the site like it always does, and the site ends on `/app/sso`: "connect
     *   this phone?". Its answer comes back as roamlight://sso?c=<code>, see
     *   [finishSso]. Nothing had to change at the provider.
     *
     * ⚠ The code is bound to [verifier]'s hash (PKCE, S256). The secret never
     *   leaves this app until it trades the code -- whoever catches the address
     *   holds nothing.
     */
    fun startSso(context: android.content.Context, typed: String) {
        val site = siteUrl(typed)
        viewModelScope.launch {
            checking = true
            try {
                if (!api.ways(site).sso) {
                    error = "This site has no single sign-on. Use the code or a password."
                    return@launch
                }
                val raw = ByteArray(32).also { java.security.SecureRandom().nextBytes(it) }
                val verifier = b64url(raw)
                val challenge = b64url(java.security.MessageDigest.getInstance("SHA-256")
                    .digest(verifier.toByteArray()))
                store.ssoSite = site
                store.ssoVerifier = verifier
                val next = java.net.URLEncoder.encode("/app/sso?challenge=$challenge", "UTF-8")
                val url = android.net.Uri.parse("$site/auth/oidc/login?next=$next")
                context.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, url)
                    .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
                error = null
            } catch (e: Exception) {
                error = (e as? ApiError)?.message ?: e.message
            } finally {
                checking = false
            }
        }
    }

    /** Single sign-on, second half: the browser came back with a code. */
    fun finishSso(uri: android.net.Uri, name: String) {
        val code = uri.getQueryParameter("c").orEmpty()
        val site = store.ssoSite
        val verifier = store.ssoVerifier
        // ⚠ Used once, whatever happens next: a second address arriving later
        //   must not find a secret still waiting for it.
        store.ssoSite = null
        store.ssoVerifier = null
        if (code.isEmpty() || site == null || verifier == null) {
            error = "That sign-in did not start in this app. Try again."
            return
        }
        viewModelScope.launch {
            checking = true
            try {
                val p = api.pair(code, name, site, verifier)
                store.token = p.token
                token = p.token
                error = null
                checking = false
                refresh()
            } catch (e: Exception) {
                error = (e as? ApiError)?.message ?: e.message
                checking = false
            }
        }
    }

    fun signOut() {
        // ⚠ Tell the site to stop sending BEFORE the token goes -- afterwards
        //   there is nothing left to say it with. At most three seconds: a site
        //   that cannot be reached must not keep anybody from signing out.
        val app = getApplication<Application>()
        viewModelScope.launch {
            kotlinx.coroutines.withTimeoutOrNull(3000) { Notices.forget(app) }
            store.token = null
            token = null
            me = null
            ImageStore.clear()
        }
    }
}
