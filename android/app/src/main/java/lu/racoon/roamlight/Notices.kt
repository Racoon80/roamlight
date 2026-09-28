//  The notices: a one-line message when an album grows or is shared.
//
//  ⚠ Firebase is set up HERE, by hand, from four public values -- not through
//    google-services.json and its Gradle plugin. They are the app's ids at
//    Firebase (project roamlight-ab28f), the same values that file would carry,
//    and the API key is the restricted Android key that ships in every Android
//    app that uses Firebase. Nothing secret: the right to SEND lives only on the
//    server (FAMILY_FCM_CREDENTIALS).
//
//  ⚠ The same trade as on the iPhone, said plainly: title and one-line body pass
//    through Google's servers, so they carry the album's title and nothing more.

package lu.racoon.roamlight

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await

object Notices {
    const val CHANNEL = "albums"
    /** The album a tapped notice is about, handed from the Activity to the tree. */
    const val EXTRA_ALBUM = "album"

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun setUp(context: Context) {
        if (FirebaseApp.getApps(context).isEmpty()) {
            FirebaseApp.initializeApp(context, FirebaseOptions.Builder()
                .setProjectId("roamlight-ab28f")
                .setApplicationId("1:861466416545:android:c2e653e6c01f3bbb2667e0")
                .setApiKey("AIzaSyB2eauqgSwvrHcC_YQvioQ74YKixLbBR7Y")
                .setGcmSenderId("861466416545")
                .build())
        }
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = context.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(
                CHANNEL, "Albums", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "When an album grows, or is shared with you"
            })
        }
    }

    /** Tell the site where this phone can be reached. Quietly: a site without
     *  push set up simply keeps the address until it has. */
    fun register(context: Context) {
        val store = Store(context)
        if (store.token == null) return
        scope.launch {
            runCatching {
                val t = FirebaseMessaging.getInstance().token.await()
                Api(store).registerNotices(t, deviceName())
                store.fcmToken = t
            }
        }
    }

    /** ⚠ Before the device token goes: afterwards there is nothing left to say
     *  it with, and the site would keep sending to a phone that signed out. */
    suspend fun forget(context: Context) {
        val store = Store(context)
        val t = store.fcmToken ?: return
        runCatching { Api(store).forgetNotices(t) }
        store.fcmToken = null
        runCatching { FirebaseMessaging.getInstance().deleteToken().await() }
    }
}

/** Where Firebase delivers. */
class RoamlightMessaging : FirebaseMessagingService() {

    /** Google handed out a new address -- the site has to hear it. */
    override fun onNewToken(token: String) = Notices.register(this)

    /**
     * ⚠ Only called while the app is OPEN. In the background Android shows the
     *   notice by itself (it carries a `notification` part) and a tap starts
     *   MainActivity with the `data` as extras -- `album` among them. In the
     *   foreground nothing would appear, so the notice is built here, the same.
     */
    override fun onMessageReceived(msg: RemoteMessage) {
        val n = msg.notification ?: return
        val open = Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            .putExtra(Notices.EXTRA_ALBUM, msg.data["album"].orEmpty())
        val pi = PendingIntent.getActivity(this, msg.data["album"].hashCode(), open,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val nm = getSystemService(NotificationManager::class.java)
        nm.notify(msg.data["album"].hashCode(), NotificationCompat.Builder(this, Notices.CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(n.title)
            .setContentText(n.body)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .build())
    }
}
