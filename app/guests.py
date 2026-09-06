"""What a guest puts in through a share link -- into quarantine.

⚠ **This is the most dangerous function in the whole project**: a stranger
puts a file on the machine that has `rw` on **the only copy of the
originals**, and that file goes through libvips and exiftool. A hole in a
parser here does not mean "website broken", it means "photographs gone".

Therefore:

* **Nothing goes into the library.** Everything lands in
  `incoming/share-<token>/`, `state='guest'`. A guest's photograph is **never**
  visible on the site by itself -- not even a perfectly good one.
* **The type is determined before decoding**, from the magic bytes. `.jpg`
  says nothing about the content.
* **The site makes up the stored name itself.** The guest's name stays
  metadata in the database and **never** reaches the file system.
* **Limits**: per file, per link, **and** a global quota -- ten links are
  otherwise 20 GB on a 60 GB disk that also holds the database.
"""
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from . import av, config, db, meta

log = logging.getLogger("family")

CHUNK = 1024 * 1024


def _dir(token: str) -> Path:
    return config.INCOMING_DIR / f"share-{token}"


def _album_dir(year: str, country: str, event: str) -> Path:
    """The quarantine for a member's upload into ONE album -- its own folder
    per album, never in the library. The name is a hash of the album key, so
    that no place name reaches the file system."""
    import hashlib
    key = f"{year or '—'}/{country or '—'}/{event or '—'}"
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return config.INCOMING_DIR / f"album-{h}"


def _total_quarantine() -> int:
    n = 0
    # ⚠ Both kinds of quarantine count towards THE SAME global quota (share-*
    # for guests, album-* for members) -- otherwise one would fill the disk
    # while the other still thought it had room.
    for pattern in ("share-*", "album-*"):
        for d in config.INCOMING_DIR.glob(pattern):
            for f in d.rglob("*"):
                if f.is_file():
                    n += f.stat().st_size
    return n


def receive_sync(share, guest_name: str, upload) -> dict:
    """Eng Datei vun engem Gaascht unhuelen."""
    dest = _dir(share["token"])
    dest.mkdir(parents=True, exist_ok=True)

    con = db.connect()
    da = con.execute("SELECT COUNT(*) n, coalesce(SUM(bytes),0) b FROM share_uploads "
                     "WHERE share_id=? AND state<>'rejected'", (share["id"],)).fetchone()
    if da["n"] >= share["guest_max_files"]:
        raise ValueError(f"this link accepts at most {share['guest_max_files']} files")
    if _total_quarantine() >= config.GUEST_TOTAL_BYTES:
        # ⚠ Global, not per link. Otherwise ten links fill the disk.
        raise ValueError("the waiting room is full — ask the owner to empty it")

    # The name NEVER reaches the file system. It is cleaned and stays metadata.
    raw_name = (getattr(upload, "filename", "") or "")[-120:]
    tmp = Path(tempfile.mkstemp(dir=str(dest), prefix=".part-")[1])
    size = 0
    try:
        with open(tmp, "wb") as f:
            while True:
                chunk = upload.file.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > share["guest_max_bytes"] or \
                   da["b"] + size > share["guest_max_bytes"]:
                    raise ValueError("too large for this link")
                f.write(chunk)
        if not size:
            raise ValueError("the file is empty")
        # ⚠ Type FROM THE CONTENT, and the megapixel limit -- before anything decodes.
        ok, why = meta.looks_like_media(tmp, by_content=True)
        if not ok:
            raise ValueError(f"that is not a photograph or a video ({why})")
        # ⚠ Virus scan BEFORE the file reaches quarantine. An infected file
        # from a guest never lands on the disk at all -- it is deleted here.
        verdict, detail = av.scan(tmp)
        if verdict != av.CLEAN:
            log.warning("guest file refused (%s): %s", verdict, detail)
            raise ValueError("that file did not pass the virus scan"
                             if verdict == av.INFECTED
                             else "the file could not be checked — try again")
        sha = _sha(tmp)
        suffix_ = _ext(tmp, raw_name)
        stored_name = f"{datetime.now():%Y%m%d-%H%M%S}-{sha[:8]}{suffix_}"
        goal_ = dest / stored_name
        # `O_CREAT|O_EXCL`: ni iwwerschreiwen, och net bei enger Kollisioun.
        fd = os.open(goal_, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
        os.close(fd)
        shutil.move(str(tmp), str(goal_))
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    with db.tx() as c:
        ident = c.execute(
            "INSERT INTO share_uploads (share_id, guest_name, filename, bytes, "
            "  sha256, state) VALUES (?,?,?,?,?, 'guest')",
            (share["id"], (guest_name or "").strip()[:60] or None,
             stored_name, size, sha)).lastrowid
    log.info("Gaascht-Datei %s (%d B) fir Link %s…", stored_name, size, share["token"][:6])
    return {"id": ident, "stored": stored_name, "bytes": size,
            "note": "waiting for the owner — nothing is on the site yet"}


def receive_member(year: str, country: str, event: str,
                   uploader: str, upload) -> dict:
    """A registered member puts a photograph into ONE album -- into quarantine.

    ⚠ EXACTLY the same chain of checks as for a guest (see above): type from
    the content, virus scan BEFORE anything reaches the disk, `O_EXCL`, and the
    member's name stays metadata. The only difference is the target (ONE album)
    and that the quota is counted per album. Nothing lands on the site by itself."""
    dest = _album_dir(year, country, event)
    dest.mkdir(parents=True, exist_ok=True)

    con = db.connect()
    da = con.execute(
        "SELECT COUNT(*) n, coalesce(SUM(bytes),0) b FROM album_uploads "
        "WHERE coalesce(year,'—')=? AND coalesce(country,'—')=? AND coalesce(event,'—')=? "
        "  AND state<>'rejected'",
        (year or "—", country or "—", event or "—")).fetchone()
    if da["n"] >= config.GUEST_MAX_FILES:
        raise ValueError(f"this album already has {da['n']} waiting — ask the owner to review them")
    if _total_quarantine() >= config.GUEST_TOTAL_BYTES:
        raise ValueError("the waiting room is full — ask the owner to empty it")

    raw_name = (getattr(upload, "filename", "") or "")[-120:]
    tmp = Path(tempfile.mkstemp(dir=str(dest), prefix=".part-")[1])
    size = 0
    try:
        with open(tmp, "wb") as f:
            while True:
                chunk = upload.file.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > config.GUEST_MAX_BYTES:
                    raise ValueError("that file is too large")
                f.write(chunk)
        if not size:
            raise ValueError("the file is empty")
        ok, why = meta.looks_like_media(tmp, by_content=True)
        if not ok:
            raise ValueError(f"that is not a photograph or a video ({why})")
        verdict, detail = av.scan(tmp)
        if verdict != av.CLEAN:
            log.warning("member file refused (%s): %s", verdict, detail)
            raise ValueError("that file did not pass the virus scan"
                             if verdict == av.INFECTED
                             else "the file could not be checked — try again")
        sha = _sha(tmp)
        suffix_ = _ext(tmp, raw_name)
        stored_name = f"{datetime.now():%Y%m%d-%H%M%S}-{sha[:8]}{suffix_}"
        goal_ = dest / stored_name
        fd = os.open(goal_, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
        os.close(fd)
        shutil.move(str(tmp), str(goal_))
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    with db.tx() as c:
        ident = c.execute(
            "INSERT INTO album_uploads (year, country, event, uploader, filename, "
            "  bytes, sha256, state) VALUES (?,?,?,?,?,?,?, 'guest')",
            (year or None, country or None, event or None,
             (uploader or "").strip()[:60] or None, stored_name, size, sha)).lastrowid
    log.info("Member-Datei %s (%d B) vun %s fir Album %s/%s/%s…",
             stored_name, size, uploader, year, country, event)
    return {"id": ident, "stored": stored_name, "bytes": size,
            "note": "waiting for the owner — nothing is on the site yet"}


def contribute_live(year: str, country: str, event: str,
                    uploader: str, upload) -> dict:
    """A registered member puts a photograph DIRECTLY into an album -- **no
    approval** (only guest links through /s/ need that, see receive_sync).

    ⚠ The same checks as everywhere else (type from the content, fail-closed
    virus scan, sizes). Then the file is stored -- as if the admin were
    accepting a guest upload -- with `store_original` (which only ever creates
    NEW files, never overwrites) and adopted (`scan.adopt` does the metadata
    and the conversion). The member becomes the `owner`, so they can manage
    their own contributions."""
    import tempfile
    from . import library, scan, tree
    tmp = Path(tempfile.mkstemp(prefix="contrib-")[1])
    size = 0
    try:
        with open(tmp, "wb") as f:
            while True:
                chunk = upload.file.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > config.GUEST_MAX_BYTES:
                    raise ValueError("that file is too large")
                f.write(chunk)
        if not size:
            raise ValueError("the file is empty")
        ok, why = meta.looks_like_media(tmp, by_content=True)
        if not ok:
            raise ValueError(f"that is not a photograph or a video ({why})")
        verdict, detail = av.scan(tmp)
        if verdict != av.CLEAN:
            log.warning("member file (live) refused (%s): %s", verdict, detail)
            raise ValueError("that file did not pass the virus scan"
                             if verdict == av.INFECTED
                             else "the file could not be checked — try again")
        sha = _sha(tmp)
        suffix_ = _ext(tmp, (getattr(upload, "filename", "") or "")[-120:])
        stored_name = f"{datetime.now():%Y%m%d-%H%M%S}-{sha[:8]}{suffix_}"
        folder = tree.target_dir(year, country,
                                 tree.clean_name(event or "Album", "Album"))
        res = library.store_original(tmp, folder, stored_name, expect_sha=sha)
        rel = str(Path(res["path"]).relative_to(config.ORIGIN_DIR))
        photo_id = scan.adopt(Path(res["path"]), rel)
        with db.tx() as c:
            c.execute("UPDATE photos SET owner=? WHERE id=? AND (owner IS NULL OR owner='')",
                      (uploader or None, photo_id))
    finally:
        tmp.unlink(missing_ok=True)
    log.info("member photo %s live from %s for album %s/%s/%s",
             photo_id, uploader, year, country, event)
    return {"photo_id": photo_id, "live": True, "note": "added to the album"}


def _sha(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _ext(p: Path, raw_: str) -> str:
    """D'Endung aus dem **erkannten** Typ, ni aus dem Numm vum Gaascht."""
    art = meta.kind_of(p)
    if not art:
        raise ValueError("that file type is not accepted")
    return "." + art


def waiting() -> list:
    """Everything waiting for the admin's click -- guests (share) AND members
    (album), in one list. Every row has a `ref` (`g<id>` / `a<id>`) that says
    which source it came from when it is accepted or thrown out."""
    con = db.connect()
    out = []
    for r in con.execute(
            "SELECT u.*, s.token, a.title FROM share_uploads u "
            "JOIN shares s ON s.id=u.share_id JOIN albums a ON a.id=s.album_id "
            "WHERE u.state='guest' ORDER BY u.at DESC"):
        out.append({"ref": f"g{r['id']}", "source": "guest",
                    "who": r["guest_name"] or "a guest",
                    "filename": r["filename"], "label": r["title"] or "shared link",
                    "bytes": r["bytes"], "at": r["at"]})
    for r in con.execute(
            "SELECT * FROM album_uploads WHERE state='guest' ORDER BY at DESC"):
        label = " / ".join(x for x in (r["year"], r["country"], r["event"]) if x)
        out.append({"ref": f"a{r['id']}", "source": "member",
                    "who": (r["uploader"] or "a member") + " (member)",
                    "filename": r["filename"], "label": label or "album",
                    "bytes": r["bytes"], "at": r["at"]})
    out.sort(key=lambda x: x["at"] or "", reverse=True)
    return out


def _split(refs):
    """`g<id>` -> Gaascht, `a<id>` -> Member. Alles anescht ass e Feeler.

    ⚠ Fréier gouf alles, wat net mat `g` oder `a` ufänkt, einfach ignoréiert:
      wien nach déi al Form (eng plakeg Zuel) geschéckt huet, krut e 200 an
      d'Gefill, et wier ugeholl -- an et ass NÄISCHT geschitt. Eng Datei, déi
      an der Quarantän bleift, well een op de falschen Knäppchen gedréckt huet,
      ass genee dee Feeler, dee kee mierkt.
    """
    g, a = [], []
    for x in refs or []:
        s = str(x)
        if s.startswith("g") and s[1:].isdigit():
            g.append(int(s[1:]))
        elif s.startswith("a") and s[1:].isdigit():
            a.append(int(s[1:]))
        else:
            raise ValueError(f"unknown reference {s!r} -- expected g<id> or a<id>")
    return g, a


def accept_any(refs) -> dict:
    """A mixed click: `g<id>` are guest uploads, `a<id>` member uploads."""
    g, a = _split(refs)
    ra, rb = accept(g), accept_member(a)
    return {"accepted": ra["accepted"] + rb["accepted"],
            "failed": ra["failed"] + rb["failed"]}


def reject_any(refs) -> dict:
    g, a = _split(refs)
    return {"rejected": reject(g)["rejected"] + reject_member(a)["rejected"]}


def accept(ids) -> dict:
    """The admin's click: out of quarantine and onto the normal path.

    ⚠ Only here does a stranger's file become a photograph. Until now it lay
    in a folder that has nothing to do with the library.
    """
    from . import library, scan, tree
    made, failed = [], []
    for r in _rows(ids):
        src = _dir(r["token"]) / r["filename"]
        if not src.is_file():
            failed.append({"id": r["id"], "error": "the file is gone"})
            continue
        try:
            yr = (datetime.now().strftime("%Y")
                    if not r["at"] else r["at"][:4])
            dest = tree.target_dir(yr, "Guests", tree.clean_name(
                r["title"] or "Share link", "Share link"))
            res = library.store_original(src, dest, r["filename"], expect_sha=r["sha256"])
            rel = str(Path(res["path"]).relative_to(config.ORIGIN_DIR))
            photo_id = scan.adopt(Path(res["path"]), rel)
            with db.tx() as c:
                c.execute("UPDATE share_uploads SET state='accepted', photo_id=? "
                          "WHERE id=?", (photo_id, r["id"]))
            src.unlink(missing_ok=True)
            made.append({"id": r["id"], "photo_id": photo_id})
        except Exception as exc:                                # noqa: BLE001
            log.warning("Gaascht-Datei %s: %s", r["id"], exc)
            failed.append({"id": r["id"], "error": str(exc)})
    return {"accepted": made, "failed": failed}


def reject(ids) -> dict:
    n = 0
    for r in _rows(ids):
        (_dir(r["token"]) / r["filename"]).unlink(missing_ok=True)
        with db.tx() as c:
            c.execute("UPDATE share_uploads SET state='rejected' WHERE id=?", (r["id"],))
        n += 1
    return {"rejected": n}


def _rows(ids):
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    return db.connect().execute(
        f"SELECT u.*, s.token, a.title FROM share_uploads u "
        f"JOIN shares s ON s.id=u.share_id JOIN albums a ON a.id=s.album_id "
        f"WHERE u.id IN ({q}) AND u.state='guest'", ids).fetchall()


def accept_member(ids) -> dict:
    """Accept a member's upload: out of quarantine and into THE album
    (year/country/event) in the originals tree -- exactly as if it had been
    copied in there by hand.

    ⚠ Same rule as for a guest: only here does a stranger's file become a
    photograph. `store_original` only ever creates NEW files (the originals
    tree is never overwritten), and `scan.adopt` hangs it into the database --
    and with that into the right album, because the path
    <year>/<country>/<event> IS that album."""
    from . import library, scan, tree
    made, failed = [], []
    for r in _member_rows(ids):
        src = _album_dir(r["year"], r["country"], r["event"]) / r["filename"]
        if not src.is_file():
            failed.append({"id": r["id"], "error": "the file is gone"})
            continue
        try:
            yr = r["year"] or (r["at"][:4] if r["at"] else datetime.now().strftime("%Y"))
            dest = tree.target_dir(yr, r["country"] or "",
                                     tree.clean_name(r["event"] or "Album", "Album"))
            res = library.store_original(src, dest, r["filename"], expect_sha=r["sha256"])
            rel = str(Path(res["path"]).relative_to(config.ORIGIN_DIR))
            photo_id = scan.adopt(Path(res["path"]), rel)
            with db.tx() as c:
                c.execute("UPDATE album_uploads SET state='accepted', photo_id=? "
                          "WHERE id=?", (photo_id, r["id"]))
            src.unlink(missing_ok=True)
            made.append({"id": r["id"], "photo_id": photo_id})
        except Exception as exc:                                # noqa: BLE001
            log.warning("Member-Datei %s: %s", r["id"], exc)
            failed.append({"id": r["id"], "error": str(exc)})
    return {"accepted": made, "failed": failed}


def reject_member(ids) -> dict:
    n = 0
    for r in _member_rows(ids):
        (_album_dir(r["year"], r["country"], r["event"]) / r["filename"]).unlink(missing_ok=True)
        with db.tx() as c:
            c.execute("UPDATE album_uploads SET state='rejected' WHERE id=?", (r["id"],))
        n += 1
    return {"rejected": n}


def _member_rows(ids):
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    return db.connect().execute(
        f"SELECT * FROM album_uploads WHERE id IN ({q}) AND state='guest'", ids).fetchall()


# ---------------------------------------------------------------------------
#  Downloading -- the master, without GPS
# ---------------------------------------------------------------------------
def clean_master(photo_id: int, keep_gps: bool = False) -> FileResponse:
    """The master for a share link.

    ⚠ By default: **GPS out**, and with it `SerialNumber`, `OwnerName` and
    `Artist`. Whoever gets a link should get the photograph -- not the home
    address and the camera's serial number. Reversible per link with `keep_gps`.
    """
    from . import serve
    p = serve.master_path(photo_id, admin=True)
    if keep_gps:
        return FileResponse(p, media_type="image/jpeg", filename=p.name,
                            headers=_HEADERS)
    out = Path(tempfile.mkstemp(suffix=".jpg", prefix="share-")[1])
    shutil.copyfile(p, out)
    # ⚠ FAILS CLOSED, not open. An earlier version did not look at the return
    # code and let the file out either way: one exiftool failure would have
    # handed a stranger the master WITH GPS, serial number and owner's name --
    # and the promise that everything going out through /s/ runs through a
    # strip would have been a lie, without anyone noticing.
    #
    # `-*GPS*=` instead of `-GPS:all=`: the latter only hits the EXIF GPS IFD.
    # The master gets its tags from the original with `-all:all` -- and that
    # brings XMP-exif:GPS* and IPTC-Ext:Location* along.
    ok = subprocess.run(
        ["exiftool", "-overwrite_original", "-q", "-m",
         # ⚠ `-MakerNotes:all=` has to be in there. The camera serial number
         # sits INSIDE the MakerNotes on some makes and survives a
         # `-SerialNumber=` -- checked, not assumed. What is lost is the lens
         # detail; the camera and the date taken stay, because those are in the
         # EXIF. This affects ONLY the copy that goes out: the master on the
         # site and the original are not touched.
         "-MakerNotes:all=",
         "-*GPS*=", "-XMP:Geotag=", "-*Location*=",
         "-SerialNumber=", "-CameraSerialNumber=", "-InternalSerialNumber=",
         "-OwnerName=", "-Artist=", "-By-line=", "-Creator=",
         "--", str(out)], capture_output=True, timeout=120, check=False)
    if ok.returncode != 0 or not _clean(out):
        out.unlink(missing_ok=True)
        log.error("the GPS strip failed for photo %s -- nothing handed out",
                  photo_id)
        raise HTTPException(status_code=503,
                            detail="the download could not be prepared")
    return FileResponse(out, media_type="image/jpeg", filename=p.name,
                        headers=_HEADERS, background=_delete_later(out))


def zip_all(rows, keep_gps: bool = False) -> Path:
    """Every photograph (and video) behind a share link in ONE zip.

    ⚠ Same rule as for the single download: everything going out through /s/
    runs through the strip. Here it is done in ONE exiftool call for all the
    photographs (much faster than one per file), and afterwards it is checked
    ONCE that nothing sensitive is left -- otherwise NOTHING is handed out.
    Returns the path of the zip; the caller deletes it.
    """
    import zipfile
    zpath = Path(tempfile.mkstemp(suffix=".zip", prefix="share-all-")[1])
    try:
        with tempfile.TemporaryDirectory(prefix="share-zip-") as td:
            tdp = Path(td)
            photos = []        # temp paths (photographs, stripped)
            videos = []       # (source, name-in-the-zip)
            for i, r in enumerate(rows, 1):
                if not r["web_name"]:
                    continue
                if r["kind"] == "video":
                    mp4 = (config.WEB_DIR / r["web_name"]).with_suffix(".mp4")
                    if mp4.is_file():
                        videos.append((mp4, f"{i:03d}-" + Path(r["web_name"]).with_suffix(".mp4").name))
                    continue
                src = config.WEB_DIR / r["web_name"]
                if not src.is_file():
                    continue
                dst = tdp / (f"{i:03d}-" + Path(r["web_name"]).name)
                shutil.copyfile(src, dst)
                photos.append(dst)
            if photos and not keep_gps:
                strip = subprocess.run(
                    ["exiftool", "-overwrite_original", "-q", "-m",
                     "-MakerNotes:all=", "-*GPS*=", "-XMP:Geotag=", "-*Location*=",
                     "-SerialNumber=", "-CameraSerialNumber=", "-InternalSerialNumber=",
                     "-OwnerName=", "-Artist=", "-By-line=", "-Creator=",
                     "--", *[str(f) for f in photos]],
                    capture_output=True, timeout=900, check=False)
                pr = subprocess.run(
                    ["exiftool", "-s", "-s", "-s", "-n", "-a",
                     "-*GPS*", "-*Location*", "-SerialNumber", "-CameraSerialNumber",
                     "-InternalSerialNumber", "-OwnerName", "-Artist", "-By-line",
                     "-Creator", "--", *[str(f) for f in photos]],
                    capture_output=True, timeout=900, check=False, text=True)
                # ⚠ With SEVERAL files exiftool prints "======== <name>" per
                # file and an "N image files read" line -- even when NOTHING
                # was found. Those lines are NOT a sensitive tag. Only a real
                # tag/value line means something is still in there.
                broken = [ln for ln in (pr.stdout or "").splitlines()
                            if ln.strip() and not ln.startswith("========")
                            and "image files read" not in ln]
                if strip.returncode != 0 or broken:
                    log.error("the zip strip failed (%s) -- nothing handed out",
                              broken[:2])
                    raise HTTPException(status_code=503,
                                        detail="the download could not be prepared")
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
                for f in photos:
                    z.write(f, arcname=f.name)
                for src, arc in videos:
                    z.write(src, arcname=arc)
        return zpath
    except Exception:
        zpath.unlink(missing_ok=True)
        raise


def _clean(p: Path) -> bool:
    """Checked, not hoped for: is there still something in it that must not go out?"""
    try:
        pr = subprocess.run(
            ["exiftool", "-s", "-s", "-s", "-n", "-a",
             "-*GPS*", "-*Location*", "-SerialNumber", "-CameraSerialNumber",
             "-InternalSerialNumber", "-OwnerName", "-Artist", "-By-line",
             "-Creator", "--", str(p)],
            capture_output=True, timeout=60, check=False, text=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return not (pr.stdout or "").strip()


def _delete_later(p: Path):
    from starlette.background import BackgroundTask
    return BackgroundTask(lambda: p.unlink(missing_ok=True))


_HEADERS = {
    "Cache-Control": "private, no-store",
    "CDN-Cache-Control": "no-store",
    "Cloudflare-CDN-Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow, noimageindex, noarchive",
    "Referrer-Policy": "no-referrer",
}
