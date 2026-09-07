"""Editing albums: year, country, name, place.

Why this has to exist: a folder that was copied **straight into the originals
tree** does not have to follow our shape `<year>/<country>/<name>`. The scan
takes it in all the same -- and afterwards it is put right here.

Three rules, and all three are hard:

1. **Nothing is deleted.** Renaming is done by moving; if something fails
   halfway, the file is still lying in one of the two places.
2. **Both trees move together.** The originals tree and the web tree always
   get the same thing -- otherwise they drift apart and the site no longer
   finds its master.
3. **The database comes after the files.** A row pointing at a file that does
   not exist is an error you can see; a file without a row is picked up again
   by the next scan.
"""
import json
import logging
import os
import shutil
import uuid
from pathlib import Path

from . import acl, config, db, tree

log = logging.getLogger("family")


class AlbumBusy(Exception):
    """A folder could not be moved because a file inside it is open right now
    (a scan or a conversion is running). Not corruption -- simply try again."""


def _norm(year: str, country: str, event: str, place: str = ""):
    """Clean the fields, the same way the upload does."""
    year = tree.clean_name(str(year or ""), "")
    if not (year.isdigit() and len(year) == 4):
        raise ValueError(f"not a valid year: {year!r}")
    country = tree.clean_name(country or "", "")
    if not country:
        raise ValueError("the country is missing")
    # A year at the end of the name always comes off -- same rule as on upload.
    event = tree.clean_name(tree.strip_year(event or ""), "")
    if not event:
        raise ValueError("the name is missing")
    return year, country, event, tree.clean_name(place or "", "")


def _rel(year: str, country: str, event: str) -> str:
    return f"{year}/{country}/{event}"


def published_keys() -> set:
    return {r["album_key"] for r in db.connect().execute(
        "SELECT album_key FROM album_published")}


def publish(album_key: str) -> None:
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO album_published (album_key) VALUES (?)",
                  (album_key,))


def unpublish(album_key: str) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM album_published WHERE album_key=?", (album_key,))


def list_all(owner: str = None) -> list:
    """Every album, with the counts and what is on the disk.

    Counted **without** the `hidden` filter: the admin has to see an album in
    which everything is hidden as well -- otherwise they could never put it
    right again.
    """
    done_ = published_keys()
    where_ = " WHERE owner=?" if owner else ""
    args = (owner,) if owner else ()
    rows = db.connect().execute(
        "SELECT coalesce(album_year,'—') AS year, coalesce(country,'—') AS country, "
        "       coalesce(event,'—') AS event, "
        "       COUNT(*) AS n, "
        "       SUM(state='ok' AND hidden=0) AS on_site, "
        "       SUM(hidden=1) AS hidden, "
        "       SUM(state<>'ok') AS waiting, "
        "       MIN(taken_at) AS von, MAX(taken_at) AS bis, "
        "       MAX(CASE WHEN state='ok' AND hidden=0 THEN id END) AS cover "
        "FROM photos" + where_ + " GROUP BY year, country, event "
        "ORDER BY year DESC, country, event", args
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # The place is per photograph -- for the album the most common one is
        # taken, or the field would be empty whenever one photograph differs.
        pl = db.connect().execute(
            "SELECT place, COUNT(*) c FROM photos WHERE coalesce(album_year,'—')=? "
            "AND coalesce(country,'—')=? AND coalesce(event,'—')=? "
            "AND place IS NOT NULL AND place<>'' GROUP BY place ORDER BY c DESC LIMIT 1",
            (r["year"], r["country"], r["event"])).fetchone()
        d["place"] = pl["place"] if pl else ""
        rel = _rel(r["year"], r["country"], r["event"])
        d["rel"] = rel
        d["on_disk"] = (config.ORIGIN_DIR / rel).is_dir()
        d["web_on_disk"] = (config.WEB_DIR / rel).is_dir()
        d["published"] = rel in done_
        # The journey: departure + transport (for the box in the workshop)
        from . import journey
        jf = journey.for_form(r["year"], r["country"], r["event"])
        d["departure"] = jf["departure"]
        d["transport"] = jf["transport"]
        d["legs"] = jf.get("legs", [])
        d["multi"] = jf.get("multi", False)
        out.append(d)
    return out


def _move_tree(src: Path, dst: Path) -> int:
    """One folder to a new place. If the target exists, they are merged.

    Merging is needed: you fix a typo and the folder should go into the one
    that is already spelled correctly. A file already lying at the target under
    the same name is **not** overwritten -- it gets a suffix.
    """
    if not src.is_dir():
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        os.rename(src, dst)          # selwecht Dateisystem -> billeg
        return 1
    n = 0
    for item in sorted(src.iterdir()):
        goal_ = dst / item.name
        if goal_.exists():
            stem, suf = item.stem, item.suffix
            i = 2
            while (dst / f"{stem}~{i}{suf}").exists():
                i += 1
            goal_ = dst / f"{stem}~{i}{suf}"
            log.warning("album move: %s already existed -> %s", item.name, goal_.name)
        shutil.move(str(item), str(goal_))
        n += 1
    try:
        src.rmdir()
    except OSError:
        log.warning("old folder %s is not empty -- it stays", src)
    return n


def _prune(root: Path, rel: str) -> None:
    """Remove empty folders above a moved album -- and ONLY empty ones.
    `rmdir` fails by itself when there is still something inside."""
    p = root / rel
    for _ in range(3):
        p = p.parent
        # ⚠ Never out past the root -- not even if `rel` is something odd.
        if p == root or root not in p.parents or not p.is_dir():
            break
        try:
            p.rmdir()
        except OSError:
            break


def edit(year: str, country: str, event: str,
         new_year: str = None, new_country: str = None, new_event: str = None,
         new_place: str = None, _record: bool = True) -> dict:
    """Rename an album / hang it somewhere else. Returns what happened.

    `_record`: normally the step is written into the `moves` table, so that an
    undo (see `undo`) can turn it back. A reverse run from the undo itself
    sets `_record=False` -- otherwise you would build yourself an endless list."""
    ny, nc, ne, np = _norm(
        new_year if new_year is not None else year,
        new_country if new_country is not None else country,
        new_event if new_event is not None else event,
        new_place if new_place is not None else "")
    new_rel = _rel(ny, nc, ne)

    conn = db.connect()
    rows = conn.execute(
        "SELECT id, origin_path, place FROM photos WHERE coalesce(album_year,'—')=? "
        "AND coalesce(country,'—')=? AND coalesce(event,'—')=?",
        (year, country, event)).fetchall()
    ids = [r["id"] for r in rows]
    if not ids:
        raise ValueError(f"no such album: {_rel(year, country, event)}")
    # ⚠ Hold on to the old place BEFORE it is overwritten -- otherwise an undo
    # cannot put it back (it is not part of the album key).
    old_place = next((r["place"] for r in rows if r["place"]), "") or ""

    # ⚠ The path on the disk comes from the PHOTOGRAPHS, not from the album
    # fields.
    #
    # The two can drift apart: a folder copied straight into the originals tree
    # does not have to follow our shape `<year>/<country>/<name>` -- something
    # like `2025/Wildlife Park` has no country level at all. The album fields
    # then say "2025/—/Wildlife Park", and that path does not exist on the disk.
    #
    # An earlier version moved using that computed name: `_move_tree` found
    # nothing, quietly returned `0` -- and the database was updated anyway. The
    # result: the rows said `2025/Luxembourg/…`, the files lay under
    # `2025/Wildlife Park`, and the site only still found its masters because
    # `web_name` carries the whole path. That is exactly what the two dots in
    # the workshop were showing.
    folder = {str(Path(r["origin_path"]).parent) for r in rows}
    if len(folder) > 1:
        raise ValueError("these photographs sit in more than one folder — "
                         "run a sync first")
    alt_rel = folder.pop()

    shifted = {}   # per tree, how many things were moved
    if new_rel != alt_rel:
        # ⚠ The files first, and both trees. If the second one fails, the first
        # is moved back -- otherwise they stand apart.
        #
        # ⚠ An OSError here is usually NOT a permissions error but a file open
        # IN PLACE: if a scan or a conversion is reading a photograph out of
        # that folder, the network share refuses to move the folder (EACCES).
        # Then NOTHING is moved halfway (the rename is atomic), and a clear
        # message comes back instead of a 500 -- and one run later it works.
        try:
            shifted["my_photos"] = _move_tree(config.ORIGIN_DIR / alt_rel,
                                                config.ORIGIN_DIR / new_rel)
        except OSError as exc:
            raise AlbumBusy(
                "could not move the folder — a photo in it is being scanned or "
                "converted right now. Try again in a moment.") from exc
        try:
            shifted["family_website"] = _move_tree(config.WEB_DIR / alt_rel,
                                                     config.WEB_DIR / new_rel)
        except OSError as exc:
            # The originals tree has already moved -> back, or they drift apart.
            _move_tree(config.ORIGIN_DIR / new_rel, config.ORIGIN_DIR / alt_rel)
            raise AlbumBusy(
                "could not move the folder — a photo in it is being scanned or "
                "converted right now. Try again in a moment.") from exc
        _prune(config.ORIGIN_DIR, alt_rel)
        _prune(config.WEB_DIR, alt_rel)

    with db.tx() as c:
        if new_rel != alt_rel:
            # ⚠ Replace ONLY at the start, not everywhere.
            #
            # `replace()` hits EVERY occurrence. If a folder sits at the top of
            # the tree (`Park/Park.jpg` -- exactly the case this mask is here
            # for), the old path ALSO stands inside the file name, and
            # `Park/Park.jpg` becomes `2025/LU/Park/2025/LU/Park.jpg`. The file
            # then lies somewhere other than the row says, the photograph turns
            # into a 404, and the next sync takes the master for lost and
            # throws it away after 30 days.
            #
            # No LIKE: `_` and `%` are wildcards there, and `clean_name` lets
            # `_` through. `substr` compares characters, not patterns.
            for col in ("origin_path", "web_name", "master_source_path"):
                c.execute(
                    f"UPDATE photos SET {col} = ? || substr({col}, ?) "
                    f"WHERE id IN ({','.join('?' * len(ids))}) "
                    f"  AND substr({col}, 1, ?) = ?",
                    (new_rel, len(alt_rel) + 1, *ids, len(alt_rel) + 1,
                     alt_rel + "/"))
            # The block list has to come along: otherwise a photograph that was
            # taken off the site comes back under the new path on the next scan.
            c.execute("UPDATE removed SET origin_path = ? || substr(origin_path, ?) "
                      "WHERE origin_root='my_photos' AND substr(origin_path, 1, ?) = ?",
                      (new_rel, len(alt_rel) + 1, len(alt_rel) + 1, alt_rel + "/"))
            # ⚠ With the slash: without it `2019/Camp` also hits `2019/Camp2`.
            c.execute("UPDATE folders SET path = ? || substr(path, ?) "
                      "WHERE substr(path, 1, ?) = ?",
                      (new_rel, len(alt_rel) + 1, len(alt_rel) + 1, alt_rel + "/"))
    # ⚠ The viewing permissions hang off the ALBUM KEY (year/country/name), not
    # off the path on the disk -- and those two are not always the same. If the
    # list stays on the old key, the album is suddenly open to everybody after
    # a rename, and nobody would notice.
    alt_key = _rel(year, country, event)
    if alt_key != new_rel:
        acl.rename_album(alt_key, new_rel)
        unpublish(alt_key)
    # ⚠ Saving means "I went through this". Always -- even when nothing
    # changed: a click on "save" is exactly the statement this row holds on to.
    publish(new_rel)
    with db.tx() as c:
        sets = ["album_year=?", "country=?", "event=?"]
        args = [ny, nc, ne]
        if np:
            sets.append("place=?"); args.append(np)
        c.execute(f"UPDATE photos SET {', '.join(sets)} "
                  f"WHERE id IN ({','.join('?' * len(ids))})", (*args, *ids))

    # ⚠ The place has just been set or changed -- so it belongs on the map. As
    # a job, because Nominatim asks for a second between requests.
    from .worker import enqueue
    enqueue("geocode", "")

    # ⚠ Recorded for the undo -- but only when something really did change (the
    # path or the album fields). A bare "save" with no change needs no undo.
    batch = None
    changed = (new_rel != alt_rel) or (alt_key != new_rel) or ((np or old_place) and np != old_place)
    if _record and changed:
        batch = uuid.uuid4().hex[:12]
        meta = json.dumps({
            "old": {"year": year, "country": country, "event": event, "place": old_place},
            "new": {"year": ny, "country": nc, "event": ne, "place": np or old_place},
            "moved": shifted, "photos": len(ids)})
        with db.tx() as c:
            c.execute("INSERT INTO moves (batch_id, src, dst, meta) VALUES (?,?,?,?)",
                      (batch, alt_key, new_rel, meta))

    log.info("Album %s -> %s (%d Fotoen)", alt_rel, new_rel, len(ids))
    return {"from": alt_rel, "to": new_rel, "photos": len(ids),
            "place": np, "moved": shifted, "batch": batch,
            # Stoung den Album virdru schif (Wee um Disk ≠ Album-Felder),
            # steet en elo riicht -- dat soll ee gesinn.
            "was_crooked": alt_rel != alt_key,
            "url": f"/y/{ny}/{nc}/{ne}"}


def undo(batch: str) -> dict:
    """Turn an album rebuild back: `edit` in the other direction.

    Because `edit` carries out the whole step (both trees, the database, the
    viewing permissions, the album fields), its own reverse run is the undo --
    with old and new swapped and the old place put back. Passes
    `_record=False`, or the undo itself would end up in the list again."""
    row = db.connect().execute(
        "SELECT * FROM moves WHERE batch_id=? AND undone_at IS NULL", (batch,)).fetchone()
    if not row:
        raise ValueError("nothing to undo (already undone or unknown)")
    m = json.loads(row["meta"] or "{}")
    old, new = m.get("old", {}), m.get("new", {})
    res = edit(new.get("year", ""), new.get("country", ""), new.get("event", ""),
               new_year=old.get("year", ""), new_country=old.get("country", ""),
               new_event=old.get("event", ""), new_place=old.get("place", ""),
               _record=False)
    with db.tx() as c:
        c.execute("UPDATE moves SET undone_at=datetime('now') WHERE batch_id=?", (batch,))
    log.info("Undo %s: %s -> %s", batch, row["dst"], row["src"])
    return {"undone": batch, "from": res["from"], "to": res["to"],
            "photos": res["photos"], "url": res["url"]}


def tags_of_albums() -> dict:
    """What an album already carries: people and tags.

    Counted by how MANY photographs -- "Max (63/63)" says something different
    from "Max (2/63)". One query for all albums, not one per row.
    """
    out = {}
    for kind_, tab, link, column in (("people", "people", "photo_people", "person_id"),
                                   ("tags", "tags", "photo_tags", "tag_id")):
        for r in db.connect().execute(
                f"SELECT coalesce(p.album_year,'—')||'/'||coalesce(p.country,'—')"
                f"       ||'/'||coalesce(p.event,'—') AS k, t.name, COUNT(*) AS n "
                f"FROM {link} l JOIN {tab} t ON t.id=l.{column} "
                f"JOIN photos p ON p.id=l.photo_id "
                f"GROUP BY k, t.name ORDER BY n DESC, t.name"):
            out.setdefault(r["k"], {}).setdefault(kind_, []).append(
                {"name": r["name"], "n": r["n"]})
    return out
