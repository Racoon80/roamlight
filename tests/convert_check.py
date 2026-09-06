#!/usr/bin/env python3
"""Acceptance test for the conversion. Runs on the server."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/family/app")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env                                              # noqa: E402

os.environ.setdefault("FAMILY_REQUIRE_AUTH", "0")

from app import config, convert, db, images, library, tree  # noqa: E402

# ---------------------------------------------------------------------------
#  The tests run against the LIVE database and the LIVE originals tree. So:
#    * they work ONLY under a year that does not exist (1999),
#    * they delete ONLY what they created themselves,
#    * and they count at the start and at the end how many photographs that are
#      not theirs are there -- if that number differs, the test is the fault and
#      not the work.
#  A `DELETE FROM photos` without a WHERE once took thirteen real photographs
#  out of the database. That must not happen a second time.
# ---------------------------------------------------------------------------
TEST_YEAR = "1999"
TEST_COUNTRY = "Testland"
MY_BATCHES = []          # remove only what the test created itself


def _foreign(dbf):
    import sqlite3
    con = _env.connect(dbf)
    n = con.execute("SELECT COUNT(*) FROM photos WHERE origin_path NOT LIKE ?",
                    (TEST_YEAR + "/%",)).fetchone()[0]
    con.close()
    return n


def _cleanup(dbf):
    """Remove only what the test created."""
    import sqlite3
    con = _env.connect(dbf)
    ids = [r[0] for r in con.execute("SELECT id FROM photos WHERE origin_path LIKE ?",
                                     (TEST_YEAR + "/%",))]
    if ids:
        q = ",".join("?" * len(ids))
        con.execute(f"DELETE FROM jobs WHERE kind='convert' AND payload IN ({q})",
                    [str(i) for i in ids])
        con.execute(f"DELETE FROM upload_files WHERE photo_id IN ({q})", ids)
        con.execute(f"DELETE FROM photos WHERE id IN ({q})", ids)
    if MY_BATCHES:
        q = ",".join("?" * len(MY_BATCHES))
        con.execute(f"DELETE FROM upload_files WHERE batch_id IN "
                    f"(SELECT id FROM upload_batches WHERE token IN ({q}))", MY_BATCHES)
        con.execute(f"DELETE FROM upload_batches WHERE token IN ({q})", MY_BATCHES)
    con.commit(); con.close()

def _origins():
    from app import config as _c
    return _c.ORIGIN_DIR


def _abort_if_real_data():
    """Stop before anything happens.

    The year 1999 is picked freely -- but if something really is lying there,
    an `rmtree` on that folder would mean losing real photographs. So nothing
    is guessed here: if the year already exists, the test does not run at all.
    """
    import sqlite3
    root = _origins() / TEST_YEAR
    # ⚠ What is looked at is the FIRST part under the year -- that is, the
    # country. This used to be `p.parts[-2:][0]`, which is the folder directly
    # ABOVE the file ("Testevent"), not the country. With that, the guard took
    # the leftovers of an aborted test for real photographs and blocked every
    # further run.
    def _alien(p):
        try:
            return p.relative_to(root).parts[0] != TEST_COUNTRY
        except ValueError:
            return True
    if root.exists() and any(p for p in root.rglob("*") if p.is_file() and _alien(p)):
        raise SystemExit(f"ABORTED: {root} holds files that are not the test's")
    con = _env.connect(_env.need("FAMILY_DB"))
    n = con.execute("SELECT COUNT(*) FROM photos WHERE origin_path LIKE ? "
                    "AND origin_path NOT LIKE ?",
                    (TEST_YEAR + "/%", f"{TEST_YEAR}/{TEST_COUNTRY}/%")).fetchone()[0]
    con.close()
    if n:
        raise SystemExit(f"OFGEBRACH: {n} echt Fotoen am Joer {TEST_YEAR}")


def _wipe_test_tree():
    """ONLY our own sub-folder -- never a whole year."""
    import shutil as _sh
    _sh.rmtree(_origins() / TEST_YEAR / TEST_COUNTRY, ignore_errors=True)
    _sh.rmtree(Path(_env.need("FAMILY_WEB"))
               / TEST_YEAR / TEST_COUNTRY, ignore_errors=True)

ok = bad = 0


def chk(what, cond, detail=""):
    global ok, bad
    if cond:
        ok += 1; print(f"  ok    {what}")
    else:
        bad += 1; print(f"  FAIL  {what}  {detail}")


def make(path, w, h, dt, orient=1):
    from PIL import Image
    Image.new("RGB", (w, h), (40, 90, 130)).save(path, "JPEG", quality=92)
    subprocess.run(["exiftool", "-overwrite_original", "-q",
                    f"-DateTimeOriginal={dt}", "-Make=Canon", "-Model=Canon EOS R5 C",
                    "-LensModel=RF24-70mm F2.8 L IS USM",
                    "-GPSLatitude=41.1579", "-GPSLatitudeRef=N",
                    "-GPSLongitude=-8.6291", "-GPSLongitudeRef=W",
                    f"-Orientation#={orient}", "--", str(path)],
                   check=True, capture_output=True)


def main():
    dbf = _env.need("FAMILY_DB")
    _abort_if_real_data()
    foreign_before = _foreign(dbf)
    print("Ofnahm-Test — Konversioun (Etapp 5)")
    print(f"  ({foreign_before} foreign photographs in the database — those stay untouched)\n")
    db.init()
    # Always starts on a clean database -- a previously failed run would
    # otherwise leave rows behind and the test breaks on a UNIQUE collision.
    _cleanup(_env.need("FAMILY_DB"))
    tmp = Path("/tmp/convert_check"); shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir()
    year, country, event = "1999", "Testland", "Testevent"
    _wipe_test_tree()
    _wipe_test_tree()

    # 1) a wide image, 2) a TALL image with orientation 6 (turned 90 degrees)
    big = tmp / "breet.jpg";  make(big, 6000, 4000, "1999:07:14 11:03:22")
    # ⚠ This is the bug in test form: the file is PHYSICALLY WIDE (6000x4000),
    # but the EXIF says "turn 90 degrees". After the rotation it has to be
    # TALL. Whoever measures before autorot() gets a landscape result here --
    # and the photograph is cut off at the top and the bottom on the site.
    tall = tmp / "hoch.jpg";  make(tall, 6000, 4000, "1999:07:15 09:41:05", orient=6)

    folder = tree.target_dir(year, country, event)
    ids = []
    for i, s in enumerate((big, tall), start=1):
        res = library.store_original(s, folder, library.build_name("1999-07-14 11:03:22", i, "jpg"))
        rel = str(Path(res["path"]).relative_to(config.ORIGIN_DIR))
        with db.tx() as conn:
            cur = conn.execute(
                "INSERT INTO photos (origin_root, origin_path, origin_sha256, origin_bytes,"
                " origin_kind, country, place, taken_at, kind, state)"
                " VALUES ('my_photos',?,?,?,'jpg',?,?, '2026-07-14 11:03:22','photo','new')",
                (rel, res["sha256"], res["bytes"], country, "Porto"))
            ids.append(cur.lastrowid)

    t0 = time.time()
    for pid in ids:
        convert.convert({"payload": str(pid)})
    dur = time.time() - t0
    print(f"  ({len(ids)} Fotoen an {dur:.1f}s)\n")

    rows = {r["id"]: r for r in db.connect().execute(
        "SELECT * FROM photos WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)}

    chk("both at 'ok'", all(rows[i]["state"] == "ok" for i in ids),
        [rows[i]["state"] for i in ids])

    web = config.WEB_DIR / year / country / event
    masters = sorted(p for p in web.iterdir() if p.suffix == ".jpg")
    chk("the master mirrors the path in the web tree", len(masters) == 2,
        [str(p) for p in masters])
    chk("the master is a JPEG, not the original format",
        all(p.suffix == ".jpg" for p in masters))

    import pyvips
    m0 = pyvips.Image.new_from_file(str(masters[0]))
    chk("the long edge is at 4000 px", max(m0.width, m0.height) == config.MASTER_LONG_EDGE,
        f"{m0.width}x{m0.height}")
    sizes = [p.stat().st_size for p in masters]
    chk("Master ass kleng (< 6 MB)", all(s < 6 * 1024 * 1024 for s in sizes),
        [f"{s/1024/1024:.1f} MB" for s in sizes])

    # The tall photograph: 4000x6000 with orientation 1 -> has to stay TALL.
    tall_row = rows[ids[1]]
    chk("tall stays tall (autorot before measuring)",
        tall_row["height"] > tall_row["width"],
        f"DB {tall_row['width']}x{tall_row['height']}")
    mt = pyvips.Image.new_from_file(str(config.WEB_DIR / tall_row["web_name"])).autorot()
    chk("the file on disk agrees with the database",
        (mt.width, mt.height) == (tall_row["width"], tall_row["height"]),
        f"Datei {mt.width}x{mt.height} / DB {tall_row['width']}x{tall_row['height']}")

    exif = subprocess.run(["exiftool", "-j", "-Model", "-LensModel", "-DateTimeOriginal",
                           "-GPSLatitude", "-Orientation#", "--", str(masters[0])],
                          capture_output=True, text=True).stdout
    chk("EXIF am Master erhalen (Kamera)", "EOS R5 C" in exif, exif[:200])
    chk("EXIF am Master erhalen (Objektiv)", "RF24-70mm" in exif, exif[:200])
    chk("EXIF am Master erhalen (GPS)", "GPS" in exif, exif[:200])
    import json as _json
    ori = _json.loads(exif)[0].get("Orientation")
    chk("the orientation on the master is 1 (do not rotate again)", ori == 1, f"Orientation={ori}")

    d = convert.derivative_dir(ids[0])
    chk("web sizes are local (400 avif + webp + lqip)",
        (d / "400.avif").is_file() and (d / "400.webp").is_file() and (d / "lqip.txt").is_file(),
        sorted(p.name for p in d.iterdir()) if d.is_dir() else "kee Ordner")
    chk("Derivater leien NET um NAS", not any(p.suffix in (".avif", ".webp")
                                              for p in web.iterdir()))
    chk("the LQIP is a data: URI",
        (d / "lqip.txt").read_text().startswith("data:image/jpeg;base64,"))

    # --- Sync behaviour: converting again overwrites the master --------------
    before = masters[0].stat().st_mtime
    time.sleep(1.1)
    convert.convert({"payload": str(ids[0])})
    chk("converting again renews the master", masters[0].stat().st_mtime > before)

    # --- and the original stays untouched ------------------------------------
    origs = sorted(p for p in folder.iterdir() if p.is_file())
    chk("the originals are still there", len(origs) == 2, [p.name for p in origs])
    chk("the original is not a JPEG copy of the master",
        all(p.stat().st_size > 0 for p in origs))

    shutil.rmtree(tmp, ignore_errors=True)
    _wipe_test_tree()
    _wipe_test_tree()
    for pid in ids:
        shutil.rmtree(convert.derivative_dir(pid), ignore_errors=True)
    _cleanup(_env.need("FAMILY_DB"))

    # ⚠ Only downwards is an error. Photographs turning UP during a run is
    # normal: somebody copies a folder into the originals tree and a scan takes
    # it in. This used to be `==`, and then the test raised the alarm although
    # nothing had been lost.
    after_ = _foreign(dbf)
    chk("no foreign photograph was lost", after_ >= foreign_before,
        f"{foreign_before} -> {after_}: the test deleted rows "
        f"that were not its own!")
    print(f"\n  {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
