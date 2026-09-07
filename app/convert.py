"""The `convert` job: from the original to the master and onto the site.

It hangs directly off an upload -- nothing waits for the next library scan.

    originals/2026/Portugal/Porto/2026....jpg      the original
            |  build the master (locally, in a temp folder)
            v
    library/2026/Portugal/Porto/2026....jpg        mirrored, same path
            |  read it BACK off the share, compare sha256
            v
    data/derivatives/<id>/400.avif|400.webp|lqip.txt   local, from the master
"""
import os
import shutil
import tempfile
from pathlib import Path

from . import config, db, images, library
from .worker import handler


def derivative_dir(photo_id: int) -> Path:
    # In folders of a thousand, or everything ends up in one directory.
    return config.DERIVATIVE_DIR / f"{photo_id // 1000:04d}" / str(photo_id)


def web_path_for(origin_path: str) -> Path:
    """Mirrored: the same path, just .jpg and a different root."""
    return (config.WEB_DIR / origin_path).with_suffix(".jpg")


@handler("convert")
def convert(job) -> None:
    photo_id = int(job["payload"])
    row = db.connect().execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
    if row is None:
        return
    # ⚠ Two sources. A library photograph builds its master from the original
    # in the originals tree. A photograph somebody uploaded for themselves
    # (origin_root='user') has no original there -- it is read from the staging
    # folder, and that source is thrown away AFTER the conversion.
    is_user = row["origin_root"] == "user"
    if is_user:
        src = Path(row["master_source_path"] or "")
        if not src.is_file():
            raise RuntimeError(f"the staged upload is gone: {src}")
        library.check_tree(config.WEB_DIR)
    else:
        src = config.ORIGIN_DIR / row["origin_path"]
        if not src.is_file():
            raise RuntimeError(f"the original is gone: {src}")
        library.check_tree(config.ORIGIN_DIR)
        library.check_tree(config.WEB_DIR)

    dst = web_path_for(row["origin_path"])
    dst.parent.mkdir(parents=True, exist_ok=True)

    # ⚠ The master is computed LOCALLY and only then put on the share.
    # Writing straight to the share means a crash halfway leaves half a file
    # behind that looks exactly like a finished master.
    is_video = row["kind"] == "video"
    duration = None
    with tempfile.TemporaryDirectory(dir=str(config.WORK_DIR)) as td:
        tmp_master = Path(td) / "master.jpg"
        tmp_mp4 = Path(td) / "web.mp4"
        if is_video:
            # ⚠ The "master" of a video is its poster frame -- which is what
            # lets the entire photo machinery (thumbnails, gallery, lightbox)
            # work unchanged. The playable MP4 goes next to the poster.
            from . import video
            poster_png = Path(td) / "poster.png"
            vinfo = video.build(src, poster_png, tmp_mp4)
            duration = vinfo["duration"]
            info = images.build_master(poster_png, tmp_master)
        else:
            info = images.build_master(src, tmp_master)
        local_sha = library.sha256_of(tmp_master)

        if dst.exists():
            # Converted again (the photograph changed): the old copy is ours
            # and may be replaced -- this is the site's tree, not the original.
            dst.unlink()
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(fd, "wb") as out, open(tmp_master, "rb") as inp:
            shutil.copyfileobj(inp, out, library.CHUNK)
            out.flush()
            os.fsync(out.fileno())

        if library.sha256_of(dst) != local_sha:
            dst.unlink(missing_ok=True)
            raise RuntimeError("the master did not survive the write -- copy removed")

        out_dir = derivative_dir(photo_id)
        shutil.rmtree(out_dir, ignore_errors=True)
        # ⚠ ALL widths here, not just the first. An earlier version built only
        # the 400 px one, and the gallery asks for 1200 and 2000 -- so EVERY
        # photograph cost a second of computing the first time somebody looked
        # at it. Sixty of those per page.
        images.build_derivatives(tmp_master, out_dir, config.DERIVATIVE_WIDTHS)

        if is_video:
            # Put the playable web copy next to the poster -- the same
            # crash-proof route (compute locally, place atomically, read the
            # checksum back).
            vdst = dst.with_suffix(".mp4")
            vsha = library.sha256_of(tmp_mp4)
            if vdst.exists():
                vdst.unlink()
            vfd = os.open(vdst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            with os.fdopen(vfd, "wb") as out, open(tmp_mp4, "rb") as inp:
                shutil.copyfileobj(inp, out, library.CHUNK)
                out.flush()
                os.fsync(out.fileno())
            if library.sha256_of(vdst) != vsha:
                vdst.unlink(missing_ok=True)
                raise RuntimeError("the video copy did not survive the write")

    rel = str(dst.relative_to(config.WEB_DIR))
    if is_user:
        # ⚠ Throw the upload away. From here on the photograph exists ONLY as
        #   the master.
        src.unlink(missing_ok=True)
    with db.tx() as conn:
        conn.execute(
            "UPDATE photos SET web_name=?, width=?, height=?, duration_s=?, "
            "master_source_path=CASE WHEN origin_root='user' THEN NULL ELSE ? END, "
            "master_source_sha256=?, master_built_at=datetime('now'), state='ok' "
            "WHERE id=?",
            (rel, info["width"], info["height"],
             round(duration) if duration else row["duration_s"],
             str(src), local_sha, photo_id),
        )

    # ⚠ HERE and not at the upload: a photograph counts as having arrived when
    #   it can actually be looked at. Before the conversion it is a row nobody
    #   can see -- announcing it then would send people to an empty album.
    #
    # ⚠ Counted, not sent. An import of five hundred makes five hundred of
    #   these calls and ONE message. See app/notify.py.
    try:
        from . import notify
        notify.note("photos", f"{row['album_year']}/{row['country']}/{row['event']}",
                    actor=row["owner"] or "", n=1)
    except Exception:                                            # noqa: BLE001
        # A notice must never be the reason a photograph fails to convert.
        log.warning("notify: could not note photo %s", photo_id, exc_info=True)
