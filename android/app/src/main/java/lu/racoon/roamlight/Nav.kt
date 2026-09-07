//  Wat tëscht zwee Schiermer weidergereecht gëtt.

package lu.racoon.roamlight

import android.util.Base64
import org.json.JSONObject

/**
 * An album inside a navigation route.
 *
 * ⚠ Not `year/country/event` in the path: an album called "Ostend / Belgium"
 *   would become extra path segments and the route would never match. Base64
 *   of the three fields is unambiguous whatever anybody names an album -- and
 *   it survives the process being killed, which a shared variable would not.
 */
object AlbumKey {
    fun of(a: Album): String {
        val o = JSONObject().put("y", a.year).put("c", a.country)
            .put("e", a.event).put("t", a.title)
        return Base64.encodeToString(o.toString().toByteArray(),
            Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
    }

    fun parse(key: String): Album? = try {
        val o = JSONObject(String(Base64.decode(key, Base64.URL_SAFE)))
        Album(o.getString("y"), o.getString("c"), o.getString("e"),
              o.optString("t"), 0, null, null)
    } catch (_: Exception) {
        null
    }
}

/**
 * The list the large view pages through.
 *
 * ⚠ Deliberately not in the route: a whole page of photographs does not belong
 *   in a URL. It is set the moment somebody taps a picture, so it is always
 *   fresh. If Android kills the process while the large view is open, this is
 *   empty again on the way back -- and `PhotoScreen` then simply goes back to
 *   the grid instead of showing nothing.
 */
object Viewing {
    var photos: List<Photo> = emptyList()
    var start: Int = 0

    fun open(list: List<Photo>, index: Int) {
        photos = list
        start = index.coerceIn(0, maxOf(0, list.size - 1))
    }
}
