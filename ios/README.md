# Roamlight — the iPhone and iPad app

It can do four things, and no more: **sign in, look at photographs, share and
upload.** Everything else is the server's work. The app computes nothing; it
shows and it passes on.

```
ios/
  Roamlight.xcodeproj/      written by hand, with a synchronised folder group
  Roamlight/
    RoamlightApp.swift   @main, the state, the colours out of site.css
    API.swift            every request to the site
    Models.swift         what the server sends back
    Keychain.swift       where the token lives
    PairView.swift       scanning the QR code
    AlbumsView.swift     the albums, the photographs, the search
    PhotoView.swift      one photograph large + the share link
    UploadView.swift     send photographs up from the camera roll
    SettingsView.swift   who am I, and how to get out
    RemoteImage.swift    an image loader that can set the `Authorization` header
    Assets.xcassets      the app icon and the accent colour
```

## How signing in works

1. On the site (in a browser) → user menu → **Phone & tablet** → *Show the code*.
2. In the app tap **Scan the code** → the camera reads the QR code.
3. The QR code holds `https://<site>/app/pair#c=<code>`. The app takes **both**
   out of it: the site's address **and** the code — so nobody has to type a URL.
4. The app trades the code for a token (`POST /api/app/pair`). That token lives
   in the **keychain**, `ThisDeviceOnly`.

From then on every request carries one header: `Authorization: Bearer fam_…`.
The server turns that into the same identity as for a browser — **the app sees
exactly what that user sees on the site**, not one row more.

There is no built-in site address. A hard-coded one would send the app's
requests to somebody else's machine.

## Building

```bash
open ios/Roamlight.xcodeproj          # or:
xcodebuild -project ios/Roamlight.xcodeproj -scheme Roamlight \
           -sdk iphoneos -configuration Debug CODE_SIGNING_ALLOWED=NO build
```

⚠ **The icon compiler (`actool`) needs an installed simulator runtime.** If
there is none (`xcrun simctl list runtimes` is empty), the build fails with

```
error: No available simulator runtimes for platform iphonesimulator
```

even though nothing is wrong with the Swift. Fix it with `xcodebuild
-downloadPlatform iOS`, or simply open the project in Xcode.

## Getting it onto a phone

- **With a developer account**: in Xcode, Signing & Capabilities → Team, then
  run it on the device. For a family: TestFlight.
- **Without an account**: a free Apple ID works too, but the app then runs for
  **seven days** before it has to be installed again.
- Set your own bundle identifier in the project before you build.

## What to keep in mind when the server changes

| App | Server |
|---|---|
| `API.pair` | `POST /api/app/pair` |
| `API.me` | `GET /api/app/me` |
| `API.albums` | `GET /api/albums` |
| `API.photos` | `GET /api/photos` |
| `API.facets` | `GET /api/facets` |
| Images | `/photos/{id}/{400\|1200\|2000\|2800}.webp` |
| `API.share` | `POST /api/albums/share` |
| Uploading | `/api/upload/batch` → `…/file` → `…/chunk` → `…/done` → `…/commit` |

⚠ The fields in `Models.swift` are named as they are on the server. Rename one
on the server and not here and you get **no error message** — you get an empty
list, and that looks like "no photographs".
