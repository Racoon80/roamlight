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
}
