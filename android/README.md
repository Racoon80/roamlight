# Roamlight for Android

The same things the iOS app does: sign in, look at photographs, search, make a
new album, add to one you are already in, share, and play a video. Kotlin and
Jetpack Compose, no server code of its own — every request goes down the same
routes the website uses.

Walking into an album plays its journey first: the map, the places, and the way
you got there. The tiles come through the site (`/tiles/…`), never straight from
OpenStreetMap — a phone asking for tiles tells a stranger roughly where the
family's albums are.

## Build it

You need a JDK 17 or newer and the Android SDK (platform 35, build-tools 35).
Android Studio brings both.

```bash
cd android
./gradlew :app:assembleDebug          # app/build/outputs/apk/debug/
./gradlew :app:installDebug           # onto a plugged-in phone
```

⚠ **Gradle cannot build on a network share.** It needs file locks that SMB and
NFS do not have, and it fails with `IOException: Operation not supported`
before it compiles anything. Keep the checkout on a local disk.

## Signing in

Open the site on a computer, go to **Phone & tablet**, press *Show the code*,
and scan it. The QR code carries the site's address and a pairing code that is
good for five minutes and one device; the app trades it for a long-lived token.

⚠ There is **no built-in address**. The site is somebody's own machine, and a
default would send this app's requests — with its token — to a stranger.

The token is kept in the app's private storage with `allowBackup="false"`, so
it does not travel to a new phone in a cloud backup. On a new phone you pair
again — which is right, because the list on the site is kept per device, and a
token that quietly moved would make that list a lie.

## What it talks to

| | |
|---|---|
| `POST /api/app/pair` | code → token |
| `GET /api/app/me` | who am I, what may I |
| `GET /api/albums` | the albums |
| `GET /api/photos` | one page, with `year`/`country`/`event` or `q` |
| `GET /photos/<id>/<w>.webp` | one image |
| `POST /api/albums/share` | a link for someone without an account |
| `POST /y/…/contribute` | one photograph into an album that exists |
| `/api/upload/…` | batch → file → chunk → done → commit |
| `GET /api/albums/journey` | the opening animation for an album |
| `GET /api/photo/<id>/video` | a signed address the system player can fetch |
| `GET /tiles/<z>/<x>/<y>.png` | one map tile, through the site |

Every one of them needs `Authorization: Bearer fam_…`, and the server then
applies exactly the rights the same person has in a browser.

## Notes for whoever picks this up next

* **No image library.** Every request needs the auth header, so `RemoteImage.kt`
  is a small loader with an `LruCache` measured in bytes. It is 80 lines and it
  does not need to know about anything else.
* **No JSON library.** `org.json` ships with Android, and the models are read
  field by field in `Models.kt`. The server's names (`web_name`, `cover_rev`,
  `file_id`) are written out there; change one and the field silently comes
  back null.
* **Cleartext http is allowed.** A family server on the home network often has
  no certificate. Over the internet the address is https, and the main README
  says how.
* **The grid pins its cell width**, and the gap lives INSIDE the cell. A
  photograph scaled to fill is wider than its column; without `fillMaxWidth()`
  the card covers its neighbour. And an adaptive grid hands out its leftover
  width as it likes — telling it a `spacing` gave a phone its gap and let the
  cards touch on a tablet. Padding inside the card cannot be handed out.
* **The video player is the platform's own** (`VideoView`), not a library: the
  server sends one MP4 file, not a stream that switches quality, so nothing
  else is needed.
* **Notices are not wired up yet.** The server side is done and provider-
  agnostic (`app/notify.py`); Android needs a Firebase project before this app
  can register for them.
