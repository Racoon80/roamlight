"""Map tiles run through this server.

Why not straight from the browser: then every family member who opens the map
tells OpenStreetMap which area they are looking at -- and with family
photographs that is the area where they live. This way the browser only ever
talks to us, the content policy can stay at `img-src 'self'`, and each tile is
fetched from OSM exactly once.

Two modes:

  * normal -- a missing tile is fetched from OSM and cached.
  * offline (`FAMILY_TILE_OFFLINE=1`) -- from the cache ONLY. No request ever
    leaves; the map lives entirely on this machine. The seeding job has to have
    run first (see app/tileseed.py). A missing tile comes back transparent, so
    the map background shows through.

If a tile is missing and cannot be fetched (network error, OSM throttling), a
transparent tile is served rather than an error -- otherwise the map turns into
a checkerboard of holes.
"""
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from . import config

log = logging.getLogger("family")
HEADERS = {"Cache-Control": "private, max-age=604800", "X-Robots-Tag": "noindex"}

MAX_ZOOM = 18            # the map never asks for more (site.js)
MAX_BYTES = 1024 * 1024  # an OSM tile is about 20-60 kB

# A 1x1 transparent PNG. The map stretches it to 256x256 with CSS -- so it is
# invisible, and the map background shows through.
_BLANK = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049"
    "454e44ae426082")
# ⚠ An empty tile is NOT cached (`no-store`): otherwise the hole stays in the
# browser even once the tile would be available. This way the browser asks
# again next time -- and then it heals itself.
_BLANK_HEADERS = {"Cache-Control": "no-store", "X-Tile": "blank", "X-Robots-Tag": "noindex"}


def _blank() -> Response:
    return Response(content=_BLANK, media_type="image/png", headers=_BLANK_HEADERS)


def _serve(path: Path) -> FileResponse:
    return FileResponse(path, media_type="image/png", headers=HEADERS)


def _fetch(z: int, x: int, y: int, path: Path, attempts: int = 3) -> None:
    """Fetch one tile from OSM and store it at `path`.

    ⚠ With a retry: OSM throttles a burst (a whole new area at once) with 429
    or 5xx. A short back-off and one more try absorbs that -- otherwise the
    tiles stay empty. A corrupt PNG is NOT retried; that is not throttling."""
    url = config.TILE_URL.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read(MAX_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and i < attempts - 1:
                time.sleep(0.5 * (i + 1)); continue
            raise
        except (urllib.error.URLError, OSError):
            if i < attempts - 1:
                time.sleep(0.5 * (i + 1)); continue
            raise
    if len(data) > MAX_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"keng PNG oder ze grouss ({len(data)} B)")
    path.parent.mkdir(parents=True, exist_ok=True)
    # ⚠ One temporary file PER REQUEST. `path.with_suffix(".part")` would be
    # the same file for two parallel requests, and the second `replace()`
    # would fail with FileNotFoundError -- a 500.
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".part")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp_name, path)


def tile(z: int, x: int, y: int):
    if not (0 <= z <= MAX_ZOOM) or not (0 <= x < 2 ** z) or not (0 <= y < 2 ** z):
        raise HTTPException(status_code=404, detail="no such tile")
    path = config.TILE_CACHE / str(z) / str(x) / f"{y}.png"

    # Offline: cache only. A missing tile is transparent -- no request.
    if config.TILE_OFFLINE:
        return _serve(path) if path.is_file() else _blank()

    fresh = (path.is_file()
             and time.time() - path.stat().st_mtime < config.TILE_MAX_AGE_DAYS * 86400)
    if fresh:
        return _serve(path)

    try:
        _fetch(z, x, y, path)
    except Exception as exc:                                    # noqa: BLE001
        # OSM throttles fast zooming. An old tile, or a transparent one, beats
        # an error and a hole in the map.
        if path.is_file():
            return _serve(path)
        log.warning("Kachel %s/%s/%s: %s", z, x, y, exc)
        return _blank()
    return _serve(path)
