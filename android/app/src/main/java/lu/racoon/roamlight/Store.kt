//  Wou den Token an d'Adress vum Site leien.
//
//  ⚠ Ordinary SharedPreferences, in the app's own private storage -- which on
//    Android no other app can read. What matters more is that the file must not
//    LEAVE the device: `allowBackup="false"` plus the extraction rules in the
//    manifest. That is the counterpart of `ThisDeviceOnly` on iOS: on a new
//    phone you pair again, and the site's device list stays honest about which
//    machine actually holds a token.

package lu.racoon.roamlight

import android.content.Context
import android.content.SharedPreferences

class Store(context: Context) {
    private val p: SharedPreferences =
        context.getSharedPreferences("roamlight", Context.MODE_PRIVATE)

    var token: String?
        get() = p.getString("token", null)
        set(v) = p.edit().apply { if (v == null) remove("token") else putString("token", v) }.apply()

    /**
     * ⚠ There is no built-in address, and that is deliberate: the site is
     *   somebody's own machine. A default would send this app's requests --
     *   with its token -- to a stranger. It is learned from the QR code.
     */
    var site: String
        get() = p.getString("site", "") ?: ""
        set(v) = p.edit().putString("site", v.trimEnd('/')).apply()

    /**
     * A single sign-on in flight: the site it went to and the secret the code
     * will be bound to. ⚠ Kept on disk and not in memory: while the browser is
     * in front, Android may throw this app's process away, and the answer then
     * arrives at a fresh one.
     */
    /** The address Google gave this phone for notices -- kept to take it back. */
    var fcmToken: String?
        get() = p.getString("fcm_token", null)
        set(v) = p.edit().apply { if (v == null) remove("fcm_token") else putString("fcm_token", v) }.apply()

    var ssoSite: String?
        get() = p.getString("sso_site", null)
        set(v) = p.edit().apply { if (v == null) remove("sso_site") else putString("sso_site", v) }.apply()
    var ssoVerifier: String?
        get() = p.getString("sso_verifier", null)
        set(v) = p.edit().apply { if (v == null) remove("sso_verifier") else putString("sso_verifier", v) }.apply()
}
