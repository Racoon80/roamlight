"""The queries behind the gallery.

The gallery is served **dynamically**, not built as static pages. With fifty
photographs that makes no difference; with thirty thousand it does -- and those
arrive eventually. So: everything with LIMIT/OFFSET, and the filtering in SQL
rather than in Python.
"""
from . import acl, config, db

PAGE_SIZE = 60


def pair_by_orientation(photos: list) -> list:
    """Always two of the same shape side by side.

    A grid row is as tall as its tallest cell. One portrait photograph next to
    two landscape ones therefore tears open a hole exactly as tall as the
    difference -- which is what you could see on the page.

    The order moves as little as possible: every photograph keeps its place and
    its partner is pulled UP to it. The first of a pair never slides down. If an
    odd one is left over in a shape, the last one takes whatever partner is
    there -- at most one pair per page.
    """
    def portrait(p):
        return (p.get("height") or 0) > (p.get("width") or 0)

    rest = list(photos)
    out = []
    while rest:
        first = rest.pop(0)
        out.append(first)
        if not rest:
            break
        want = portrait(first)
        mate = 0
        for i, cand in enumerate(rest):
            if portrait(cand) == want:
                mate = i
                break
        out.append(rest.pop(mate))
    return out


def album_title(year: str, event: str) -> str:
    """"2016" + "Fragas de Sao Simao 2016"  ->  "2016 Fragas de Sao Simao".

    The year is already part of the folder structure. If it is in the name as
    well it shows up twice, and that reads wrong. So the site builds the title
    itself: the year first, then the name without its trailing year. ⚠ NOTHING
    is renamed on disk -- this is a way of showing it, not a change."""
    name = (event or "").strip()
    parts = name.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 4:
        name = parts[0].strip()
    year = (year or "").strip()
    return f"{year} {name}".strip() if year else name

# "On the site" is exactly one condition, and it lives here -- nowhere else.
ON_SITE = "state='ok' AND hidden=0"


def _where(f: dict, viewer=None):
    """⚠ `viewer` is not an option, it is the access control.

    Every road to a photograph goes through here: the lists, the overview, the
    map, the search, the neighbours. Leave it out and you get the whole
    library -- which is why the routes always pass it, and why
    `serve.allowed()` makes the same check again for direct image addresses.
    """
    sql, args = [ON_SITE], []
    clause, acl_args = acl.sql_clause(viewer)
    if clause:
        sql.append(clause); args.extend(acl_args)
    if f.get("year"):
        # ⚠ The ALBUM year, not the day it was taken. A photograph from 31
        # December belongs to the album it sits in, and the year has to be
        # changeable.
        sql.append("album_year=?"); args.append(str(f["year"]))
    if f.get("month"):
        sql.append("substr(taken_at,6,2)=?"); args.append(f"{int(f['month']):02d}")
    if f.get("country"):
        sql.append("country=?"); args.append(f["country"])
    if f.get("place"):
        sql.append("place=?"); args.append(f["place"])
    if f.get("event"):
        sql.append("event=?"); args.append(f["event"])
    if f.get("tag"):
        sql.append("id IN (SELECT photo_id FROM photo_tags pt JOIN tags g "
                   "ON g.id=pt.tag_id WHERE g.name=? COLLATE NOCASE)")
        args.append(f["tag"])
    if f.get("person"):
        sql.append("id IN (SELECT photo_id FROM photo_people pp JOIN people p "
                   "ON p.id=pp.person_id WHERE p.name=? COLLATE NOCASE)")
        args.append(f["person"])
    if f.get("owner"):
        sql.append("owner=?"); args.append(f["owner"])
    if f.get("untagged"):
        # "Nothing on it yet" -- that is the to-do list. Without it you do not
        # know two hours later where you stopped.
        sql.append("id NOT IN (SELECT photo_id FROM photo_people)")
    if f.get("in_collection") is not None:
        sql.append("id " + ("IN" if f["in_collection"] else "NOT IN") +
                   " (SELECT photo_id FROM album_photos)")
    if f.get("camera"):
        sql.append("camera=?"); args.append(f["camera"])
    if f.get("kind"):
        sql.append("kind=?"); args.append(f["kind"])
    if f.get("rating"):
        sql.append("rating>=?"); args.append(int(f["rating"]))
    if f.get("has_gps"):
        sql.append("gps_lat IS NOT NULL")
    if f.get("q"):
        # Without AI there is no content search. What there is, is what was
        # measured or typed -- and that is what gets searched here.
        # ⚠ SQLite does not mix `?` and `?1`: so the value appears seven times
        #   in the argument list, and there is no numbered placeholder anywhere.
        like = f"%{f['q']}%"
        sql.append(
            "(origin_path LIKE ? OR coalesce(title,'') LIKE ? "
            "OR coalesce(note,'') LIKE ? OR coalesce(camera,'') LIKE ? "
            "OR coalesce(place,'') LIKE ? OR coalesce(country,'') LIKE ? "
            "OR id IN (SELECT photo_id FROM photo_tags t JOIN tags g ON g.id=t.tag_id "
            "          WHERE g.name LIKE ?) "
            "OR id IN (SELECT photo_id FROM photo_people pp JOIN people p "
            "          ON p.id=pp.person_id WHERE p.name LIKE ?))")
        args.extend([like] * 8)
    return " AND ".join(sql), args


def list_photos(filters: dict, page: int = 1, page_size: int = PAGE_SIZE,
                include_hidden: bool = False, only_hidden: bool = False,
                viewer=None, pair: bool = True) -> dict:
    """`include_hidden` and `only_hidden` are ONLY for the admin register --
    the gallery itself never sees a hidden photograph.

    `pair`: for the two-column grid, two photographs of the same shape are put
    next to each other (no holes). The collage layout does NOT want that -- it
    passes `pair=False`, so the order stays strictly by date and time and
    nothing gets shuffled."""
    where, args = _where(filters, viewer)
    if only_hidden:
        where = where.replace(ON_SITE, "state='ok' AND hidden=1")
    elif include_hidden:
        where = where.replace(ON_SITE, "state='ok'")
    conn = db.connect()
    total = conn.execute(f"SELECT COUNT(*) FROM photos WHERE {where}", args).fetchone()[0]
    pages = max(1, -(-total // page_size))
    page = max(1, min(page, pages))
    rows = conn.execute(
        f"SELECT id, web_name, taken_at, width, height, country, place, camera, "
        f"       title, kind, rating, duration_s, hidden, rev "
        f"FROM photos WHERE {where} ORDER BY taken_at, id LIMIT ? OFFSET ?",
        (*args, page_size, (page - 1) * page_size),
    ).fetchall()
    photos = [dict(r) for r in rows]
    return {
        "total": total, "page": page, "pages": pages, "page_size": page_size,
        "photos": pair_by_orientation(photos) if pair else photos,
    }


def photo(photo_id: int, viewer=None):
    conn = db.connect()
    where, args = _where({}, viewer)
    row = conn.execute(f"SELECT * FROM photos WHERE id=? AND {where}",
                       (photo_id, *args)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["tags"] = [r["name"] for r in conn.execute(
        "SELECT g.name FROM tags g JOIN photo_tags t ON t.tag_id=g.id "
        "WHERE t.photo_id=? ORDER BY g.name", (photo_id,))]
    d["people"] = [r["name"] for r in conn.execute(
        "SELECT p.name FROM people p JOIN photo_people pp ON pp.person_id=p.id "
        "WHERE pp.photo_id=? ORDER BY p.name", (photo_id,))]
    return d


def neighbours(photo_id: int, filters: dict, viewer=None):
    """Previous and next, in the same order as the list."""
    where, args = _where(filters, viewer)
    conn = db.connect()
    cur = conn.execute(f"SELECT taken_at, id FROM photos WHERE id=? AND {where}",
                       (photo_id, *args)).fetchone()
    if cur is None:
        return None, None
    prev = conn.execute(
        f"SELECT id FROM photos WHERE {where} AND (taken_at, id) < (?, ?) "
        f"ORDER BY taken_at DESC, id DESC LIMIT 1", (*args, cur["taken_at"], cur["id"])).fetchone()
    nxt = conn.execute(
        f"SELECT id FROM photos WHERE {where} AND (taken_at, id) > (?, ?) "
        f"ORDER BY taken_at, id LIMIT 1", (*args, cur["taken_at"], cur["id"])).fetchone()
    return (prev["id"] if prev else None), (nxt["id"] if nxt else None)


def structure(viewer=None) -> dict:
    """Year -> country -> album, with counts. This is the navigation."""
    where, args = _where({}, viewer)
    rows = db.connect().execute(
        f"SELECT coalesce(album_year,'—') AS year, coalesce(country,'—') AS country, "
        f"       coalesce(event,'—') AS event, COUNT(*) AS n "
        f"FROM photos WHERE {where} "
        f"GROUP BY year, country, event", args
    ).fetchall()
    out = {}
    for r in rows:
        y = out.setdefault(r["year"] or "—", {})
        c = y.setdefault(r["country"], {})
        c[r["event"]] = c.get(r["event"], 0) + r["n"]
    return dict(sorted(out.items(), reverse=True))


def facets(viewer=None) -> dict:
    """What there is to choose from in the filters.

    ⚠ The viewer belongs here too: a list of countries that names a country
    which appears in no album this person can see gives the album away."""
    conn = db.connect()
    where, args = _where({}, viewer)
    def col(name):
        return [r[0] for r in conn.execute(
            f"SELECT DISTINCT {name} FROM photos WHERE {where} AND {name} IS NOT NULL "
            f"AND {name}<>'' ORDER BY {name}", args)]
    return {
        "years": [r[0] for r in conn.execute(
            f"SELECT DISTINCT album_year FROM photos WHERE {where} "
            f"ORDER BY 1 DESC", args) if r[0]],
        "countries": col("country"), "places": col("place"), "cameras": col("camera"),
        # ⚠ The names as well. A list of everybody who is tagged anywhere tells
        # a family member who is in the photographs they cannot see. So: only
        # names that sit on a photograph they can see.
        "tags": [r[0] for r in conn.execute(
            f"SELECT DISTINCT g.name FROM tags g JOIN photo_tags t ON t.tag_id=g.id "
            f"JOIN photos p ON p.id=t.photo_id WHERE {where} ORDER BY g.name", args)],
        "people": [r[0] for r in conn.execute(
            f"SELECT DISTINCT n.name FROM people n JOIN photo_people pp "
            f"ON pp.person_id=n.id JOIN photos p ON p.id=pp.photo_id "
            f"WHERE {where} ORDER BY n.name", args)],
    }


def cover(filters: dict = None, viewer=None):
    """The newest photograph of a selection -- used as the cover."""
    where, args = _where(filters or {}, viewer)
    row = db.connect().execute(
        f"SELECT id FROM photos WHERE {where} ORDER BY is_cover DESC, taken_at DESC, id DESC LIMIT 1", args
    ).fetchone()
    return row["id"] if row else None


def rev(photo_id) -> int:
    """The revision of a photograph -- it hangs on every image address as ?v=,
    so the browser may cache for a long time and still gets the new picture
    after a rotation."""
    if not photo_id:
        return 0
    row = db.connect().execute(
        "SELECT rev FROM photos WHERE id=?", (photo_id,)).fetchone()
    return row["rev"] if row else 0


def covers(viewer=None) -> dict:
    """One cover per album, in a single query -- otherwise the front page would
    be one query per card."""
    where, args = _where({}, viewer)
    rows = db.connect().execute(
        f"SELECT coalesce(album_year,'—') AS year, coalesce(country,'—') AS country, "
        f"       coalesce(event,'—') AS event, id, taken_at, rev FROM photos WHERE {where} "
        f"ORDER BY is_cover DESC, taken_at DESC, id DESC", args
    ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(f"{r['year']}/{r['country']}/{r['event']}", r["id"])
    return out


def albums(viewer=None) -> list:
    """One row per album, with cover and count -- for the index on the front
    page. One query, not one per card."""
    where, args = _where({}, viewer)
    rows = db.connect().execute(
        f"SELECT coalesce(album_year,'—') AS year, coalesce(country,'—') AS country, "
        f"       coalesce(event,'—') AS event, id, taken_at, rev FROM photos WHERE {where} "
        f"ORDER BY is_cover DESC, taken_at DESC, id DESC", args
    ).fetchall()
    out = {}
    for r in rows:
        event = r["event"]
        key = (r["year"], r["country"], event)
        a = out.setdefault(key, {"year": r["year"], "country": r["country"],
                                 "event": event,
                                 "title": album_title(r["year"], event),
                                 "n": 0, "cover": r["id"], "cover_rev": r["rev"],
                                 "latest": r["taken_at"]})
        a["n"] += 1
    # ⚠ The YEAR counts first, not the most recent capture date.
    #
    # A single photograph without metadata gets the file date -- and that is the
    # day it was copied onto the share. Three such photographs in an album from
    # 2019 carried today's date, and that put the whole album at the top of a
    # list that says "most recent first". The year comes from a person and
    # cannot be twisted by one file.
    return sorted(out.values(),
                  key=lambda a: (a["year"] or "", a["latest"] or "", a["event"]),
                  reverse=True)


def map_points(viewer=None) -> list:
    """One point per album.

    Two sources, and the order is deliberate:
      1. **GPS out of the photographs** -- measured, not guessed. Phone
         photographs always have it, a camera when the app was on.
      2. **The place** typed at upload, looked up once.

    The site says which of the two a point came from. A guessed point that
    looks like a measured one is worse than no point at all.
    """
    from . import geo
    where, args = _where({}, viewer)
    rows = db.connect().execute(
        f"SELECT coalesce(album_year,'') AS year, coalesce(country,'') AS country, "
        f"       coalesce(event,'—') AS event, id, place, gps_lat, gps_lon, taken_at "
        f"FROM photos WHERE {where} ORDER BY is_cover DESC, taken_at DESC, id DESC", args
    ).fetchall()
    albums = {}
    for r in rows:
        event = r["event"]
        key = (r["year"], r["country"], event)
        a = albums.setdefault(key, {"year": r["year"], "country": r["country"],
                                    "event": event, "cover": r["id"], "n": 0,
                                    "place": r["place"], "gps": []})
        a["n"] += 1
        if not a["place"] and r["place"]:
            a["place"] = r["place"]
        if r["gps_lat"] is not None and r["gps_lon"] is not None:
            a["gps"].append((r["gps_lat"], r["gps_lon"]))

    out = []
    for a in albums.values():
        if a["gps"]:
            lat = sum(p[0] for p in a["gps"]) / len(a["gps"])
            lon = sum(p[1] for p in a["gps"]) / len(a["gps"])
            source = "from the photographs"
        else:
            # Only what has already been looked up -- a page fetches nothing.
            hit = geo.lookup_cached(a["place"] or "", a["country"])
            if not hit:
                continue
            lat, lon, source = hit["lat"], hit["lon"], "from the place name"
        out.append({
            "lat": round(lat, 6), "lon": round(lon, 6),
            "title": album_title(a["year"], a["event"]),
            "where": ", ".join(x for x in (a["place"], a["country"]) if x),
            "n": a["n"], "cover": a["cover"], "source": source,
            "url": f"/y/{a['year']}/{a['country']}/{a['event']}",
        })
    return out


def strips(per_album: int = 8, viewer=None) -> dict:
    """A few photographs per album -- for the layouts that show a strip."""
    where, args = _where({}, viewer)
    rows = db.connect().execute(
        f"SELECT coalesce(album_year,'—') AS year, coalesce(country,'—') AS country, "
        f"       coalesce(event,'—') AS event, id, taken_at, rev FROM photos WHERE {where} "
        f"ORDER BY taken_at, id", args
    ).fetchall()
    out = {}
    for r in rows:
        key = f"{r['year']}/{r['country']}/{r['event']}"
        bucket = out.setdefault(key, [])
        if len(bucket) < per_album:
            bucket.append({"id": r["id"]})
    return out


def by_day(photos: list) -> list:
    """Grouped by day, in the order they arrive."""
    out, cur, day = [], [], None
    for p in photos:
        d = (p.get("taken_at") or "")[:10] or "—"
        if d != day:
            if cur:
                out.append((day, cur))
            day, cur = d, []
        cur.append(p)
    if cur:
        out.append((day, cur))
    return out
