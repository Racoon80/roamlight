"""Serving the pictures.

Everything here sits behind the sign-in. There is **no** path that hands out a
file without one -- not a thumbnail, not even the blurred placeholder.

The web sizes are computed on demand and then cached. The master is a JPEG, so
a new size costs milliseconds and never a RAW decode.
"""
import threading
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from . import acl, config, convert, db, images

_MIME = {"avif": "image/avif", "webp": "image/webp", "jpg": "image/jpeg"}
_locks = {}
_locks_guard = threading.Lock()

# ⚠ `private` and not `no-store`: the browser looking at the photograph may
# keep it (it is on the screen anyway), but NO shared cache and no CDN may. The
# address carries ?v=<rev>, which is what makes `immutable` safe here.
# These headers belong on EVERY image answer. A proxy in front usually sets
# them too -- and `add_header` does not inherit into a location that has one of
# its own, so they are set in both places on purpose.
HEADERS = {
    "Cache-Control": "private, max-age=31536000, immutable",
    "CDN-Cache-Control": "no-store",
    "Cloudflare-CDN-Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow, noimageindex, noarchive",
}


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _fit(want: int, real) -> int:
    """The largest step that is not wider than the photograph itself."""
    if not real or want <= real:
        return want
    matching = [w for w in config.DERIVATIVE_WIDTHS if w <= real]
    return max(matching) if matching else min(config.DERIVATIVE_WIDTHS)


def allowed(photo_id: int, admin: bool = False, viewer=None):
    """The ONE place that decides whether a photograph may leave the building.

    ⚠ It has to be asked BEFORE the cache. An earlier version first looked
    whether the derivative was already on disk -- so a photograph that had been
    shown once and was then HIDDEN kept coming out of /photos/<id>/400.webp.
    The ids are a sequence, so they are found by counting. Hiding did not do
    what it promised."""
    row = db.connect().execute(
        "SELECT web_name, hidden, album_year, country, event, width FROM photos "
        "WHERE id=? AND state='ok'", (photo_id,)
    ).fetchone()
    if row is None or not row["web_name"]:
        raise HTTPException(status_code=404, detail="not found")
    if row["hidden"] and not admin:
        raise HTTPException(status_code=404, detail="not found")
    # ⚠ The album rights. Without this the whole separation is a facade: the
    # ids are a sequence, and /photos/117/400.webp needs no page to reach. A
    # 404 and not a 403 -- "does not exist" gives away less than "not allowed".
    if not acl.may_see(viewer, row["album_year"], row["country"], row["event"]):
        raise HTTPException(status_code=404, detail="not found")
    return row


def master_path(photo_id: int, admin: bool = False, viewer=None) -> Path:
    row = allowed(photo_id, admin, viewer)
    p = config.WEB_DIR / row["web_name"]
    if not p.is_file():
        raise HTTPException(status_code=404, detail="the master is missing")
    return p


def derivative(photo_id: int, width: int, ext: str, admin: bool = False,
               viewer=None) -> FileResponse:
    if width not in config.DERIVATIVE_WIDTHS or ext not in ("avif", "webp"):
        raise HTTPException(status_code=404, detail="no such size")
    row = allowed(photo_id, admin, viewer)   # always first, never after a cache hit
    # ⚠ A photograph is never enlarged -- `images.build_derivatives` skips a
    # width bigger than the photograph itself. So a request for 2800 on a
    # photograph that is 1200 wide answered 404, and the browser showed a
    # broken image. That is exactly what a phone photograph (1200x1600) did to
    # an album cover when the page asked for 2800.
    #
    # So the request is pushed down to the largest step that exists. Always
    # answering with something is the right call: whoever asks for a picture
    # gets a picture.
    width = _fit(width, row["width"])
    out_dir = convert.derivative_dir(photo_id)
    target = out_dir / f"{width}.{ext}"
    if not target.is_file():
        with _lock(f"{photo_id}:{width}"):
            if not target.is_file():
                images.build_derivatives(master_path(photo_id, admin, viewer), out_dir, [width])
    if not target.is_file():
        raise HTTPException(status_code=404, detail="could not be built")
    return FileResponse(target, media_type=_MIME[ext], headers=HEADERS)


def lqip(photo_id: int, admin: bool = False, viewer=None) -> str:
    allowed(photo_id, admin, viewer)
    out_dir = convert.derivative_dir(photo_id)
    f = out_dir / "lqip.txt"
    if not f.is_file():
        with _lock(f"{photo_id}:lqip"):
            if not f.is_file():
                images.build_derivatives(master_path(photo_id, admin, viewer), out_dir, [])
    try:
        return f.read_text()
    except OSError:
        return ""


def master(photo_id: int, admin: bool = False, viewer=None) -> FileResponse:
    """The full master. Only reachable by somebody who signed in."""
    p = master_path(photo_id, admin, viewer)
    return FileResponse(p, media_type="image/jpeg", headers=HEADERS,
                        filename=p.name)


def video(photo_id: int, admin: bool = False, viewer=None) -> FileResponse:
    """The playable web copy (MP4). It sits next to the poster (.jpg -> .mp4).

    `FileResponse` answers range requests by itself (Accept-Ranges), so a
    browser can jump around in the video without loading all of it. The album
    rights go through `allowed()`, exactly as for a picture."""
    row = allowed(photo_id, admin, viewer)
    p = (config.WEB_DIR / row["web_name"]).with_suffix(".mp4")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="no video")
    return FileResponse(p, media_type="video/mp4",
                        headers={"Cache-Control": "private, max-age=604800",
                                 "X-Robots-Tag": "noindex"})
