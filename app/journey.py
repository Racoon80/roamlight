"""The journey: an album gets a departure and a means of travel, and the map
plays a short animation from there to the album when it is opened.

The destination is already known -- the album's place, from GPS or looked up,
exactly like its pin on the map. What is new is the DEPARTURE (looked up once)
and the means of travel (car | bus | train | plane).
"""
import json
import logging
import urllib.error
import urllib.request

from . import config, db, geo

log = logging.getLogger("family")

MODES = ("car", "bus", "train", "plane")
ROAD_MODES = ("car", "bus")     # the ones that follow an actual road


def _road_route(from_ll, to_ll):
    """A road route, fetched once, server-side. Returns [[lat,lon],...] or None
    (no service, an error, or no route)."""
    base = (config.OSRM_URL or "").rstrip("/")
    if not base:
        return None
    url = (f"{base}/route/v1/driving/"
           f"{from_ll[1]},{from_ll[0]};{to_ll[1]},{to_ll[0]}"
           f"?overview=simplified&geometries=geojson")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("OSRM route: %s", exc)
        return None
    if d.get("code") != "Ok" or not d.get("routes"):
        return None
    coords = d["routes"][0].get("geometry", {}).get("coordinates") or []
    if len(coords) < 2:
        return None
    return [[round(c[1], 6), round(c[0], 6)] for c in coords]   # -> [lat, lon]


def _key(year: str, country: str, event: str) -> str:
    # ⚠ The same shape as the album key everywhere else (acl, published): those
    # fields arrive already coalesced to a dash from the interface.
    return f"{year or '—'}/{country or '—'}/{event or '—'}"


def _album_dest(year: str, country: str, event: str):
    """The coordinates of the destination -- exactly like the map pin: the
    average of the GPS positions if there are any, otherwise the looked-up
    place. Returns (lat, lon) or None."""
    rows = db.connect().execute(
        "SELECT gps_lat, gps_lon, place, country FROM photos WHERE state='ok' AND hidden=0 "
        "AND coalesce(album_year,'—')=? AND coalesce(country,'—')=? AND coalesce(event,'—')=?",
        (year or "—", country or "—", event or "—")).fetchall()
    gps = [(r["gps_lat"], r["gps_lon"]) for r in rows
           if r["gps_lat"] is not None and r["gps_lon"] is not None]
    if gps:
        return sum(p[0] for p in gps) / len(gps), sum(p[1] for p in gps) / len(gps)
    for r in rows:
        hit = geo.lookup_cached(r["place"] or "", r["country"] or "")
        if hit:
            return hit["lat"], hit["lon"]
    return None


def _geo_ll(name: str):
    name = (name or "").strip()
    if not name:
        return None
    hit = geo.get(name, "")
    return [hit["lat"], hit["lon"]] if hit else None


def _build_legs(start_ll, legs_in):
    """`legs_in`: [{transport,name}] -> [{name,lat,lon,transport,route}]. For a
    car or bus leg the road is computed from the previous point (once,
    server-side)."""
    out, prev = [], start_ll
    for leg in legs_in:
        name = (leg.get("name") or "").strip()
        if not name:
            continue
        transport = leg.get("transport") if leg.get("transport") in MODES else "car"
        ll = _geo_ll(name)
        route = _road_route(prev, ll) if (ll and prev and transport in ROAD_MODES) else None
        out.append({"name": name, "lat": (ll[0] if ll else None),
                    "lon": (ll[1] if ll else None), "transport": transport, "route": route})
        if ll:
            prev = ll
    return out


def _save(key, departure, lat, lon, transport, route, legs, legs_on=1):
    with db.tx() as c:
        c.execute(
            "INSERT INTO album_journey (album_key, departure, dep_lat, dep_lon, transport, route, legs, legs_on) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(album_key) DO UPDATE SET "
            "departure=excluded.departure, dep_lat=excluded.dep_lat, dep_lon=excluded.dep_lon, "
            "transport=excluded.transport, route=excluded.route, legs=excluded.legs, "
            "legs_on=excluded.legs_on",
            (key, departure, lat, lon, transport,
             json.dumps(route) if route else None,
             json.dumps(legs) if legs else None, 1 if legs_on else 0))


def set_journey(year: str, country: str, event: str, departure: str,
                transport: str, legs=None, multi=None) -> dict:
    """Set the journey. Two shapes:

    * **simple**: a departure and one means of travel all the way to the album.
    * **multi-hop** (optional, `legs`): a chain of "by <mode> to <place>" (car
      to the airport, plane to Malaga, bus to the coast). The last stop is the
      destination.

    `multi` is the switch from the workshop: `True` = the stops count, `False`
    = the simple trip counts. `None` = as before, meaning stops count if there
    are any (so an older client that does not send the switch still works).

    ⚠ **Off does not mean gone.** With `multi=False` the stops stay, just
    without coordinates and without roads (`legs_on=0`). Otherwise a chain of
    three stops would be gone with one click, and somebody would have to type
    them again when they switch it back on.

    An empty departure AND no stops = delete. Everything is looked up and the
    roads are computed server-side -- which is why this is an owner's path."""
    key = _key(year, country, event)
    departure = (departure or "").strip()
    transport = transport if transport in MODES else "car"
    legs_in = [l for l in (legs or []) if (l.get("name") or "").strip()]
    if multi is None:
        multi = bool(legs_in)
    if not departure and not legs_in:
        with db.tx() as c:
            c.execute("DELETE FROM album_journey WHERE album_key=?", (key,))
        return {"cleared": True}
    # Starting point: the departure that was typed in, otherwise home.
    start_ll = _geo_ll(departure) if departure else [config.HOME_LAT, config.HOME_LON]
    lat = start_ll[0] if start_ll else None
    lon = start_ll[1] if start_ll else None
    if legs_in and multi:
        built = _build_legs(start_ll, legs_in)
        first_mode = built[0]["transport"] if built else transport
        _save(key, departure or config.HOME_NAME, lat, lon, first_mode, None, built)
        return {"departure": departure or config.HOME_NAME, "legs": len(built),
                "located": bool(built) and all(l["lat"] is not None for l in built),
                "on_road": any(l["route"] for l in built)}
    # The simple case: one leg to the destination.
    route = None
    if transport in ROAD_MODES and start_ll:
        dest = _album_dest(year, country, event)
        if dest:
            route = _road_route(start_ll, [dest[0], dest[1]])
    # ⚠ The stops are stored RAW (mode and name, no coordinates): that way the
    #   work stays if the switch is turned back on, and nothing at all goes out
    #   over the network for something that is not being shown.
    _save(key, departure, lat, lon, transport, route,
          [{"transport": l.get("transport", "car"), "name": l["name"].strip()}
           for l in legs_in] or None,
          legs_on=0 if legs_in else 1)
    return {"departure": departure, "transport": transport, "multi": False,
            "located": bool(start_ll), "on_road": bool(route)}


def _haversine_km(a, b) -> float:
    import math
    r = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2-la1)/2)**2
         + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2)
    return 2*r*math.asin(min(1, math.sqrt(h)))


def _auto_mode(frm, to) -> str:
    """For an automatic trip: a plane when it is far, otherwise a car."""
    return "plane" if _haversine_km(frm, to) > config.JOURNEY_PLANE_KM else "car"


def get_journey(year: str, country: str, event: str):
    """Everything the animation needs, or None (when the destination cannot be
    located). EVERY album -- including a new one with nothing set -- gets an
    opening animation: with no departure of its own it flies or drives from
    `config.HOME` (a stylised arc). A journey set by hand overrides that, with
    its own departure, mode and real road.

    `{departure, transport, from:[lat,lon], to:[lat,lon], route}`."""
    dest = _album_dest(year, country, event)
    if not dest:
        return None                       # keng Uertschaft -> keng Animatioun
    to = [round(dest[0], 6), round(dest[1], 6)]
    row = db.connect().execute(
        "SELECT departure, dep_lat, dep_lon, transport, route, legs, legs_on "
        "FROM album_journey WHERE album_key=?",
        (_key(year, country, event),)).fetchone()
    # --- multi-hop (optional, and only when the switch is on) ---
    if row and row["legs"] and row["legs_on"]:
        try:
            stored = json.loads(row["legs"])
        except (ValueError, TypeError):
            stored = None
        if stored:
            frm = ([round(row["dep_lat"], 6), round(row["dep_lon"], 6)]
                   if row["dep_lat"] is not None else [config.HOME_LAT, config.HOME_LON])
            legs = [{"to": [round(l["lat"], 6), round(l["lon"], 6)],
                     "transport": l.get("transport", "car"),
                     "route": l.get("route"), "name": l.get("name")}
                    for l in stored if l.get("lat") is not None]
            if legs:
                return {"departure": row["departure"], "from": frm, "legs": legs}
    # --- the simple case (one departure to the destination) ---
    if row and row["dep_lat"] is not None:
        route = None
        if row["route"]:
            try:
                route = json.loads(row["route"])
            except (ValueError, TypeError):
                route = None
        return {"departure": row["departure"],
                "from": [round(row["dep_lat"], 6), round(row["dep_lon"], 6)],
                "legs": [{"to": to, "transport": row["transport"], "route": route, "name": None}]}
    # --- automatesch: vun Doheem aus, Modus no Distanz, Bou (keng Strooss) ---
    frm = [config.HOME_LAT, config.HOME_LON]
    return {"departure": config.HOME_NAME, "from": frm,
            "legs": [{"to": to, "transport": _auto_mode(frm, dest), "route": None, "name": None}]}


def for_form(year: str, country: str, event: str) -> dict:
    """What the workshop box is filled in with: the departure text, the mode,
    and (optionally) the stops of a multi-hop journey."""
    row = db.connect().execute(
        "SELECT departure, transport, legs, legs_on FROM album_journey WHERE album_key=?",
        (_key(year, country, event),)).fetchone()
    legs = []
    if row and row["legs"]:
        try:
            legs = [{"transport": l.get("transport", "car"), "name": l.get("name", "")}
                    for l in json.loads(row["legs"])]
        except (ValueError, TypeError):
            legs = []
    return {"departure": row["departure"] if row else "",
            "transport": row["transport"] if row else "car", "legs": legs,
            # The switch in the workshop: on when there ARE stops and they count.
            "multi": bool(legs and row["legs_on"])}
