//  E Bild vum Site.
//
//  ⚠ No image library. Every request needs the `Authorization` header, and the
//    photographs come from a server that is only reachable at home -- a loader
//    of our own is smaller than teaching a library about both. With a cache,
//    because a gallery asks for the same photograph again and again while
//    scrolling.

package lu.racoon.roamlight

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp

/**
 * ⚠ An LruCache measured in BYTES, not in entries. A gallery of 500
 *   photographs would otherwise be held whole and the app would be killed;
 *   an eighth of the heap is what Android itself recommends for images.
 */
object ImageStore {
    private val cache = object : LruCache<String, Bitmap>(
        (Runtime.getRuntime().maxMemory() / 8).toInt()
    ) {
        override fun sizeOf(key: String, value: Bitmap) = value.byteCount
    }

    fun cached(key: String): Bitmap? = cache.get(key)

    suspend fun load(api: Api, id: Int, width: Int, rev: Int): Bitmap? {
        val key = "$id-$width-$rev"
        cache.get(key)?.let { return it }
        return try {
            val data = api.image(id, width, rev)
            val bmp = BitmapFactory.decodeByteArray(data, 0, data.size) ?: return null
            cache.put(key, bmp)
            bmp
        } catch (_: Exception) {
            null
        }
    }

    fun clear() = cache.evictAll()
}

@Composable
fun RemoteImage(
    id: Int,
    width: Int,
    modifier: Modifier = Modifier,
    /** The server bumps `photos.rev` when a photograph is turned -- see Api.image. */
    rev: Int = 0,
    contentScale: ContentScale = ContentScale.Crop,
) {
    val api = LocalApi.current
    val key = "$id-$width-$rev"
    var bitmap by remember(key) { mutableStateOf(ImageStore.cached(key)) }

    LaunchedEffect(key) {
        if (bitmap == null) bitmap = ImageStore.load(api, id, width, rev)
    }

    Box(modifier.background(Ink.groundWarm), contentAlignment = Alignment.Center) {
        val b = bitmap
        if (b != null) {
            Image(
                bitmap = b.asImageBitmap(),
                contentDescription = null,
                contentScale = contentScale,
                modifier = Modifier.fillMaxSize(),
            )
        } else {
            CircularProgressIndicator(
                modifier = Modifier.size(18.dp),
                strokeWidth = 2.dp,
                color = Ink.inkMute,
            )
        }
    }
}
