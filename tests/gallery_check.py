#!/usr/bin/env python3
"""Acceptance test for the gallery. Runs on the server as the user `family`."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
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
SECRET = Path("/etc/family/proxy-secret").read_text().strip()
ADMIN = {"X-Family-Proxy": SECRET, "X-authentik-username": "siteadmin",
         "X-authentik-groups": ADMIN_GROUP}
FAMILY = {"X-Family-Proxy": SECRET, "X-authentik-username": "zz-test-viewer",
          "X-authentik-groups": VIEWER_GROUP}
ORIGINS = Path(_env.need("FAMILY_ORIGINS"))
WEB = Path(_env.need("FAMILY_WEB"))
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
    con = sqlite3.connect(dbf)
    n = con.execute("SELECT COUNT(*) FROM photos WHERE origin_path NOT LIKE ?",
                    (TEST_YEAR + "/%",)).fetchone()[0]
    con.close()
    return n


def _cleanup(dbf):
    """Remove only what the test created."""
    import sqlite3
    con = sqlite3.connect(dbf)
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
    con = sqlite3.connect(_env.need("FAMILY_DB"))
    n = con.execute("SELECT COUNT(*) FROM photos WHERE origin_path LIKE ? "
                    "AND origin_path NOT LIKE ?",
                    (TEST_YEAR + "/%", f"{TEST_YEAR}/{TEST_COUNTRY}/%")).fetchone()[0]
    con.close()
    if n:
        raise SystemExit(f"OFGEBRACH: {n} echt Fotoen am Joer {TEST_YEAR}")


def _open_for_tests():
    """⚠ The rule is: no list = **the admin only**. So the test has to open its
    own album to its own viewer, or that viewer sees nothing and half the
    checks stop meaning anything."""
    import sqlite3 as _s
    con = _s.connect(_env.need("FAMILY_DB"))
    con.execute("INSERT OR IGNORE INTO album_acl (album_key, principal) VALUES (?,?)",
                (f"{YEAR}/{COUNTRY}/{EVENT}", "user:zz-test-viewer"))
    con.commit(); con.close()


def _drop_test_acl():
    import sqlite3 as _s
    con = _s.connect(_env.need("FAMILY_DB"))
    con.execute("DELETE FROM album_acl WHERE album_key LIKE ?", (TEST_YEAR + "/%",))
    con.commit(); con.close()


def _wipe_test_tree():
    """ONLY our own sub-folder -- never a whole year."""
    import shutil as _sh
    _sh.rmtree(ORIGINS / TEST_YEAR / TEST_COUNTRY, ignore_errors=True)
    _sh.rmtree(Path(_env.need("FAMILY_WEB"))
               / TEST_YEAR / TEST_COUNTRY, ignore_errors=True)

ok = bad = 0


def chk(what, cond, detail=""):
    global ok, bad
    if cond: ok += 1; print(f"  ok    {what}")
    else: bad += 1; print(f"  FAIL  {what}  {detail}")


def req(path, hdr=ADMIN, method="GET", data=None, ctype="application/json", raw=None):
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    r = urllib.request.Request(BASE + path, data=body, method=method,
                               headers={**hdr, "Content-Type": ctype})
    try:
        resp = urllib.request.urlopen(r, timeout=90)
        return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers



def _drop_test_members():
    """⚠ An account the test invented through the header is noted by
    `members.note_seen()` -- and afterwards it stands on the settings page
    among the family. That has happened: a made-up name was ticked onto a
    viewing list because it was sitting there. A test must not invent a person
    who then looks real."""
    import sqlite3 as _s
    con = _s.connect(_env.need("FAMILY_DB"))
    con.execute("DELETE FROM members WHERE username LIKE 'zz-test-%' "
                "AND seen_in_authentik=0")
    con.commit(); con.close()


def main():
    dbf = _env.need("FAMILY_DB")
    _abort_if_real_data()
    foreign_before = _foreign(dbf)
    print("Ofnahm-Test — Gallerie (Etapp 6)")
    print(f"  ({foreign_before} foreign photographs in the database — those stay untouched)\n")
    tmp = Path("/tmp/gallery_check"); shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir()
    _wipe_test_tree()
    _wipe_test_tree()
    import sqlite3
    dbf = _env.need("FAMILY_DB")
    con = sqlite3.connect(dbf)
    _cleanup(dbf)

    from PIL import Image
    files = []
    for i, dt in enumerate(("1999:07:14 11:03:22", "1999:07:15 09:41:05",
                            "1999:07:16 14:20:10"), start=1):
        p = tmp / f"IMG_{i}.JPG"
        Image.new("RGB", (2400, 1600), (30 + i * 30, 80, 120)).save(p, "JPEG", quality=90)
        subprocess.run(["exiftool", "-overwrite_original", "-q", f"-DateTimeOriginal={dt}",
                        "-Make=Canon", "-Model=Canon EOS R5 C", "--", str(p)],
                       check=True, capture_output=True)
        files.append(p)

    st, b, _ = req("/api/upload/batch", method="POST")
    batch = json.loads(b)["batch"]; MY_BATCHES.append(batch)
    for f in files:
        st, b, _ = req(f"/api/upload/{batch}/file", method="POST",
                       data={"name": f.name, "size": f.stat().st_size})
        fid = json.loads(b)["file_id"]
        req(f"/api/upload/{batch}/file/{fid}/chunk?offset=0", method="PUT",
            raw=f.read_bytes(), ctype="application/octet-stream")
        req(f"/api/upload/{batch}/file/{fid}/done", method="POST")
    req(f"/api/upload/{batch}/commit", method="POST",
        data={"year": YEAR, "country": COUNTRY, "event": EVENT, "place": PLACE})

    t0 = time.time(); n = 0
    while time.time() - t0 < 120:
        con = sqlite3.connect(dbf)
        n = con.execute("SELECT COUNT(*) FROM photos WHERE state='ok' AND origin_path LIKE ?", (TEST_YEAR + "/%",)).fetchone()[0]
        con.close()
        if n == 3: break
        time.sleep(2)
    chk("three photographs converted", n == 3, n)
    _open_for_tests()


    st, body, _ = req("/", hdr=FAMILY)
    html = body.decode()
    chk("the front page opens", st == 200 and "<html" in html, st)
    chk("the year is on the front page", ">" + YEAR + "<" in html or "/y/" + YEAR in html)
    shown = YEAR + " " + EVENT.replace(YEAR, "").strip()
    chk("the album is on it, with the title built from the structure", shown in html,
        "erwaart: " + shown)
    chk("a cover photograph on the front page", 'class="hero"' in html)
    chk("the album index on the front page", 'class="index__row"' in html)
    chk("noindex is in the page", 'content="noindex' in html)

    ev_url = f"/y/{YEAR}/{urllib.parse.quote(COUNTRY)}/{urllib.parse.quote(EVENT)}"
    st, body, _ = req(ev_url, hdr=FAMILY)
    html = body.decode()
    ids = re.findall(r'data-lightbox="(\d+)"', html)
    chk("the album page shows three plates", st == 200 and len(ids) == 3, len(ids))
    chk("AVIF an WebP am Markup", "/1200.avif" in html and "/1200.webp" in html)
    chk("the album list is on the page", 'class="drawer"' in html)
    chk("the album page has a header with the plate count", 'class="plate"' in html
        and 'dest-head' in html)
    chk("d'L\u00ebscht l\u00e9isst sech op- an zoumaachen", 'id="drawer-open"' in html)

    pid = ids[0]
    st, body, hdr = req(f"/photos/{pid}/400.avif", hdr=FAMILY)
    chk("a thumbnail comes out (AVIF)", st == 200 and len(body) > 500, f"{st} {len(body)}B")
    cc = hdr.get("Cache-Control", "")
    # `private` is compulsory -- it keeps the photograph in the family member's
    # browser and out of ANY shared cache. `public` would be a mistake nobody
    # sees on the site. The age may be long, because the address carries ?v=<rev>.
    chk("the image is private, not public", cc.startswith("private,") and "public" not in cc, cc)
    chk("Cloudflare kritt no-store",
        (hdr.get("CDN-Cache-Control") or "") == "no-store"
        and (hdr.get("Cloudflare-CDN-Cache-Control") or "") == "no-store",
        f"{hdr.get('CDN-Cache-Control')} / {hdr.get('Cloudflare-CDN-Cache-Control')}")
    chk("the image carries noindex", "noindex" in (hdr.get("X-Robots-Tag") or ""))

    st, body, _ = req(f"/photos/{pid}/2000.webp", hdr=FAMILY)
    chk("2000 px is served", st == 200 and len(body) > 5000, f"{st} {len(body)}B")

    # ⚠ The web sizes have to be there ALREADY, before anybody opens the page.
    # An earlier version built only the 400 one during the conversion, and the
    # gallery asks for 1200 and 2000 -- so every photograph cost a second of
    # computing on the first look, sixty per page. That was the entire reason
    # the site was slow.
    sys.path.insert(0, "/opt/family/app")
   # this check looks on the disk
    from app import convert as _cv, config as _cfg
    d = _cv.derivative_dir(int(pid))
    # A width larger than the master is deliberately NOT built
    # (images.build_derivatives: `if w > img.width: continue`) -- otherwise an
    # image would be scaled up.
    import sqlite3 as _s3
    _c = _s3.connect(dbf); _mw = _c.execute("SELECT width FROM photos WHERE id=?", (pid,)).fetchone()[0]; _c.close()
    missing_ = [f"{w}.{e}" for w in _cfg.DERIVATIVE_WIDTHS if w <= (_mw or 0)
              for e in ("avif", "webp") if not (d / f"{w}.{e}").is_file()]
    chk("every web size is computed in advance", not missing_, "feelen: " + ", ".join(missing_))

    st, body, _ = req(f"/photos/{pid}/master.jpg", hdr=FAMILY)
    chk("Master kann erofgelueden ginn", st == 200 and len(body) > 20000, st)

    st, body, _ = req(f"/api/photo/{pid}", hdr=FAMILY)
    d = json.loads(body)
    chk("Metadaten an der API", d.get("camera") == "Canon EOS R5 C", d.get("camera"))
    chk("the original's path is NOT handed out",
        "origin_path" not in d and "master_source_path" not in d, sorted(d)[:6])
    chk("the LQIP is included", (d.get("lqip") or "").startswith("data:image/jpeg;base64,"))

    st, body, _ = req("/search?q=" + urllib.parse.quote(PLACE), hdr=FAMILY)
    chk("searching for the place finds three", st == 200 and
        len(re.findall(r'data-lightbox="(\d+)"', body.decode())) == 3)
    st, body, _ = req("/search?q=" + urllib.parse.quote("doesnotexist"), hdr=FAMILY)
    chk("a search with no hits brings no plates",
        len(re.findall(r'data-lightbox="(\d+)"', body.decode())) == 0)

    # ⚠ Under the ownership model an account holder MAY upload -- that is the
    # feature. Somebody with no account (not even a viewer) stays out.
    st, _, _ = req("/admin/upload", hdr=FAMILY)
    chk("an account holder reaches the upload page", st == 200, st)
    st, body, _ = req("/admin/upload", hdr=ADMIN)
    html = body.decode()
    chk("the admin reaches the upload page", st == 200, st)
    # The order is part of the question: the fields first, the photographs after.
    chk("d'Felder stinn VIRUN der Drop-Zone",
        html.index("Where do these belong") < html.index("Add the photos"),
        "Drop-Zone steet virun de Felder")
    chk("the drop zone starts locked", 'class="drop is-locked"' in html, "net gespaart")
    chk("four pickers, no hidden datalist",
        html.count('data-combo=') == 4 and "<datalist" not in html,
        html.count('data-combo='))
    chk("the fonts are self-hosted (no external requests)",
        "fonts.googleapis" not in html and "fonts.gstatic" not in html)
    for f in ("year", "country", "event", "place"):
        chk(f"Feld '{f}' ass do", f'id="{f}"' in html)

    st, _, _ = req("/admin", hdr=FAMILY)
    chk("a plain viewer does NOT reach /admin", st == 403, st)
    st, body, _ = req("/admin", hdr=ADMIN)
    chk("/admin geet op (kee 404)", st == 200, st)
    ah = body.decode()
    # ⚠ Do not depend on the words -- they have changed already ("Queue" became
    #   "in the queue" when the table turned into boxes). What is checked is
    #   WHAT is there: both trees, and the count of what is on the site.
    chk("/admin weist den Zoustand",
        "Originals" in ah and "Masters" in ah
        and "mounted" in ah and 'class="tile' in ah, ah[:0])
    chk("/admin verlinkt den Upload", "/admin/upload" in ah)

    # --- De Register -------------------------------------------------------
    reg = f"/admin/album/{YEAR}/{urllib.parse.quote(COUNTRY)}/{urllib.parse.quote(EVENT)}"
    st, body, _ = req(reg, hdr=ADMIN)
    chk("Register geet op", st == 200 and "reg-item" in body.decode(), st)
    st, _, _ = req(reg, hdr=FAMILY)
    chk("an account holder reaches the register (sees their own)", st == 200, st)

    import sqlite3 as _sq
    con = _sq.connect(dbf)
    pid, opath, w, h = con.execute(
        "SELECT id, origin_path, width, height FROM photos WHERE origin_path LIKE ? "
        "ORDER BY id LIMIT 1", (TEST_YEAR + "/%",)).fetchone()
    con.close()
    orig = ORIGINS / opath
    orig_before = orig.read_bytes()

    req("/api/photos/bulk", hdr=ADMIN, method="POST", data={"action": "hide", "ids": [pid]})
    con = _sq.connect(dbf); hid = con.execute("SELECT hidden FROM photos WHERE id=?", (pid,)).fetchone()[0]; con.close()
    chk("verstoppen wierkt", hid == 1, hid)
    st, body, _ = req(f"/y/{YEAR}/{urllib.parse.quote(COUNTRY)}/{urllib.parse.quote(EVENT)}", hdr=FAMILY)
    chk("a hidden photograph is no longer in the gallery",
        f'data-lightbox="{pid}"' not in body.decode())
    # ⚠ This test should have found the bug: a hidden photograph was still
    # handed out through /photos/<id>/..., because the cache was asked before
    # the database. The ids are a sequence -- so they can be found by counting.
    code, _, _ = req(f"/photos/{pid}/400.webp", hdr=FAMILY)
    chk("a hidden photograph does not come out directly either", code == 404, code)
    code, _, _ = req(f"/photos/{pid}/master.jpg", hdr=FAMILY)
    chk("a hire Master och net", code == 404, code)
    code, _, _ = req(f"/photos/{pid}/400.webp", hdr=ADMIN)
    chk("but the admin sees it (the register needs that)", code == 200, code)
    req("/api/photos/bulk", hdr=ADMIN, method="POST", data={"action": "show", "ids": [pid]})

    req("/api/photos/bulk", hdr=ADMIN, method="POST", data={"action": "rotate_right", "ids": [pid]})
    con = _sq.connect(dbf); w2, h2 = con.execute("SELECT width, height FROM photos WHERE id=?", (pid,)).fetchone(); con.close()
    chk("rotating rotates", (w2, h2) == (h, w), f"{w}x{h} -> {w2}x{h2}")
    chk("the original is untouched by a rotation", orig.read_bytes() == orig_before)
    req("/api/photos/bulk", hdr=ADMIN, method="POST", data={"action": "rotate_left", "ids": [pid]})

    req("/api/photos/bulk", hdr=ADMIN, method="POST", data={"action": "remove", "ids": [pid]})
    con = _sq.connect(dbf)
    gone = con.execute("SELECT COUNT(*) FROM photos WHERE id=?", (pid,)).fetchone()[0]
    con.close()
    chk("vum Site geholl", gone == 0, gone)
    chk("⚠ the ORIGINAL is still there", orig.is_file() and orig.read_bytes() == orig_before,
        str(orig))

    # Somebody in a group the site does not know does not get in at all.
    stranger = {"X-Family-Proxy": SECRET, "X-authentik-username": "zz-test-friem",
                "X-authentik-groups": "Iergendeng-Grupp"}
    code, _, _ = req("/", hdr=stranger)
    chk("eng friem Grupp kritt 403", code == 403, code)

    st, body, _ = req("/static/site.css", hdr=FAMILY)
    css = body.decode()
    chk("the CSS is served", st == 200, st)
    chk("the font files are included", "@font-face" in css and "/static/fonts/" in css)
    chk("no external font requests in the CSS", "googleapis" not in css and "gstatic" not in css)
    for f in ("bodoni-moda-500.woff2", "spectral-300.woff2", "ibm-plex-mono-400.woff2"):
        st2, b2, _ = req("/static/fonts/" + f, hdr=FAMILY)
        chk(f"font {f} is served", st2 == 200 and len(b2) > 5000, f"{st2} {len(b2)}B")

    shutil.rmtree(tmp, ignore_errors=True)
    _wipe_test_tree()
    _wipe_test_tree()
    con = sqlite3.connect(dbf)
    _cleanup(dbf)
    # ⚠ Only downwards is an error. Photographs turning UP during a run is
    # normal: somebody copies a folder into the originals tree and a scan takes
    # it in. This used to be `==`, and then the test raised the alarm although
    # nothing had been lost.
    after_ = _foreign(_env.need("FAMILY_DB"))
    chk("no foreign photograph was lost", after_ >= foreign_before,
        f"{foreign_before} -> {after_}: the test deleted rows "
        f"that were not its own!")
    _drop_test_acl()
    _drop_test_members()
    print(f"\n  {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
