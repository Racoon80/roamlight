"""Uploading: batch -> files -> suggestion -> confirmation -> filed away.

    incoming/<batch>/<n>.part          uploaded, in chunks of 5 MB
        |  sha256, exiftool, duplicate check
        v
    a suggestion: year - country - name - place
        |  the person uploading confirms or changes it       (`begin`)
        v
    the queue files it away                                  (`file_away`)
        |  originals/<year>/<country>/<name>/, sha256 read back
        |  and only THEN is the copy in incoming removed
        v
    ask how far it has got, as often as you like             (`progress`)

⚠ The last step is a QUEUE and not the request, and that is the one thing in
  here worth knowing. Filing 247 photographs takes about twelve minutes -- a
  virus scan, a copy, a read-back and a database row, each -- and no HTTP
  request survives that: nginx gives up after sixty seconds unless told
  otherwise, Cloudflare after a hundred and there is no telling it otherwise, a
  phone goes to sleep sooner than both. On 20.09.2026 a batch of 247 was filed
  perfectly and the person who sent it was shown a 504, because by the time
  there was an answer there was nobody left to give it to.

  So: `begin` writes down what was confirmed and hands the work over, and what
  happened to each file is written into `upload_files` as it goes -- where it
  can still be read tomorrow, by whoever asks.
"""
import logging
import re
import secrets
import shutil
from pathlib import Path

from . import av, config, db, library, meta, tree
from .worker import enqueue, handler

log = logging.getLogger("family")


_TOKEN = re.compile(r"^[0-9a-f]{16}$")


def _batch_dir(batch: str) -> Path:
    # The batch id comes out of the URL. Without this check `..` would be one
    # folder up -- not exploitable as it stands, but that would be luck rather
    # than design.
    if not _TOKEN.match(batch or ""):
        raise ValueError("unknown batch")
    return config.INCOMING_DIR / batch


def create_batch() -> str:
    batch = secrets.token_hex(8)
    _batch_dir(batch).mkdir(parents=True, exist_ok=True)
    with db.tx() as conn:
        conn.execute("INSERT INTO upload_batches (token) VALUES (?)", (batch,))
    return batch


def add_file(batch: str, original_name: str, size: int) -> dict:
    # ⚠ `filing_at IS NULL` too: once the filing has started, the list of files
    #   is what the worker is walking through. A file added now would either be
    #   missed or picked up half-written.
    row = db.connect().execute(
        "SELECT id FROM upload_batches WHERE token=? AND committed_at IS NULL "
        "AND filing_at IS NULL", (batch,)
    ).fetchone()
    if row is None:
        raise ValueError("unknown batch")
    if size > config.MAX_UPLOAD_BYTES:
        raise ValueError(f"too large ({size} bytes, limit {config.MAX_UPLOAD_BYTES})")
    ext = library.safe_ext(original_name)
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO upload_files (batch_id, original_name, ext, size) VALUES (?,?,?,?)",
            (row["id"], (original_name or "")[:255], ext, size),
        )
        fid = cur.lastrowid
    (_batch_dir(batch) / f"{fid}.part").touch()
    return {"file_id": fid, "ext": ext}


def append_chunk(batch: str, file_id: int, offset: int, data: bytes) -> int:
    part = _batch_dir(batch) / f"{file_id}.part"
    if not part.exists():
        raise ValueError("unknown file")
    have = part.stat().st_size
    if offset != have:
        # Resumable: the client should carry on where we are.
        raise ValueError(f"wrong offset: we have {have}, you sent {offset}")
    if have + len(data) > config.MAX_UPLOAD_BYTES:
        raise ValueError("too large")
    with open(part, "ab") as fh:
        fh.write(data)
    return part.stat().st_size


def finish_file(batch: str, file_id: int) -> dict:
    """File finished: check it, hash it, read the metadata, look for duplicates."""
    part = _batch_dir(batch) / f"{file_id}.part"
    row = db.connect().execute("SELECT * FROM upload_files WHERE id=?", (file_id,)).fetchone()
    if row is None or not part.exists():
        raise ValueError("unknown file")

    named = part.with_name(f"{file_id}.{row['ext']}")
    part.rename(named)                       # still our own staging area
    ok, why = meta.looks_like_media(named)
    if not ok:
        named.unlink(missing_ok=True)
        with db.tx() as conn:
            conn.execute("UPDATE upload_files SET state='rejected', note=? WHERE id=?",
                         (why, file_id))
        return {"file_id": file_id, "state": "rejected", "note": why}

    sha = library.sha256_of(named)
    info = meta.read(named)
    dup = db.connect().execute(
        "SELECT id, origin_path FROM photos WHERE origin_sha256=?", (sha,)
    ).fetchone()
    with db.tx() as conn:
        conn.execute(
            "UPDATE upload_files SET state='ready', sha256=?, size=?, taken_at=?, "
            "taken_source=?, camera=?, lens=?, gps_lat=?, gps_lon=?, kind=?, "
            "dup_of=?, note=NULL WHERE id=?",
            (sha, named.stat().st_size, info["taken_at"], info["taken_source"],
             info["camera"], info["lens"], info["gps_lat"], info["gps_lon"],
             info["kind"], dup["id"] if dup else None, file_id),
        )
    return {
        "file_id": file_id, "state": "ready", "sha256": sha,
        "taken_at": info["taken_at"], "taken_source": info["taken_source"],
        "camera": info["camera"], "kind": info["kind"],
        "duplicate_of": dup["origin_path"] if dup else None,
    }


def proposal(batch: str) -> dict:
    """What the site thinks -- and always labelled as a suggestion."""
    rows = db.connect().execute(
        "SELECT f.* FROM upload_files f JOIN upload_batches b ON b.id=f.batch_id "
        "WHERE b.token=? AND f.state='ready' ORDER BY f.taken_at, f.id", (batch,)
    ).fetchall()
    if not rows:
        return {"files": 0, "proposal": None}
    dates = [r["taken_at"] for r in rows if r["taken_at"]]
    guessed = any(r["taken_source"] == "file" for r in rows)
    p = tree.propose(dates[0] if dates else "")
    return {
        "files": len(rows),
        "bytes": sum(r["size"] or 0 for r in rows),
        "from": dates[0] if dates else None,
        "to": dates[-1] if dates else None,
        "date_guessed": guessed,
        "duplicates": sum(1 for r in rows if r["dup_of"]),
        "proposal": p,
        "known": tree.overview(),
    }


# --------------------------------------------------------------------------
#  Filing away: ASK for it, DO it, ask HOW FAR it has got.
#
#  ⚠ These three used to be one single request, and that was a bug waiting for
#    a big upload to find it. Filing 247 photographs takes about twelve
#    minutes -- a virus scan, a copy, a sha256 read back and a database row,
#    each, one after the other -- and nothing survives twelve minutes of HTTP:
#    nginx gives up after sixty seconds unless told otherwise, Cloudflare after
#    a hundred whatever anybody configures, a phone goes into a pocket sooner
#    than either. On 20.09.2026 all 247 photographs were filed perfectly and
#    the person who sent them was shown an error, because the answer had
#    nowhere left to arrive.
#
#    So the request only writes down what was confirmed and hands the work to
#    the queue. The work then takes as long as it takes, and the client asks
#    how far it has got. A commit that is repeated does NOT file anything
#    twice -- `file_away` only ever looks at files that are still `ready`.
# --------------------------------------------------------------------------


def _mark(file_id: int, state: str, note: str = None) -> None:
    """Write down how one file of a batch ended up.

    ⚠ This is why the reason survives. It used to live only in the answer to
      the commit request -- and when that answer was lost (a proxy giving up,
      a phone asleep), nobody could ever find out why a photograph had not
      arrived.
    """
    with db.tx() as conn:
        conn.execute("UPDATE upload_files SET state=?, note=? WHERE id=?",
                     (state, note, file_id))


def begin(batch: str, year: str, country: str, event: str = "", place: str = "",
          include_duplicates: bool = False, owner: str = None,
          keep_original: bool = True) -> dict:
    """The four fields are confirmed. Returns AT ONCE; the filing runs in the queue.

    The FOLDER is the name: a wedding is called "Anna's wedding" and not the name
    of the village. The place is something else and stays as metadata (for the
    map). With no name the place is used instead, and the other way round.
    """
    b = db.connect().execute(
        "SELECT * FROM upload_batches WHERE token=? AND committed_at IS NULL", (batch,)
    ).fetchone()
    if b is None:
        raise ValueError("unknown batch (or already finished)")
    # ⚠ Asked for twice -- a phone that lost the answer and sent it again. That
    #   is not an error and it must not start a second run: say where the first
    #   one has got to.
    if b["filing_at"]:
        return progress(batch)

    event = (event or "").strip() or (place or "").strip()
    place = (place or "").strip() or event
    folder = tree.target_dir(year, country, event)      # ValueError if it is not a path
    n = db.connect().execute(
        "SELECT COUNT(*) FROM upload_files WHERE batch_id=? AND state='ready'",
        (b["id"],)).fetchone()[0]
    with db.tx() as conn:
        conn.execute(
            "UPDATE upload_batches SET year=?, country=?, event=?, place=?, owner=?, "
            "keep_original=?, include_duplicates=?, filing_at=datetime('now') WHERE id=?",
            (year, country, event, place, owner,
             1 if keep_original else 0, 1 if include_duplicates else 0, b["id"]))
    enqueue("file-batch", batch)
    log.info("upload %s: %d file(s) handed to the queue for %s/%s/%s",
             batch, n, year, country, folder.name)
    # ⚠ `stored`, `skipped` and `failed` go out empty rather than missing: a
    #   client that reads them without looking gets an empty list, not a crash.
    return {"state": "working", "batch": batch, "files": n,
            "folder": str(folder), "web_folder": str(tree.web_dir(year, country, event)),
            "event": folder.name, "year": year, "country": country,
            "stored": [], "skipped": [], "failed": [], "incoming_left": n}


@handler("file-batch")
def _file_batch_job(row) -> None:
    """The queue's side of it. ⚠ A job that breaks comes back (see worker.py),
    and the second run carries on where the first stopped.

    ⚠ A batch that is already finished is NOT an error here. The service can
      stop between the last file and the line that marks the job done, and then
      the job runs once more -- with nothing left to do. Raising there would
      put a red `error` on the queue for a batch that went perfectly."""
    token = row["payload"]
    done = db.connect().execute(
        "SELECT 1 FROM upload_batches WHERE token=? AND committed_at IS NOT NULL",
        (token,)).fetchone()
    if done:
        return
    file_away(token)


def progress(batch: str) -> dict:
    """How far the filing has got. Safe to ask as often as you like."""
    b = db.connect().execute(
        "SELECT * FROM upload_batches WHERE token=?", (batch,)).fetchone()
    if b is None:
        raise ValueError("unknown batch")
    rows = db.connect().execute(
        "SELECT id, state, original_name, note, photo_id FROM upload_files "
        "WHERE batch_id=? ORDER BY id", (b["id"],)).fetchall()
    # ⚠ `file_id` goes out with every one of them. That is the number the
    #   client gave its row on the screen when the file went up -- `photo_id`
    #   only exists once the photograph has been filed, and a row that FAILED
    #   never gets one. Without this the page could not say which line went
    #   wrong.
    stored = [{"file_id": r["id"], "file": r["original_name"],
               "photo_id": r["photo_id"]}
              for r in rows if r["state"] == "stored"]
    skipped = [{"file_id": r["id"], "file": r["original_name"],
                "reason": r["note"] or "Duplikat"}
               for r in rows if r["state"] == "skipped"]
    failed = [{"file_id": r["id"], "file": r["original_name"],
               "error": r["note"] or "unknown"}
              for r in rows if r["state"] == "failed"]
    waiting = sum(1 for r in rows if r["state"] == "ready")
    folder = web_folder = ""
    if b["year"] and b["country"] and b["event"]:
        try:
            folder = str(tree.target_dir(b["year"], b["country"], b["event"]))
            web_folder = str(tree.web_dir(b["year"], b["country"], b["event"]))
        except ValueError:
            pass
    return {
        # ⚠ `done` is `committed_at`, and nothing else. Counting the files
        #   would call it finished in the gap between the last one and the
        #   last line of `file_away`.
        "state": ("done" if b["committed_at"] else
                  "working" if b["filing_at"] else "waiting"),
        "batch": batch,
        "total": len(stored) + len(skipped) + len(failed) + waiting,
        "waiting": waiting,
        "stored": stored, "skipped": skipped, "failed": failed,
        "folder": folder, "web_folder": web_folder,
        "year": b["year"], "country": b["country"], "event": b["event"],
        "incoming_left": waiting,
    }


def file_away(batch: str) -> dict:
    """Write the batch into the library. Runs in the queue, never in a request.

    Per file: write into the originals tree, read it back, compare the sha256,
    create the row in `photos`, and ONLY THEN remove the copy in incoming. If
    anything breaks, the upload stays where it is -- always.
    """
    b = db.connect().execute(
        "SELECT * FROM upload_batches WHERE token=? AND committed_at IS NULL", (batch,)
    ).fetchone()
    if b is None:
        raise ValueError("unknown batch (or already finished)")

    year, country = b["year"], b["country"]
    event, place = b["event"] or "", b["place"] or ""
    owner = b["owner"]
    keep_original = bool(b["keep_original"])
    include_duplicates = bool(b["include_duplicates"])
    folder = tree.target_dir(year, country, event)
    rows = db.connect().execute(
        "SELECT * FROM upload_files WHERE batch_id=? AND state='ready' ORDER BY taken_at, id",
        (b["id"],)
    ).fetchall()

    stored, skipped, failed = [], [], []
    # ⚠ NOT 0. The file name is built out of the capture date and this number,
    #   and this job can run a second time (it broke, the service restarted).
    #   Starting at zero again would build a name that is already on disk, and
    #   `store_original` refuses to overwrite -- so the second run would report
    #   a collision for work the first run did correctly.
    seq = db.connect().execute(
        "SELECT COUNT(*) FROM upload_files WHERE batch_id=? AND state='stored'",
        (b["id"],)).fetchone()[0]
    for r in rows:
        if r["dup_of"] and not include_duplicates:
            skipped.append({"file": r["original_name"], "reason": "Duplikat"})
            _mark(r["id"], "skipped", "Duplikat")
            continue
        seq += 1
        src = _batch_dir(batch) / f"{r['id']}.{r['ext']}"
        name = library.build_name(r["taken_at"], seq, r["ext"])
        # ⚠ Virus scan BEFORE the file reaches the library. An infected file is
        # never written into the originals tree -- it stays in incoming and is
        # reported. That holds for your own uploads too: a stick can carry
        # something you did not put there.
        av_state, av_detail = av.scan(src)
        if av_state != av.CLEAN:
            why = ("virus scan: infected" if av_state == av.INFECTED
                   else "virus scan could not run")
            failed.append({"file": r["original_name"], "error": why})
            _mark(r["id"], "failed", why)
            log.warning("upload refused (%s): %s -- %s",
                        av_state, av_detail, r["original_name"])
            continue
        web_rel = f"{year}/{tree.clean_name(country)}/{folder.name}/{name}"
        if keep_original:
            # --- administrator: the original goes into the library ---
            try:
                res = library.store_original(src, folder, name, expect_sha=r["sha256"])
            except Exception as exc:                   # noqa: BLE001 -- reported
                failed.append({"file": r["original_name"], "error": str(exc)})
                _mark(r["id"], "failed", str(exc))
                continue
            rel = str(Path(res["path"]).relative_to(config.ORIGIN_DIR))
            origin_root, source_path = "my_photos", None
            obytes, omtime = res["bytes"], Path(res["path"]).stat().st_mtime
            osha = res["sha256"]
        else:
            # --- a contributor: NO original in the library. The file goes to a
            # staging folder, the master is built from it, and the upload is
            # then thrown away. The photograph lives only as the master, which
            # was a deliberate decision: their photographs are not part of the
            # archive of originals.
            stage = config.INCOMING_DIR / "staged"
            stage.mkdir(parents=True, exist_ok=True)
            staged = stage / f"{b['token']}-{r['id']}.{r['ext']}"
            try:
                shutil.move(str(src), str(staged))
            except Exception as exc:                   # noqa: BLE001
                failed.append({"file": r["original_name"], "error": str(exc)})
                _mark(r["id"], "failed", str(exc))
                continue
            rel = web_rel
            origin_root, source_path = "user", str(staged)
            obytes, omtime = staged.stat().st_size, staged.stat().st_mtime
            osha = r["sha256"]

        with db.tx() as conn:
            cur = conn.execute(
                "INSERT INTO photos (origin_root, origin_path, origin_sha256, origin_bytes, "
                " origin_mtime, origin_kind, country, place, album_year, event, "
                " taken_at, taken_source, camera, "
                " lens, gps_lat, gps_lon, kind, state, web_name, owner, master_source_path) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?)",
                (origin_root, rel, osha, obytes, omtime,
                 r["ext"], tree.clean_name(country), tree.clean_name(place, ""),
                 year, folder.name,
                 r["taken_at"], r["taken_source"], r["camera"], r["lens"],
                 r["gps_lat"], r["gps_lon"], r["kind"],
                 Path(name).with_suffix(".jpg").name, owner, source_path),
            )
            photo_id = cur.lastrowid
            conn.execute("UPDATE upload_files SET state='stored', photo_id=? WHERE id=?",
                         (photo_id, r["id"]))
        if keep_original:
            # Only now. Until here the copy in incoming is the only one we have.
            src.unlink(missing_ok=True)
        stored.append({"file": r["original_name"], "path": rel, "photo_id": photo_id})
        # The conversion hangs off the upload itself -- no waiting for a scan.
        # ⚠ Which queue depends on where the file came from. An administrator's
        #   upload was written into the originals tree and is converted from
        #   there by the site itself. A contributor's or a guest's file is a
        #   file from OUTSIDE, and it goes to `convert-upload`, which runs in a
        #   process without the originals mounted -- see app/convert_cli.py.
        enqueue("convert" if origin_root != "user" else "convert-upload",
                str(photo_id))

    # ⚠ `committed_at` is written LAST and only here. It is what the client
    #   reads as "finished", so it must not be set while a single file is
    #   still to be done.
    with db.tx() as conn:
        conn.execute(
            "UPDATE upload_batches SET committed_at=datetime('now') WHERE id=?",
            (b["id"],),
        )
    left = [p for p in _batch_dir(batch).iterdir()] if _batch_dir(batch).is_dir() else []
    if not left:
        shutil.rmtree(_batch_dir(batch), ignore_errors=True)
    # ⚠ What comes in through the form has been looked at: year, name, place,
    # country and the viewing list were just filled in. That album does not
    # belong on the list of things still to do.
    album_key = f"{year}/{tree.clean_name(country)}/{folder.name}"
    from . import album as _alb
    _alb.publish(album_key)
    # ⚠ A contributor's album belongs to them: by default only they (and an
    # administrator) see it. Set only when there is no viewing list yet --
    # otherwise a second upload into the same album would overwrite a list
    # somebody had already set.
    if owner and stored:
        from . import acl as _acl
        if not _acl.of_album(year, tree.clean_name(country), folder.name):
            _acl.set_audience(year, tree.clean_name(country), folder.name,
                              [f"user:{owner}"])
    enqueue("geocode", "")
    return {
        "folder": str(folder), "web_folder": str(tree.web_dir(year, country, event)),
        # The name as it lands on disk -- with the trailing year removed
        # (tree.strip_year). The page needs it for the link to the album.
        "event": folder.name, "year": year, "country": country,
        "stored": stored, "skipped": skipped, "failed": failed,
        "incoming_left": len(left),
    }


def purge_orphans(older_than_hours: int = 24) -> dict:
    """Remove batch folders that no longer have a row.

    A batch that is abandoned (browser closed, network gone) leaves a folder
    behind. That grows slowly -- 41 folders for 9 batches, when it was found --
    and nobody notices, because there is no error. The sync does this as it
    goes.

    ⚠ Only what is OLDER than `older_than_hours` and belongs to **no** batch.
    An upload in progress must not be taken out from under somebody's hands.
    """
    import shutil
    import time
    cutoff = time.time() - older_than_hours * 3600
    known = {r["token"] for r in db.connect().execute(
        "SELECT token FROM upload_batches WHERE committed_at IS NULL")}
    gone_ = 0
    for d in config.INCOMING_DIR.iterdir():
        # ⚠ `staged` is NOT an orphan. It holds the master sources of every
        #   contributor's photographs (origin_root='user') until the conversion
        #   has built the master from them -- `photos.master_source_path` points
        #   straight into it. It is not a batch token and it does not start with
        #   `share-`, so without this line a backlog of conversions older than a
        #   day would have been deleted out from under the queue.
        if not d.is_dir() or d.name in ("staged",) or d.name.startswith("share-") \
                or d.name in known:
            continue
        try:
            if d.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(d, ignore_errors=True)
        gone_ += 1
    if gone_:
        log.info("incoming: %d verwaist Batch-Ordner ewechgeholl", gone_)
    return {"removed": gone_}
