//  Wat de Server zréckschéckt.
//
//  ⚠ Read field by field out of org.json, and not by a reflecting parser. The
//    names below are the server's names (`web_name`, `cover_rev`, `file_id`):
//    change one here and the field comes back null -- which looks exactly like
//    "there are no photographs" and is very hard to see.

package lu.racoon.roamlight

import org.json.JSONArray
import org.json.JSONObject

private fun JSONObject.str(k: String): String? =
    if (isNull(k)) null else optString(k, "").ifEmpty { null }

private fun JSONObject.int(k: String): Int? = if (isNull(k)) null else optInt(k)

private fun JSONArray.strings(): List<String> =
    (0 until length()).mapNotNull { optString(it, "").ifEmpty { null } }

data class Pairing(val token: String, val user: String) {
    companion object {
        fun of(o: JSONObject) = Pairing(o.getString("token"), o.optString("user", ""))
    }
}

data class Rights(val view: Boolean, val upload: Boolean,
                  val share: Boolean, val admin: Boolean)

data class Me(
    val user: String,
    val email: String,
    val groups: List<String>,
    val may: Rights,
    val site: String,
) {
    companion object {
        fun of(o: JSONObject): Me {
            val m = o.optJSONObject("may") ?: JSONObject()
            return Me(
                user = o.optString("user", ""),
                email = o.optString("email", ""),
                groups = o.optJSONArray("groups")?.strings() ?: emptyList(),
                may = Rights(m.optBoolean("view"), m.optBoolean("upload"),
                             m.optBoolean("share"), m.optBoolean("admin")),
                site = o.optString("site", ""),
            )
        }
    }
}

data class Album(
    val year: String,
    val country: String,
    val event: String,
    val title: String,
    val n: Int,
    val cover: Int?,
    val coverRev: Int?,
) {
    /** The same key as `album_key` on the server: `<year>/<country>/<name>`. */
    val id: String get() = "$year/$country/$event"

    companion object {
        fun of(o: JSONObject) = Album(
            year = o.optString("year", ""),
            country = o.optString("country", ""),
            event = o.optString("event", ""),
            title = o.optString("title", ""),
            n = o.optInt("n", 0),
            cover = o.int("cover"),
            coverRev = o.int("cover_rev"),
        )

        fun list(o: JSONObject): List<Album> {
            val a = o.optJSONArray("albums") ?: return emptyList()
            return (0 until a.length()).map { of(a.getJSONObject(it)) }
        }
    }
}

data class Photo(
    val id: Int,
    val takenAt: String?,
    val width: Int?,
    val height: Int?,
    val country: String?,
    val place: String?,
    val camera: String?,
    val title: String?,
    val kind: String?,
    val durationS: Int?,
    val rev: Int?,
) {
    val isVideo: Boolean get() = kind == "video"

    /** How wide against how tall. Without measurements: a square — then the
     *  clamp on a zoomed photograph is a little generous, never wrong. */
    val ratio: Float
        get() {
            val w = width ?: 0
            val h = height ?: 0
            return if (w > 0 && h > 0) w.toFloat() / h.toFloat() else 1f
        }

    companion object {
        fun of(o: JSONObject) = Photo(
            id = o.getInt("id"),
            takenAt = o.str("taken_at"),
            width = o.int("width"),
            height = o.int("height"),
            country = o.str("country"),
            place = o.str("place"),
            camera = o.str("camera"),
            title = o.str("title"),
            kind = o.str("kind"),
            durationS = o.int("duration_s"),
            rev = o.int("rev"),
        )
    }
}

data class PhotoPage(val total: Int, val page: Int, val pages: Int, val photos: List<Photo>) {
    companion object {
        fun of(o: JSONObject): PhotoPage {
            val a = o.optJSONArray("photos") ?: JSONArray()
            return PhotoPage(
                total = o.optInt("total", 0),
                page = o.optInt("page", 1),
                pages = o.optInt("pages", 1),
                photos = (0 until a.length()).map { Photo.of(a.getJSONObject(it)) },
            )
        }
    }
}

/** Where a video may be fetched from, and for how long that address is good. */
data class VideoTicket(val url: String, val expiresIn: Int) {
    companion object {
        fun of(o: JSONObject) = VideoTicket(
            url = o.optString("url", ""),
            expiresIn = o.optInt("expires_in", 0),
        )
    }
}

data class ShareResult(val url: String, val password: String,
                       val expiresAt: String?, val n: Int?) {
    companion object {
        fun of(o: JSONObject) = ShareResult(
            url = o.optString("url", ""),
            password = o.optString("password", ""),
            expiresAt = o.str("expires_at"),
            n = o.int("n"),
        )
    }
}

/** What already exists, so nobody has to remember last year's spelling. */
data class Facets(val years: List<String>, val countries: List<String>,
                  val places: List<String>) {
    companion object {
        fun of(o: JSONObject) = Facets(
            years = o.optJSONArray("years")?.strings() ?: emptyList(),
            countries = o.optJSONArray("countries")?.strings() ?: emptyList(),
            places = o.optJSONArray("places")?.strings() ?: emptyList(),
        )
    }
}
