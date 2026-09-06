"""Collections -- saved selections, not folders.

⚠ **The word.** "Album" on this site means what comes out of a folder
(`2016 Fragas de Sao Simao`), and the workshop under `/admin/albums` edits
exactly those. Two different things under one name would be a mistake you feel
every day. So:

    album       what is on disk.  One folder; a photograph is in exactly one.
    collection  a selection.      Virtual; a photograph can be in ten.

The database table is still called `albums` -- it was laid out that way from
the start, and renaming it buys nothing but risk.

A collection is what gets shared through a link.
"""
import logging
import re
import unicodedata

from . import acl, db

log = logging.getLogger("family")

_SAFE = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    s = unicodedata.normalize("NFKD", (title or "").strip().lower())
    s = s.encode("ascii", "ignore").decode()
    s = _SAFE.sub("-", s).strip("-")
    return s[:60] or "collection"


def _free_slug(title: str, except_: int = None) -> str:
    basis = slugify(title)
    conn = db.connect()
    slug, i = basis, 2
    while True:
        row = conn.execute("SELECT id FROM albums WHERE slug=?", (slug,)).fetchone()
        if row is None or row["id"] == except_:
            return slug
        slug = f"{basis}-{i}"
        i += 1


def create(title: str, photo_ids=None, owner: str = None) -> dict:
    title = unicodedata.normalize("NFC", (title or "").strip())[:120]
    if not title:
        raise ValueError("the title is missing")
    slug = _free_slug(title)
    with db.tx() as c:
        ident = c.execute("INSERT INTO albums (slug, title, owner) VALUES (?,?,?)",
                          (slug, title, owner)).lastrowid
    if photo_ids:
        add(ident, photo_ids)
    return one(ident)


def rename(ident: int, title: str) -> dict:
    title = unicodedata.normalize("NFC", (title or "").strip())[:120]
    if not title:
        raise ValueError("the title is missing")
    # ⚠ The slug stays! It is the address, and an address that changes when
    # something is renamed breaks every link already sent out.
    with db.tx() as c:
        c.execute("UPDATE albums SET title=? WHERE id=?", (title, ident))
    return one(ident)


def remove(ident: int) -> dict:
    """Remove the collection. **The photographs stay** -- they were never in
    it, they were only listed."""
    with db.tx() as c:
        n = c.execute("SELECT COUNT(*) FROM album_photos WHERE album_id=?",
                      (ident,)).fetchone()[0]
        c.execute("DELETE FROM albums WHERE id=?", (ident,))   # album_photos: CASCADE
    return {"removed": ident, "held": n,
            "note": "the photographs themselves are untouched"}


def add(ident: int, photo_ids) -> dict:
    ids = [int(i) for i in (photo_ids or [])]
    if not ids:
        raise ValueError("no photographs selected")
    conn = db.connect()
    if conn.execute("SELECT 1 FROM albums WHERE id=?", (ident,)).fetchone() is None:
        raise ValueError("no such collection")
    start = conn.execute(
        "SELECT coalesce(MAX(sort_index), -1) + 1 FROM album_photos WHERE album_id=?",
        (ident,)).fetchone()[0]
    with db.tx() as c:
        c.executemany(
            "INSERT OR IGNORE INTO album_photos (album_id, photo_id, sort_index) "
            "VALUES (?,?,?)", [(ident, p, start + k) for k, p in enumerate(ids)])
        # The first photograph becomes the cover, if there is none yet.
        c.execute("UPDATE albums SET cover_photo_id=? WHERE id=? "
                  "AND cover_photo_id IS NULL", (ids[0], ident))
    return one(ident)


def drop(ident: int, photo_ids) -> dict:
    ids = [int(i) for i in (photo_ids or [])]
    if not ids:
        raise ValueError("no photographs selected")
    q = ",".join("?" * len(ids))
    with db.tx() as c:
        c.execute(f"DELETE FROM album_photos WHERE album_id=? AND photo_id IN ({q})",
                  (ident, *ids))
        # If the cover was among them, the collection takes the next one --
        # otherwise it shows a picture that is no longer in it.
        c.execute("UPDATE albums SET cover_photo_id=("
                  "  SELECT photo_id FROM album_photos WHERE album_id=? "
                  "  ORDER BY sort_index LIMIT 1) "
                  "WHERE id=? AND cover_photo_id NOT IN ("
                  "  SELECT photo_id FROM album_photos WHERE album_id=?)",
                  (ident, ident, ident))
    return one(ident)


def set_cover(ident: int, photo_id: int) -> dict:
    conn = db.connect()
    if conn.execute("SELECT 1 FROM album_photos WHERE album_id=? AND photo_id=?",
                    (ident, photo_id)).fetchone() is None:
        raise ValueError("that photograph is not in this collection")
    with db.tx() as c:
        c.execute("UPDATE albums SET cover_photo_id=? WHERE id=?", (photo_id, ident))
    return one(ident)


def one(ident: int) -> dict:
    row = db.connect().execute(
        "SELECT a.*, (SELECT COUNT(*) FROM album_photos p WHERE p.album_id=a.id) AS n, "
        "       (SELECT rev FROM photos WHERE id=a.cover_photo_id) AS cover_rev "
        "FROM albums a WHERE a.id=?", (ident,)).fetchone()
    if row is None:
        raise ValueError("no such collection")
    return dict(row)


def by_slug(slug: str):
    row = db.connect().execute("SELECT id FROM albums WHERE slug=?", (slug,)).fetchone()
    return one(row["id"]) if row else None


def all_of(owner: str = None) -> list:
    where_ = " WHERE a.owner=?" if owner else ""
    args = (owner,) if owner else ()
    return [dict(r) for r in db.connect().execute(
        "SELECT a.*, (SELECT COUNT(*) FROM album_photos p WHERE p.album_id=a.id) AS n, "
        "       (SELECT rev FROM photos WHERE id=a.cover_photo_id) AS cover_rev "
        "FROM albums a" + where_ + " ORDER BY a.created_at DESC, a.id DESC", args)]


def owned_by(ident_id: int) -> str:
    row = db.connect().execute("SELECT owner FROM albums WHERE id=?", (ident_id,)).fetchone()
    return row["owner"] if row else None


def photos_of(ident: int, include_hidden: bool = False, viewer=None) -> list:
    """The photographs in the collection's own order -- not by date.

    That order is somebody's choice: they arranged it that way.

    ⚠ A collection is **not a back door**. If a photograph from an album that
    is closed to this viewer sits in a collection, they do not see it here
    either.
    """
    where_ = "" if include_hidden else " AND ph.state='ok' AND ph.hidden=0"
    clause, acl_args = acl.sql_clause(viewer, "ph.")
    if clause:
        where_ += " AND " + clause
    return [dict(r) for r in db.connect().execute(
        "SELECT ph.id, ph.web_name, ph.taken_at, ph.width, ph.height, ph.country, "
        "       ph.place, ph.camera, ph.title, ph.kind, ph.rating, ph.duration_s, "
        "       ph.hidden, ph.rev, ap.sort_index "
        "FROM album_photos ap JOIN photos ph ON ph.id=ap.photo_id "
        f"WHERE ap.album_id=?{where_} ORDER BY ap.sort_index, ph.id",
        (ident, *acl_args))]


def of_photos(photo_ids) -> dict:
    """`{photo_id: [titles]}` -- to show what a photograph is already in."""
    ids = [int(i) for i in (photo_ids or [])]
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    out = {i: [] for i in ids}
    for r in db.connect().execute(
            f"SELECT ap.photo_id, a.title FROM album_photos ap "
            f"JOIN albums a ON a.id=ap.album_id WHERE ap.photo_id IN ({q}) "
            f"ORDER BY a.title", ids):
        out[r["photo_id"]].append(r["title"])
    return out
