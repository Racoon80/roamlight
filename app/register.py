"""The register: turning photographs, hiding them, taking them off the site.

⚠ **"Take off the site" never deletes an original.** Not the file in your
  library and nothing in a backup. It means *not on the website* -- which is a
  different thing from *gone*. Somebody who really wants to be rid of a file
  does that in a file manager; the site cannot, and that is on purpose.

What the site may touch is its **own** tree: the master in the served library
and the web sizes on this machine. Those can always be computed again -- the
next scan picks the photograph back up if you want it back.
"""
import logging
import shutil
from pathlib import Path

import pyvips

from . import config, convert, db, images

log = logging.getLogger("family")


def _rows(ids):
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    return db.connect().execute(
        f"SELECT * FROM photos WHERE id IN ({q})", list(ids)).fetchall()


def set_cover(photo_id: int) -> dict:
    """Make a photograph the cover of ITS album.

    There is exactly one cover per album, so is_cover is cleared for every
    photograph in the same album (year/country/name) and set on this one alone.
    Nothing is moved and nothing is deleted -- it is only a mark."""
    con = db.connect()
    row = con.execute(
        "SELECT coalesce(album_year,'—') AS y, coalesce(country,'—') AS c, "
        "coalesce(event,'—') AS e FROM photos WHERE id=?", (int(photo_id),)).fetchone()
    if row is None:
        raise ValueError("no such photograph")
    with db.tx() as c:
        c.execute("UPDATE photos SET is_cover=0 WHERE coalesce(album_year,'—')=? "
                  "AND coalesce(country,'—')=? AND coalesce(event,'—')=?",
                  (row["y"], row["c"], row["e"]))
        c.execute("UPDATE photos SET is_cover=1 WHERE id=?", (int(photo_id),))
    log.info("cover set: photo %s for album %s/%s/%s",
             photo_id, row["y"], row["c"], row["e"])
    return {"cover": int(photo_id)}


def set_hidden(ids, hidden: bool) -> dict:
    """Hiding means: stays in the database, disappears from the site."""
    if not ids:
        return {"changed": 0}
    q = ",".join("?" * len(ids))
    with db.tx() as c:
        c.execute(f"UPDATE photos SET hidden=? WHERE id IN ({q})",
                  [1 if hidden else 0, *ids])
    return {"changed": len(ids), "hidden": hidden}


def rotate(ids, quarter_turns: int) -> dict:
    """Turn the master and rebuild the web sizes.

    ONLY the master is turned -- the original stays as it came out of the
    camera. Which is also why a rotation can always be undone."""
    done, failed = 0, []
    for row in _rows(ids):
        master = config.WEB_DIR / (row["web_name"] or "")
        if not row["web_name"] or not master.is_file():
            failed.append({"id": row["id"], "error": "kee Master"})
            continue
        try:
            img = pyvips.Image.new_from_file(str(master))
            img = img.rot({1: "d90", 2: "d180", 3: "d270"}[quarter_turns % 4]) \
                if quarter_turns % 4 else img
            tmp = master.with_suffix(".rot.jpg")
            img.jpegsave(str(tmp), Q=config.MASTER_QUALITY, strip=True,
                         optimize_coding=True)
            # Carry the metadata over from the old master, and set the
            # orientation to 1: the pixels are already turned.
            import subprocess
            subprocess.run(["exiftool", "-overwrite_original", "-q", "-m",
                            "-TagsFromFile", str(master), "-all:all",
                            "-Orientation#=1", "--", str(tmp)],
                           capture_output=True, timeout=120, check=False)
            tmp.replace(master)
            out = convert.derivative_dir(row["id"])
            shutil.rmtree(out, ignore_errors=True)
            images.build_derivatives(master, out, config.DERIVATIVE_WIDTHS)
            with db.tx() as c:
                # Raise `rev`: the addresses of the web sizes carry it as ?v=,
                # so after a rotation the browser gets a NEW address. Without
                # it, it would show the old picture for a year.
                c.execute("UPDATE photos SET width=?, height=?, rev=rev+1 WHERE id=?",
                          (img.width, img.height, row["id"]))
            done += 1
        except Exception as exc:                                # noqa: BLE001
            log.warning("rotate %s: %s", row["id"], exc)
            failed.append({"id": row["id"], "error": str(exc)})
    return {"rotated": done, "failed": failed}


def remove(ids) -> dict:
    """Take off the site: master and web sizes gone, database row gone.

    The original is **not** touched. A scan would pick the photograph up again
    -- so its sha256 is kept in `removed`, and it does not come back by
    itself."""
    gone, failed = 0, []
    for row in _rows(ids):
        try:
            # ⚠ The database FIRST, then the files. The other way round was a
            # bug: a foreign key blocked the row from being deleted, and the
            # photograph was left standing there without its master. If the
            # transaction fails, nothing is gone yet.
            with db.tx() as c:
                c.execute("INSERT OR IGNORE INTO removed (origin_root, origin_path, sha256) "
                          "VALUES (?,?,?)",
                          (row["origin_root"], row["origin_path"], row["origin_sha256"]))
                # What points at the photograph and should not drag it along:
                # the upload history stays, it did happen after all.
                c.execute("UPDATE upload_files SET photo_id=NULL WHERE photo_id=?", (row["id"],))
                # ⚠ `dup_of` points at `photos` as well -- which was forgotten.
                # A photograph that another one had once been recognised as a
                # duplicate of could not be removed: "FOREIGN KEY constraint
                # failed".
                c.execute("UPDATE upload_files SET dup_of=NULL WHERE dup_of=?", (row["id"],))
                c.execute("UPDATE share_uploads SET photo_id=NULL WHERE photo_id=?", (row["id"],))
                c.execute("UPDATE albums SET cover_photo_id=NULL WHERE cover_photo_id=?",
                          (row["id"],))
                c.execute("DELETE FROM jobs WHERE kind='convert' AND payload=?",
                          (str(row["id"]),))
                c.execute("DELETE FROM photos WHERE id=?", (row["id"],))
            if row["web_name"]:
                (config.WEB_DIR / row["web_name"]).unlink(missing_ok=True)
            shutil.rmtree(convert.derivative_dir(row["id"]), ignore_errors=True)
            gone += 1
        except Exception as exc:                                # noqa: BLE001
            log.warning("remove %s: %s", row["id"], exc)
            failed.append({"id": row["id"], "error": str(exc)})
    return {"removed": gone, "failed": failed,
            "note": "the originals are untouched"}


def restore_all() -> dict:
    """Empty the block list -- the next scan picks everything up again."""
    with db.tx() as c:
        n = c.execute("SELECT COUNT(*) FROM removed").fetchone()[0]
        c.execute("DELETE FROM removed")
    return {"forgotten": n}
