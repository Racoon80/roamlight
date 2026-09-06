#!/usr/bin/env python3
"""Acceptance test for the upload. Runs on the server.

    bash tests/run.sh upload

Makes synthetic photographs with EXIF, carries them through the whole API, and
then checks on the disk. It touches nothing but what it creates for the test --
and it clears that up again at the end.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env                                              # noqa: E402

# ⚠ The group names are NOT hard-coded: they come from the same environment the
# site itself reads. A test that assumes "admin" would fail on every
# installation that calls its groups something else -- and one that assumes
# somebody's own names would fail everywhere else.
def _group(var, fallback):
    v = os.environ.get(var)
    if not v:
        try:                                     # the service's own file
            for line in open("/etc/family/env"):
                if line.startswith(var + "="):
                    v = line.split("=", 1)[1].strip().strip('"\''); break
        except OSError:
            pass
    return (v or fallback).split(",")[0].strip()


ADMIN_GROUP = _group("FAMILY_ADMIN_GROUPS", "admin")
VIEWER_GROUP = _group("FAMILY_VIEWER_GROUPS", "family")

BASE = "http://127.0.0.1:8080"
# ⚠ Kee feste Wee méi: _env.headers() weess, wéi ee sech op DËSER
#   Installatioun ausweist -- Proxy-Käpp oder Apparat-Token.
_ADMIN_H = _env.headers("siteadmin")
HDR = dict(_env.headers("zz-test-admin"))
ORIGINS = Path(_env.need("FAMILY_ORIGINS"))
INCOMING = Path(_env.need("FAMILY_INCOMING"))

YEAR, COUNTRY, EVENT, PLACE = "1999", "Testland", "Testevent", "Testplaz"
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

def _abort_if_real_data():
    """Stop before anything happens.

    The year 1999 is picked freely -- but if something really is lying there,
    an `rmtree` on that folder would mean losing real photographs. So nothing
    is guessed here: if the year already exists, the test does not run at all.
    """
    import sqlite3
    root = ORIGINS / TEST_YEAR
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
    _sh.rmtree(ORIGINS / TEST_YEAR / TEST_COUNTRY, ignore_errors=True)
    _sh.rmtree(Path(_env.need("FAMILY_WEB"))
               / TEST_YEAR / TEST_COUNTRY, ignore_errors=True)

ok = bad = 0


def chk(what, cond, detail=""):
    global ok, bad
    if cond:
        ok += 1; print(f"  ok    {what}")
    else:
        bad += 1; print(f"  FAIL  {what}  {detail}")


def call(method, path, data=None, raw=None, ctype="application/json"):
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    req = urllib.request.Request(BASE + path, data=body, method=method,
                                 headers={**HDR, "Content-Type": ctype})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def make_photo(path, dt, seq):
    """A real JPEG with EXIF -- Pillow for the image, exiftool for the metadata."""
    from PIL import Image
    Image.new("RGB", (1200, 800), (30 + seq * 20, 60, 90)).save(path, "JPEG", quality=90)
    subprocess.run(
        ["exiftool", "-overwrite_original", "-q",
         f"-DateTimeOriginal={dt}", f"-CreateDate={dt}",
         "-Make=Canon", "-Model=Canon EOS R5 C", "-LensModel=RF24-70mm F2.8 L IS USM",
         "-ISO=400", "-FNumber=2.8", "-ExposureTime=1/250",
         "-GPSLatitude=41.1579", "-GPSLatitudeRef=N",
         "-GPSLongitude=-8.6291", "-GPSLongitudeRef=W", "--", str(path)],
        check=True, capture_output=True)


def upload(batch, path):
    st, f = call("POST", f"/api/upload/{batch}/file",
                 {"name": path.name, "size": path.stat().st_size})
    assert st == 200, f
    fid = f["file_id"]
    data = path.read_bytes()
    step = 5 * 1024 * 1024
    for off in range(0, len(data), step):
        st, _ = call("PUT", f"/api/upload/{batch}/file/{fid}/chunk?offset={off}",
                     raw=data[off:off + step], ctype="application/octet-stream")
        assert st == 200
    return call("POST", f"/api/upload/{batch}/file/{fid}/done")[1]



def _drop_test_members():
    """⚠ An account the test invented through the header is noted by
    `members.note_seen()` -- and afterwards it stands on the settings page
    among the family. A test must not invent a person who then looks real."""
    import sqlite3 as _s
    con = _env.connect(_env.need("FAMILY_DB"))
    con.execute("DELETE FROM members WHERE username LIKE 'zz-test-%' "
                "AND seen_in_authentik=0")
    con.commit(); con.close()


def main():
    tmp = Path("/tmp/upload_check"); shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir()
    target = ORIGINS / YEAR / COUNTRY / EVENT
    _wipe_test_tree()

    dbf = _env.need("FAMILY_DB")
    _abort_if_real_data()
    foreign_before = _foreign(dbf)
    print("Ofnahm-Test — Upload (Etapp 4)")
    print(f"  ({foreign_before} foreign photographs in the database — those stay untouched)\n")
    src = []
    for i, dt in enumerate(("1999:07:14 11:03:22", "1999:07:15 09:41:05",
                            "1999:07:22 18:12:44"), start=1):
        p = tmp / f"IMG_{1496 + i}.JPG"
        make_photo(p, dt, i)
        src.append(p)
    junk = tmp / "net-e-bild.jpg"
    junk.write_bytes(b"this is not a photograph, just text with a .jpg extension\n")

    st, b = call("POST", "/api/upload/batch"); batch = b["batch"]; MY_BATCHES.append(batch)
    chk("Batch ugeluecht", st == 200 and batch, b)

    res = [upload(batch, p) for p in src]
    chk("three photographs accepted", all(r.get("state") == "ready" for r in res), res)
    chk("EXIF-Datum gelies", res[0].get("taken_at", "").startswith("1999-07-14")
        and res[0].get("taken_source") == "exif", res[0])
    chk("Kamera gelies", res[0].get("camera") == "Canon EOS R5 C", res[0])

    rj = upload(batch, junk)
    chk("not a photograph -> refused", rj.get("state") == "rejected", rj)

    st, prop = call("GET", f"/api/upload/{batch}/proposal")
    chk("Virschlag: Joer aus dem EXIF", prop["proposal"]["year"] == "1999", prop)
    chk("the proposal counts three files", prop["files"] == 3, prop)

    st, out = call("POST", f"/api/upload/{batch}/commit",
                   {"year": YEAR, "country": COUNTRY, "event": EVENT, "place": PLACE})
    chk("Commit ok", st == 200 and len(out.get("stored", [])) == 3, out)
    chk("no error while storing", not out.get("failed"), out.get("failed"))

    chk("the folder is the proposed path", Path(out["folder"]) == target, out.get("folder"))
    files = sorted(p for p in target.iterdir() if p.is_file())
    chk("three files in the originals tree", len(files) == 3, [p.name for p in files])

    same = 0
    for orig, dst in zip(src, files):
        if hashlib.sha256(orig.read_bytes()).hexdigest() == hashlib.sha256(dst.read_bytes()).hexdigest():
            same += 1
    chk("byte-identesch mat dem Original", same == 3, f"{same}/3")
    chk("the name was generated by the site (not the client's)",
        all(p.name.startswith("1999") and "IMG_" not in p.name for p in files),
        [p.name for p in files])
    chk("incoming ass eidel", not (INCOMING / batch).exists(), str(INCOMING / batch))

    # --- and the conversion hangs off the upload, with no second button ----
    import sqlite3, time
    dbf = _env.need("FAMILY_DB")
    web = Path(_env.need("FAMILY_WEB")) / YEAR / COUNTRY / EVENT
    t0 = time.time()
    while time.time() - t0 < 120:
        con = _env.connect(dbf)
        n = con.execute("SELECT COUNT(*) FROM photos WHERE state='ok' AND origin_path LIKE ?", (TEST_YEAR + "/%",)).fetchone()[0]
        con.close()
        if n == 3:
            break
        time.sleep(2)
    chk("the conversion starts by itself (no second button)", n == 3,
        f"{n}/3 no {time.time()-t0:.0f}s")
    masters = sorted(p.name for p in web.iterdir()) if web.is_dir() else []
    chk("three masters in the web tree", len(masters) == 3, masters)
    chk("the master mirrors the original",
        masters == sorted(p.stem + ".jpg" for p in files), masters)

    # --- Duplikat ---------------------------------------------------------
    st, b2 = call("POST", "/api/upload/batch"); batch2 = b2["batch"]; MY_BATCHES.append(batch2)
    r2 = upload(batch2, src[0])
    chk("a duplicate is recognised", bool(r2.get("duplicate_of")), r2)
    st, out2 = call("POST", f"/api/upload/{batch2}/commit",
                    {"year": YEAR, "country": COUNTRY, "event": EVENT, "place": PLACE})
    chk("a duplicate is skipped by default",
        len(out2.get("stored", [])) == 0 and len(out2.get("skipped", [])) == 1, out2)
    chk("no fourth file in the originals tree", len(list(target.iterdir())) == 3,
        [p.name for p in target.iterdir()])

    # --- Kollisioun: selwecht Zil, selwechten Numm -------------------------
    from app import library
    victim = files[0]
    before = victim.read_bytes()
    try:
        library.store_original(src[1], target, victim.name)
        chk("an existing file is NOT overwritten", False, "store_original ass duerchgaang")
    except library.LibraryError:
        chk("an existing file is NOT overwritten", True)
    chk("the content is untouched", victim.read_bytes() == before)
    try:
        library.store_original(src[1], target, victim.name.upper())
        chk("Kollisioun och ouni Grouss-/Klengschreiwung", False, "duerchgaang")
    except library.LibraryError:
        chk("Kollisioun och ouni Grouss-/Klengschreiwung", True)

    # --- The mount check --------------------------------------------------
    # ⚠ On a TEMP root, never on the live share: an earlier version deleted the
    # marker file of the real originals tree here and wrote it back afterwards
    # -- a crash in that window would have left the site fail-closed.
    fake = tmp / "fake-share"
    fake.mkdir()
    try:
        library.check_tree(fake)
        chk("without the marker file nothing is written", False, "check_tree ass duerchgaang")
    except library.LibraryError:
        chk("without the marker file nothing is written", True)

    shutil.rmtree(tmp, ignore_errors=True)
    _wipe_test_tree()
    _wipe_test_tree()
    import sqlite3
    db = _env.connect(_env.need("FAMILY_DB"))
    # The year at the end of the name always comes off -- and in BOTH trees,
    # because web_dir() runs through target_dir().
    from app import tree as _tree
    for raw_, should in [("Wedding 2016", "Wedding"),
                      ("Fragas de São Simão 2015", "Fragas de São Simão"),
                      ("Summer_2019", "Summer"), ("Trip-2020", "Trip"),
                      ("Porto", "Porto"), ("2016", "2016"),
                      ("Kanner 2016 Sommer", "Kanner 2016 Sommer")]:
        chk(f"Numm ouni Joer: {raw_!r}", _tree.strip_year(raw_) == should,
            _tree.strip_year(raw_))
    chk("both trees get the same folder",
        _tree.target_dir("2016", "Portugal", "Porto 2016").name
        == _tree.web_dir("2016", "Portugal", "Porto 2016").name == "Porto",
        f"{_tree.target_dir('2016','Portugal','Porto 2016').name} / "
        f"{_tree.web_dir('2016','Portugal','Porto 2016').name}")

    _cleanup(_env.need("FAMILY_DB"))

    # ⚠ Only downwards is an error. Photographs turning UP during a run is
    # normal: somebody copies a folder into the originals tree and a scan takes
    # it in. This used to be `==`, and then the test raised the alarm although
    # nothing had been lost.
    after_ = _foreign(_env.need("FAMILY_DB"))
    chk("no foreign photograph was lost", after_ >= foreign_before,
        f"{foreign_before} -> {after_}: the test deleted rows "
        f"that were not its own!")
    _drop_test_members()
    print(f"\n  {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.path.insert(0, _env.app_root())

    sys.exit(main())
