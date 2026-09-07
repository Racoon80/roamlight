//  D'Verbindung zum Site. Alles, wat iwwer d'Netz geet, steet hei.
//
//  ⚠ One header does the signing in: `Authorization: Bearer fam_…`. The server
//    (app/devices.py) checks it and then sets the same identity as for a
//    browser -- so exactly the same rights apply in the app as on the site.
//    No cookie, no session.

package lu.racoon.roamlight

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

class ApiError(val code: Int, val detail: String = "") : Exception() {
    override val message: String
        get() = when {
            code == 0 && detail.isNotEmpty() -> detail
            code == 0 -> "The site could not be reached."
            code == 401 -> "This device was taken off the list. Connect it again."
            detail.isNotEmpty() -> detail
            else -> "The site said $code."
        }
}

class Api(private val store: Store) {

    val site: String get() = store.site

    // MARK: - Ufroen

    private fun url(path: String): URL {
        val base = store.site
        if (base.isEmpty()) throw ApiError(0, "This device is not connected yet.")
        return URL(URL(base), path)
    }

    /**
     * One request, start to finish. Runs on the IO dispatcher, because
     * HttpURLConnection blocks -- on the main thread Android would throw
     * NetworkOnMainThreadException.
     */
    private suspend fun run(
        path: String,
        method: String = "GET",
        body: ByteArray? = null,
        contentType: String? = "application/json",
        readTimeoutMs: Int = 30_000,
    ): ByteArray = withContext(Dispatchers.IO) {
        val c = url(path).openConnection() as HttpURLConnection
        try {
            c.requestMethod = method
            c.connectTimeout = 15_000
            c.readTimeout = readTimeoutMs
            c.instanceFollowRedirects = false
            store.token?.let { c.setRequestProperty("Authorization", "Bearer $it") }
            if (body != null) {
                c.doOutput = true
                contentType?.let { c.setRequestProperty("Content-Type", it) }
                c.setFixedLengthStreamingMode(body.size)
                c.outputStream.use { it.write(body) }
            }
            val code = c.responseCode
            // ⚠ 3xx is an error here and not a redirect to follow. Without a
            //   valid token the site answers a page request with a 302 to the
            //   sign-in page -- following it would hand back an HTML login form
            //   with status 200, and the app would show "unexpected answer"
            //   instead of "connect this device again".
            if (code in 300..399) throw ApiError(401)
            val stream = if (code in 200..299) c.inputStream else c.errorStream
            val data = stream?.use { it.readBytes() } ?: ByteArray(0)
            if (code !in 200..299) {
                // The server sends {"detail": "..."} -- that sentence is what
                // gets shown. If there is none, the status code stays.
                val detail = try {
                    JSONObject(String(data)).optString("detail", "")
                } catch (_: Exception) { "" }
                throw ApiError(code, detail)
            }
            data
        } catch (e: ApiError) {
            throw e
        } catch (e: Exception) {
            throw ApiError(0, e.message ?: "")
        } finally {
            c.disconnect()
        }
    }

    private suspend fun getJson(path: String): JSONObject =
        parse(run(path))

    private suspend fun postJson(path: String, body: JSONObject): JSONObject =
        parse(run(path, "POST", body.toString().toByteArray()))

    private fun parse(data: ByteArray): JSONObject =
        try { JSONObject(String(data)) }
        catch (_: Exception) { throw ApiError(0, "The site sent something unexpected.") }

    /**
     * ⚠ Every part of the path is escaped on its own: an album called
     *   "Ostend / Belgium" would otherwise become two path segments.
     *   URLEncoder is made for form bodies, so `+` has to become `%20` again.
     */
    private fun esc(s: String): String =
        URLEncoder.encode(s, "UTF-8").replace("+", "%20")

    // MARK: - Umellen

    /** Trade the pairing code for a token. The only request that goes without one. */
    suspend fun pair(code: String, name: String, siteUrl: String): Pairing {
        store.site = siteUrl
        val had = store.token
        store.token = null                       // pairing must go unauthenticated
        return try {
            Pairing.of(postJson("/api/app/pair",
                JSONObject().put("code", code).put("name", name)))
        } catch (e: Exception) {
            store.token = had
            throw e
        }
    }

    suspend fun me(): Me = Me.of(getJson("/api/app/me"))

    // MARK: - Kucken

    suspend fun albums(): List<Album> = Album.list(getJson("/api/albums"))

    suspend fun photos(album: Album?, page: Int = 1, query: String? = null): PhotoPage {
        val q = StringBuilder("?page=$page")
        if (album != null) {
            q.append("&year=").append(esc(album.year))
            q.append("&country=").append(esc(album.country))
            q.append("&event=").append(esc(album.event))
        }
        if (!query.isNullOrEmpty()) q.append("&q=").append(esc(query))
        return PhotoPage.of(getJson("/api/photos$q"))
    }

    suspend fun facets(): Facets = Facets.of(getJson("/api/facets"))

    /**
     * One image. ⚠ WebP and not AVIF: Android can do both, but the server
     * computes AVIF at effort 2 -- WebP arrives sooner and looks the same.
     */
    suspend fun image(id: Int, width: Int): ByteArray =
        run("/photos/$id/$width.webp")

    // MARK: - Deelen

    suspend fun share(album: Album, days: Int = 14): ShareResult =
        ShareResult.of(postJson("/api/albums/share", JSONObject()
            .put("year", album.year).put("country", album.country)
            .put("event", album.event).put("days", days)
            .put("allow_download", true)))

    // MARK: - Eng Foto an en Album deen et scho gëtt

    /**
     * ⚠ The SAME route the web page uses
     *   (`POST /y/<year>/<country>/<event>/contribute`), open to everyone who
     *   may look at that album. It goes live with no approval; the checks (type
     *   from the content, virus scan) run on the server either way.
     *
     * ⚠ multipart/form-data with the field name `file`, because that is what
     *   the server reads. A JSON body would be quietly ignored.
     */
    suspend fun contribute(album: Album, name: String, data: ByteArray) {
        val boundary = "roamlight.${System.nanoTime().toString(16)}"
        val out = ByteArrayOutputStream()
        fun put(s: String) = out.write(s.toByteArray())
        put("--$boundary\r\n")
        put("Content-Disposition: form-data; name=\"file\"; filename=\"$name\"\r\n")
        put("Content-Type: application/octet-stream\r\n\r\n")
        out.write(data)
        put("\r\n--$boundary--\r\n")

        val path = "/y/${esc(album.year)}/${esc(album.country)}/${esc(album.event)}/contribute"
        // A photograph off a phone is several MB, and a mobile network is not
        // the wifi at home.
        run(path, "POST", out.toByteArray(),
            "multipart/form-data; boundary=$boundary", readTimeoutMs = 300_000)
    }

    // MARK: - Eroplueden
    //
    // ⚠ In chunks and not in one piece: a photograph off a phone is 5 MB
    //   before you know it. The server already has the path
    //   (upload/batch -> file -> chunk -> done -> commit).

    suspend fun newBatch(): String =
        postJson("/api/upload/batch", JSONObject()).getString("batch")

    suspend fun addFile(batch: String, name: String, size: Int): Int =
        postJson("/api/upload/$batch/file",
            JSONObject().put("name", name).put("size", size)).getInt("file_id")

    suspend fun sendChunk(batch: String, file: Int, offset: Int, data: ByteArray) {
        run("/api/upload/$batch/file/$file/chunk?offset=$offset", "PUT", data,
            "application/octet-stream", readTimeoutMs = 120_000)
    }

    suspend fun finishFile(batch: String, file: Int) {
        run("/api/upload/$batch/file/$file/done", "POST", "{}".toByteArray())
    }

    suspend fun commit(batch: String, year: String, country: String,
                       event: String, place: String) {
        run("/api/upload/$batch/commit", "POST", JSONObject()
            .put("year", year).put("country", country)
            .put("event", event).put("place", place).toString().toByteArray())
    }

    companion object {
        const val CHUNK = 512 * 1024
    }
}
