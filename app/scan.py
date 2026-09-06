"""Walking the library and picking up what the site does not know yet.

Needed in exactly two cases:
  * photographs copied straight into a folder (not through the upload form),
  * and after the database has lost something while the files are still there.

A pass **only reads**. It creates no file, touches none and deletes none -- it
adds rows to the database and jobs to the queue.
"""
import logging
from pathlib import Path

from . import av, config, db, library, meta, tree
from .worker import enqueue

log = logging.getLogger("family")


def parts_of(rel: Path):
    """<year>/<country>/<event>/file  or  <year>/<event>/file (the older shape)."""
    p = rel.parts
    if len(p) >= 4:
        return p[0], p[1], p[2]
    if len(p) == 3:
        return p[0], None, p[1]
    return (p[0] if p else None), None, None


def place_from_event(event: str) -> str:
    """"Fragas de Sao Simao 2016" -> "Fragas de Sao Simao".

    That is a guess, not a truth -- but it is the convention across a whole
    library, and it beats an empty field. It can be changed on the site."""
    e = (event or "").strip()
    parts = e.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 4:
        return parts[0].strip()
    return e


def adopt(path: Path, rel: str):
    """Take a file the site does not know yet into the database.

    Returns the photo id, or `None` when the file is not a picture. It stands
    on its own because the sync needs exactly the same thing -- and two copies
    of this logic would drift apart.
    """
    ok, why = meta.looks_like_media(path)
    if not ok:
        log.warning("Scan: %s iwwersprongen -- %s", rel, why)
        return None
    # ⚠ Virus scan. A file that arrived directly in the library (a stick, an
    # export from somewhere) is scanned before the site gives it a row. If it
    # is not clean it gets `state='infected'`, NO conversion job and therefore
    # no master -- it never reaches the site. It is NOT deleted: the original
    # belongs to whoever put it there, and it is listed in the admin so they
    # can remove it themselves.
    av_state, av_detail = av.scan(path)
    info = meta.read(path)
    year, country, event = parts_of(Path(rel))
    place = place_from_event(event or "")
    sha = library.sha256_of(path)
    st = path.stat()
    # ⚠ Two passes can find the same file at the same moment: the nightly sync
    # and the "scan" button, or two worker threads. Both read their list of
    # "what the site knows" right at the start, and at that point the file was
    # unknown to both. The second one then died with
    # `UNIQUE constraint failed` -- and the whole scan answered 500. So: catch
    # it and take the row the first one created.
    import sqlite3 as _s3
    with db.tx() as c:
        try:
            cur = c.execute(
                "INSERT INTO photos (origin_root, origin_path, origin_sha256, origin_bytes,"
                " origin_mtime, origin_kind, country, place, album_year, event,"
                " taken_at, taken_source, camera,"
                " lens, iso, aperture, shutter, gps_lat, gps_lon, kind, state)"
                " VALUES ('my_photos',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'new')",
                (rel, sha, st.st_size, st.st_mtime, path.suffix.lstrip(".").lower(),
                 tree.clean_name(country or "", "") or None,
                 place or None,
                 # Album year and name out of the path. If the first part is
                 # not a year (a folder copied in by hand does not have to
                 # follow our shape), the year comes from the metadata -- and
                 # can be corrected in the workshop.
                 (year if (year or "").isdigit() and len(year or "") == 4
                  else (info["taken_at"] or "")[:4] or None),
                 (event or None),
                 info["taken_at"], info["taken_source"], info["camera"],
                 info["lens"], info["iso"], info["aperture"], info["shutter"],
                 info["gps_lat"], info["gps_lon"], info["kind"]),
            )
            photo_id = cur.lastrowid
            if av_state != av.CLEAN:
                c.execute("UPDATE photos SET state='infected', note=? WHERE id=?",
                          (f"virus scan: {av_state} — {av_detail}"[:200], photo_id))
        except _s3.IntegrityError:
            # Somebody was faster. Their row counts -- and there is no second job.
            log.info("scan: %s was just taken in by another run", rel)
            return None
    if av_state != av.CLEAN:
        log.warning("file NOT clean (%s): %s -- state=infected, no convert",
                    av_state, rel)
        return photo_id
    enqueue("convert", str(photo_id))
    return photo_id


def scan_origins(root: Path = None, limit: int = 0) -> dict:
    root = root or config.ORIGIN_DIR
    library.check_tree(root)
    conn = db.connect()
    # ⚠ EVERYTHING in one pass, not one query per file.
    #
    # An earlier version called `_backfill(rel)` for every known file, and that
    # did its own SELECT: with 1,478 photographs that is 1,478 queries, and the
    # scan ran into a timeout. With a library of 31,753 it would never have
    # finished at all.
    known_rows = {r["origin_path"]: r for r in conn.execute(
        "SELECT origin_path, id, country, place, album_year, event FROM photos "
        "WHERE origin_root='my_photos'")}
    # What somebody deliberately took off the site does not come back by itself.
    blocked_ = {r["origin_path"] for r in conn.execute(
        "SELECT origin_path FROM removed WHERE origin_root='my_photos'")}
    known = set(known_rows) | blocked_
    seen = new = skipped = filled = 0
    to_follow = []

    # ⚠ Materialise the list first -- a percentage needs a total. `rglob` gives
    # a list here, because it is sorted afterwards anyway.
    import json as _json
    import time as _time
    entries = sorted(root.rglob("*"))
    total = len(entries) or 1

    def _progress(done, current, running=True):
        db.set_state("scan_progress", _json.dumps({
            "running": running, "total": total, "done": done,
            "pct": min(100, int(done * 100 / total)),
            "current": current, "seen": seen, "new": new, "filled": filled,
            "at": _time.strftime("%Y-%m-%d %H:%M:%S")}))

    _progress(0, "")
    _last_report = 0.0

    for idx, path in enumerate(entries, 1):
    # Do not write the progress for every file -- at most once a second.
        if _time.time() - _last_report > 1.0:
            _progress(idx, str(path.parent.relative_to(root)) if path.parent != root else "")
            _last_report = _time.time()
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in config.ALLOWED_SUFFIXES:
            continue
        seen += 1
        rel = str(path.relative_to(root))
        if rel in known:
            row = known_rows.get(rel)
            if row is not None:
                sets = _missing(row, rel)
                if sets:
                    to_follow.append(sets)
                    filled += 1
            continue
        photo_id = adopt(path, rel)
        if photo_id is None:
            skipped += 1
            continue
        new += 1
        if limit and new >= limit:
            break

    if to_follow:
        # One write for everything, not one per photograph.
        with db.tx() as c:
            for columns, args in to_follow:
                c.execute(f"UPDATE photos SET {columns} WHERE id=?", args)

    _progress(total, "", running=False)
    return {"seen": seen, "new": new, "skipped": skipped,
            "filled": filled, "known": len(known)}


def _missing(row, rel: str):
    """What is missing on a known row and can be read out of the path -- or
    `None`.

    Nothing else is touched: a name somebody set by hand stays, even when the
    folder is called something different."""
    year, country, event = parts_of(Path(rel))
    will_country = tree.clean_name(country or "", "") or None
    will_place = place_from_event(event or "") or None
    sets, args = [], []
    if not row["country"] and will_country:
        sets.append("country=?"); args.append(will_country)
    if not row["place"] and will_place:
        sets.append("place=?"); args.append(will_place)
    if not row["album_year"] and (year or "").isdigit() and len(year or "") == 4:
        sets.append("album_year=?"); args.append(year)
    if not row["event"] and event:
        sets.append("event=?"); args.append(event)
    if not sets:
        return None
    return ", ".join(sets), (*args, row["id"])


def _backfill(rel: str) -> int:
    """For a row that already exists: see whether the country or the place are
    missing, and fill them in from the path. Nothing else is touched."""
    row = db.connect().execute(
        "SELECT id, country, place, album_year, event FROM photos "
        "WHERE origin_root='my_photos' AND origin_path=?", (rel,)).fetchone()
    if row is None:
        return 0
    year, country, event = parts_of(Path(rel))
    want_country = tree.clean_name(country or "", "") or None
    want_place = place_from_event(event or "") or None
    sets, args = [], []
    if not row["country"] and want_country:
        sets.append("country=?"); args.append(want_country)
    if not row["place"] and want_place:
        sets.append("place=?"); args.append(want_place)
    if not row["album_year"] and (year or "").isdigit() and len(year or "") == 4:
        sets.append("album_year=?"); args.append(year)
    if not row["event"] and event:
        sets.append("event=?"); args.append(event)
    if not sets:
        return 0
    with db.tx() as c:
        c.execute(f"UPDATE photos SET {', '.join(sets)} WHERE id=?", (*args, row["id"]))
    return 1


# ---------------------------------------------------------------------------
#  As a job -- a scan walks the whole library and can take minutes.
#  Synchronously over HTTP that produced a gateway timeout.
# ---------------------------------------------------------------------------
from .worker import handler  # noqa: E402


@handler("scan")
def _job(job) -> None:
    res = scan_origins()
    log.info("Scan (Job): %s", res)
