"""Where a place is.

Plenty of photographs have no GPS in them -- most cameras have no receiver. So
the map is built from what you type when you upload: a country and a place. Each
pair is looked up exactly once, and the answer stays in the database.

⚠ This is the only request the site makes to the outside world, and it goes out
  from the SERVER, never from a browser -- somebody looking at the gallery
  contacts nobody but you. Switch it off with FAMILY_GEOCODE=0.
"""
import json
import logging
import time
import urllib.parse
import urllib.request

from . import config, db

log = logging.getLogger("family")

URL = "https://nominatim.openstreetmap.org/search"
_last_call = [0.0]


def _lookup(place: str, country: str):
    """Nominatim allows one request a second. We keep to that."""
    query = ", ".join(x for x in (place, country) if x)
    params = urllib.parse.urlencode({
        "q": query, "format": "jsonv2", "addressdetails": "1", "limit": "1",
        "accept-language": "en"})
    wait = 1.1 - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(
        f"{URL}?{params}",
        headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read() or b"[]")
        if not data:
            return None
        hit = data[0]
        addr = hit.get("address") or {}
        # ⚠ Parsing belongs INSIDE the try: an odd answer used to take the
        # whole front page down with a 500.
        return {
            "lat": float(hit["lat"]), "lon": float(hit["lon"]),
            "display_name": (hit.get("display_name") or "")[:300],
            "region": ((addr.get("state") or addr.get("county")
                        or addr.get("region")) or "")[:120] or None,
        }
    except Exception as exc:                                    # noqa: BLE001
        log.warning("geocode %r: %s", query, exc)
        return None
    finally:
        _last_call[0] = time.time()


def lookup_cached(place: str, country: str):
    """From the database ONLY. For pages that must not make a network call."""
    place, country = (place or "").strip(), (country or "").strip()
    if not place:
        return None
    row = db.connect().execute(
        "SELECT * FROM places WHERE place=? AND country=? AND lat IS NOT NULL",
        (place, country)).fetchone()
    return dict(row) if row else None


def get(place: str, country: str):
    """From the database, or looked up once and kept there.

    ⚠ This makes a network call with a second of waiting built in. It belongs
    in /api/geocode or in the worker -- NOT in a page somebody is opening."""
    place = (place or "").strip()
    country = (country or "").strip()
    if not place:
        return None
    row = db.connect().execute(
        "SELECT * FROM places WHERE place=? AND country=?", (place, country)).fetchone()
    if row is not None:
        return dict(row) if row["lat"] is not None else None
    if not config.GEOCODE:
        return None
    hit = _lookup(place, country)
    with db.tx() as c:
        c.execute(
            "INSERT INTO places (place, country, lat, lon, display_name, region, source) "
            "VALUES (?,?,?,?,?,?,'nominatim') "
            "ON CONFLICT(place, country) DO UPDATE SET lat=excluded.lat, lon=excluded.lon, "
            "display_name=excluded.display_name, region=excluded.region",
            (place, country, hit["lat"] if hit else None, hit["lon"] if hit else None,
             hit["display_name"] if hit else None, hit["region"] if hit else None))
    if hit:
        log.info("geocode %r, %r -> %.4f, %.4f", place, country, hit["lat"], hit["lon"])
    else:
        log.warning("geocode %r, %r: nothing found", place, country)
    return hit


def resolve_all() -> dict:
    """Every place the site knows about that has not been looked up yet."""
    rows = db.connect().execute(
        "SELECT DISTINCT place, coalesce(country,'') AS country FROM photos "
        "WHERE state='ok' AND hidden=0 AND place IS NOT NULL AND place<>''").fetchall()
    found = missing = 0
    for r in rows:
        if get(r["place"], r["country"]):
            found += 1
        else:
            missing += 1
    return {"places": len(rows), "located": found, "not_found": missing}


# ---------------------------------------------------------------------------
#  As a job -- the lookup service asks for a second between requests
# ---------------------------------------------------------------------------
from .worker import handler                                     # noqa: E402


@handler("geocode")
def _job(job) -> None:
    """Look up every place that is not on the map yet.

    ⚠ A job and not part of a request: the lookup service wants a second
    between calls, and a page waiting on that is a page that hangs. It is
    kicked off after an album is saved, and once a night.
    """
    res = resolve_all()
    log.info("map: %s", res)
