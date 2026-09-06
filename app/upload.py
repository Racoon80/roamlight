"""Uploading: batch -> files -> suggestion -> confirmation -> filed away.

    incoming/<batch>/<n>.part          uploaded, in chunks of 5 MB
        |  sha256, exiftool, duplicate check
        v
    a suggestion: year - country - name - place
        |  the person uploading confirms or changes it
        v
    originals/<year>/<country>/<name>/  verified (sha256 read back)
        |
        +- and only THEN is the copy in incoming removed
"""
import logging
import re
import secrets
import shutil
from pathlib import Path

from . import av, config, db, library, meta, tree

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
    row = db.connect().execute(
        "SELECT id FROM upload_batches WHERE token=? AND committed_at IS NULL", (batch,)
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


def commit(batch: str, year: str, country: str, event: str = "", place: str = "",
           include_duplicates: bool = False, owner: str = None,
           keep_original: bool = True) -> dict:
    """The four fields are confirmed -- now it gets filed away.

    The FOLDER is the name: a wedding is called "Anna's wedding" and not the name
    of the village. The place is something else and stays as metadata (for the
    map). With no name the place is used instead, and the other way round.

    Per file: write into the originals tree, read it back, compare the sha256,
    create the row in `photos`, and ONLY THEN remove the copy in incoming. If
    anything breaks, the upload stays where it is -- always.
    """
    b = db.connect().execute(
        "SELECT * FROM upload_batches WHERE token=? AND committed_at IS NULL", (batch,)
    ).fetchone()
    if b is None:
        raise ValueError("unknown batch (or already finished)")

    event = (event or "").strip() or (place or "").strip()
    place = (place or "").strip() or event
    folder = tree.target_dir(year, country, event)
    rows = db.connect().execute(
        "SELECT * FROM upload_files WHERE batch_id=? AND state='ready' ORDER BY taken_at, id",
        (b["id"],)
    ).fetchall()

    stored, skipped, failed = [], [], []
    seq = 0
    for r in rows:
        if r["dup_of"] and not include_duplicates:
            skipped.append({"file": r["original_name"], "reason": "Duplikat"})
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
            failed.append({"file": r["original_name"],
                           "error": ("virus scan: infected" if av_state == av.INFECTED
                                     else "virus scan could not run")})
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
        from .worker import enqueue
        enqueue("convert", str(photo_id))

    with db.tx() as conn:
        conn.execute(
            "UPDATE upload_batches SET committed_at=datetime('now'), year=?, country=?, "
            "event=?, place=? WHERE id=?",
            (year, country, event, place, b["id"]),
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
    from .worker import enqueue as _eq
    _eq("geocode", "")
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
        if not d.is_dir() or d.name.startswith("share-") or d.name in known:
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
