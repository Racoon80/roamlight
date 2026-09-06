"""Making the map work offline: load the tiles onto this machine in advance.

Why: otherwise every tile is only fetched when somebody looks at it -- and the
tile server throttles fast zooming (429), which leaves empty squares.
Pre-loaded and served locally, the map lives entirely here and nothing leaves
the machine when somebody opens it.

What gets loaded:
  * z0..BASE for the whole area of all places together (the overview).
  * z(BASE+1)..MAX for a small box (+/-RADIUS tiles) around each place.

Idempotent: a tile that is already there is skipped -- and only a REAL download
waits out the delay. So the job can simply be run again when new places have
appeared: it fetches only what is missing.

It runs as a job, NOT inside a request -- one pass takes minutes.
"""
import logging
import math
import os
import tempfile
import time
import urllib.request
from pathlib import Path

from . import config, db

log = logging.getLogger("family")

MAX_BYTES = 1024 * 1024
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _deg2tile(lat: float, lon: float, z: int):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(max(-85.05, min(85.05, lat)))
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def _places():
    rows = db.connect().execute(
        "SELECT lat, lon FROM places WHERE lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()
    return [(r["lat"], r["lon"]) for r in rows]


def _box(lat_top, lon_left, lat_bot, lon_right, z, into):
    x0, y0 = _deg2tile(lat_top, lon_left, z)
    x1, y1 = _deg2tile(lat_bot, lon_right, z)
    for x in range(min(x0, x1), max(x0, x1) + 1):
        for y in range(min(y0, y1), max(y0, y1) + 1):
            into.add((z, x, y))


def tileset() -> set:
    """Every (z, x, y) the map needs offline. Bounded -- only the places."""
    pts = _places()
    want: set = set()
    if not pts:
        return want
    base = config.TILE_SEED_BASE_ZOOM
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    pad = 0.5
    # The overview: the whole area of every place, at low zoom
    for z in range(0, base + 1):
        _box(max(lats) + pad, min(lons) - pad, min(lats) - pad, max(lons) + pad, z, want)
    # Detail: a block of (2R+1)^2 tiles around each place, at EVERY zoom level.
    # A radius in tiles (not in degrees) keeps the view around a place filled
    # at every zoom -- in degrees, medium zoom would give you a single tile.
    r = config.TILE_SEED_RADIUS_TILES
    for lat, lon in pts:
        for z in range(base + 1, config.TILE_SEED_MAX_ZOOM + 1):
            cx, cy = _deg2tile(lat, lon, z)
            n = 2 ** z
            for x in range(cx - r, cx + r + 1):
                for y in range(cy - r, cy + r + 1):
                    if 0 <= x < n and 0 <= y < n:
                        want.add((z, x, y))
    return want


def plan() -> dict:
    """What the seeding would do -- without downloading anything."""
    want = tileset()
    have = sum(1 for (z, x, y) in want
               if (config.TILE_CACHE / str(z) / str(x) / f"{y}.png").is_file())
    return {"places": len(_places()), "tiles": len(want), "cached": have,
            "todo": len(want) - have, "base_zoom": config.TILE_SEED_BASE_ZOOM,
            "max_zoom": config.TILE_SEED_MAX_ZOOM, "offline": config.TILE_OFFLINE}


def _download(z: int, x: int, y: int, path: Path) -> None:
    url = config.TILE_URL.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or not data.startswith(_PNG_SIG):
        raise ValueError(f"keng PNG ({len(data)} B)")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".part")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def seed(progress_every: int = 250) -> dict:
    want = sorted(tileset())
    total = len(want)
    new = skip = fail = 0
    log.info("Kaart-Seed: %d Kacheln (z0..%d Iwwersiicht, ..%d Detail)",
             total, config.TILE_SEED_BASE_ZOOM, config.TILE_SEED_MAX_ZOOM)
    for i, (z, x, y) in enumerate(want, 1):
        path = config.TILE_CACHE / str(z) / str(x) / f"{y}.png"
        if path.is_file():
            skip += 1
            continue
        try:
            _download(z, x, y, path)
            new += 1
            time.sleep(config.TILE_SEED_DELAY)     # be polite to the tile server
        except Exception as exc:                   # noqa: BLE001
            fail += 1
            log.warning("Seed %s/%s/%s: %s", z, x, y, exc)
            time.sleep(min(5.0, config.TILE_SEED_DELAY * 6))   # throttled -> back off
        if i % progress_every == 0:
            log.info("Seed %d/%d (nei %d, do %d, feeler %d)", i, total, new, skip, fail)
    res = {"tiles": total, "new": new, "skipped": skip, "failed": fail}
    log.info("map seeding done: %s", res)
    return res


# ---------------------------------------------------------------------------
#  As a job -- one pass takes minutes, which does not belong in a request
# ---------------------------------------------------------------------------
from .worker import handler                                    # noqa: E402


@handler("tileseed")
def _job(job) -> None:
    seed()
