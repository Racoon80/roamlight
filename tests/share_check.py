#!/usr/bin/env python3
"""Acceptance test for the share links. Runs on the server as `family`.

This is the only place on the site where something goes out **without any
sign-in**. So it does not only check that it works, but above all **what must
not get through**: somebody else's photograph, an expired address, the site
itself, the GPS data, and brute force.
"""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.cookiejar import CookieJar, DefaultCookiePolicy


class _Policy(DefaultCookiePolicy):
    """⚠ The site's cookie is `Secure` -- and rightly so: it travels over https.
    The test, however, talks straight to 127.0.0.1:8080, that is over http, and
    an ordinary `CookieJar` would quietly throw it away. That would be a fault
    in the test, not on the site."""
    def return_ok_secure(self, cookie, request):
        return True


def jar_op():
    return CookieJar(_Policy())
from pathlib import Path

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

sys.path.insert(0, "/opt/family/app")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env                                              # noqa: E402


BASE = "http://127.0.0.1:8080"
SECRET = Path("/etc/family/proxy-secret").read_text().strip()
ADMIN = {"X-Family-Proxy": SECRET, "X-authentik-username": "siteadmin",
         "X-authentik-groups": ADMIN_GROUP}
GAAST = {"X-Family-Proxy": SECRET}          # KEEN Authentik-Header
DB = _env.need("FAMILY_DB")
VIR = "zz-share-"

ok = bad = 0


def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1; print(f"  ok    {name}")
    else:
        bad += 1; print(f"  FAIL  {name}  {extra}")


def req(path, hdr, method="GET", data=None, form=None, jar=None, body=None,
        ctype=None):
    if data is not None:
        raw, ctype = json.dumps(data).encode(), "application/json"
    elif form is not None:
        raw, ctype = urllib.parse.urlencode(form).encode(), \
            "application/x-www-form-urlencoded"
    else:
        raw = body
    r = urllib.request.Request(BASE + path, data=raw, method=method)
    for k, v in hdr.items():
        r.add_header(k, v)
    if ctype:
        r.add_header("Content-Type", ctype)
    if data is not None:
        r.add_header("Sec-Fetch-Site", "same-origin")
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar) if jar is not None
        else urllib.request.BaseHandler(),
        _NoRedirect())
    try:
        with op.open(r, timeout=60) as f:
            return f.status, f.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def q(sql, *a):
    con = _env.connect(DB); con.row_factory = sqlite3.Row
    r = con.execute(sql, a).fetchall(); con.close()
    return [dict(x) for x in r]


def _wipe():
    con = _env.connect(DB)
    for r in con.execute("SELECT token FROM shares WHERE album_id IN "
                         "(SELECT id FROM albums WHERE title LIKE ?)", (VIR + "%",)):
        shutil.rmtree(Path("/opt/family/incoming") / f"share-{r[0]}", ignore_errors=True)
    con.execute("DELETE FROM share_uploads WHERE share_id IN (SELECT id FROM shares "
                "WHERE album_id IN (SELECT id FROM albums WHERE title LIKE ?))", (VIR + "%",))
    con.execute("DELETE FROM shares WHERE album_id IN "
                "(SELECT id FROM albums WHERE title LIKE ?)", (VIR + "%",))
    con.execute("DELETE FROM album_photos WHERE album_id IN "
                "(SELECT id FROM albums WHERE title LIKE ?)", (VIR + "%",))
    con.execute("DELETE FROM albums WHERE title LIKE ?", (VIR + "%",))
    con.commit(); con.close()


def multipart(felder, fpath):
    grenz = "----" + uuid.uuid4().hex
    buf = io.BytesIO()
    for k, v in felder.items():
        buf.write(f"--{grenz}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                  f"{v}\r\n".encode())
    fname, inhalt, typ = fpath
    buf.write(f"--{grenz}\r\nContent-Disposition: form-data; name=\"file\"; "
              f"filename=\"{fname}\"\r\nContent-Type: {typ}\r\n\r\n".encode())
    buf.write(inhalt); buf.write(f"\r\n--{grenz}--\r\n".encode())
    return buf.getvalue(), f"multipart/form-data; boundary={grenz}"


def main():
    from app import collections, config, shares
    photos_before = q("SELECT COUNT(*) n FROM photos")[0]["n"]
    print("Ofnahm-Test — Deel-Links (Etapp 10)")
    print(f"  ({photos_before} photographs in the database — those stay untouched)\n")
    _wipe()

    ids = [r["id"] for r in q("SELECT id FROM photos WHERE state='ok' AND hidden=0 "
                              "ORDER BY id LIMIT 3")]
    if len(ids) < 3:
        raise SystemExit("ABORTED: too few photographs")
    st = collections.create(VIR + "album", ids)
    friem = q("SELECT id FROM photos WHERE state='ok' AND id NOT IN (%s) LIMIT 1"
              % ",".join(map(str, ids)))[0]["id"]

    # -- 1. Create ----------------------------------------------------------
    code, body = req("/api/shares", ADMIN, "POST", {
        "action": "new", "collection_id": st["id"], "days": 30})
    d = json.loads(body) if code == 200 else {}
    chk("create a link", code == 200 and d.get("token"), f"{code} {body[:120]}")
    tok, pw = d["token"], d["password"]
    chk("the password has 4 words and 3 digits",
        len(pw.split("-")) == 5 and pw.split("-")[-1].isdigit(), pw)
    chk("⚠ only the hash is in the database",
        pw not in q("SELECT password_hash h FROM shares WHERE token=?", tok)[0]["h"])
    chk("the hash is argon2id",
        q("SELECT password_hash h FROM shares WHERE token=?", tok)[0]["h"]
        .startswith("$argon2id$"))
    code, _ = req("/api/shares", ADMIN, "POST", {"action": "new",
                                                 "collection_id": st["id"], "days": 9999})
    chk("no link lasts longer than a year", code == 400, code)

    # -- 2. Without the password nothing comes out ---------------------------
    code, body = req(f"/s/{tok}", GAAST)
    chk("the page asks for a password", code == 200 and b"password" in body.lower(), code)
    chk("⚠ a weist keng Foto", b"/img/" not in body)
    code, _ = req(f"/s/{tok}/img/{ids[0]}/400.webp", GAAST)
    chk("⚠ an image without the password is a 404", code == 404, code)
    code, _ = req(f"/s/{tok}/img/{ids[0]}/master.jpg", GAAST)
    chk("⚠ the master without the password is a 404", code == 404, code)

    # -- 3. Mat Passwuert -----------------------------------------------------
    jar = jar_op()
    code, _ = req(f"/s/{tok}", GAAST, "POST", form={"password": pw}, jar=jar)
    chk("mam richtege Passwuert eran", code == 303, code)
    code, body = req(f"/s/{tok}", GAAST, jar=jar)
    chk("d'Sammlung geet op", code == 200 and b"/img/" in body, code)
    for pid in ids:
        code, _ = req(f"/s/{tok}/img/{pid}/400.webp", GAAST, jar=jar)
        if code != 200:
            break
    chk("every photograph of the collection comes out", code == 200, code)

    # -- 4. What must NOT get through ----------------------------------------
    code, _ = req(f"/s/{tok}/img/{friem}/400.webp", GAAST, jar=jar)
    chk("⚠ a photograph from ANOTHER collection is a 404 (IDOR)", code == 404, code)
    code, _ = req("/", GAAST, jar=jar)
    chk("⚠ the site itself stays shut", code == 403, code)
    code, _ = req("/api/photos", GAAST, jar=jar)
    chk("⚠ an d'API och", code == 403, code)
    code, body = req(f"/s/{tok}", GAAST, jar=jar)
    chk("⚠ the share page shows none of the site's navigation",
        b'id="drawer"' not in body and b"/admin" not in body)

    # -- 5. One link's cookie does not count for another --------------------
    code, body = req("/api/shares", ADMIN, "POST", {
        "action": "new", "collection_id": st["id"], "days": 7})
    d2 = json.loads(body)
    code, _ = req(f"/s/{d2['token']}/img/{ids[0]}/400.webp", GAAST, jar=jar)
    chk("⚠ one link's cookie does not count for another", code == 404, code)

    # -- 6. Downloading: the master, and without GPS -------------------------
    mit_gps = q("SELECT id FROM photos WHERE state='ok' AND gps_lat IS NOT NULL LIMIT 1")
    code, body = req(f"/s/{tok}/img/{ids[0]}/master.jpg", GAAST, jar=jar)
    chk("the master comes out", code == 200 and len(body) > 100000, f"{code} {len(body)}")
    if mit_gps:
        gid = mit_gps[0]["id"]
        collections.add(st["id"], [gid])
        code, body = req(f"/s/{tok}/img/{gid}/master.jpg", GAAST, jar=jar)
        p = Path("/tmp/share_gps.jpg"); p.write_bytes(body)
        # ⚠ With `-a -G1`: the camera serial number sits in the MakerNotes and
        #   does not show up without `-a`. That is exactly the one that was
        #   still in there, and the security review found it.
        out_ = subprocess.run(["exiftool", "-s", "-s", "-s", "-a", "-n",
                              "-*GPS*", "-*Location*", "-SerialNumber",
                              "-CameraSerialNumber", "-InternalSerialNumber",
                              "-OwnerName", "-Artist", "-By-line", "-Creator",
                              "--", str(p)],
                             capture_output=True, text=True).stdout.strip()
        chk("⚠ GPS, serial number and owner name are out of the download",
            out_ == "", out_[:160])
        bleift = subprocess.run(["exiftool", "-s", "-s", "-s", "-Model",
                                 "-DateTimeOriginal", "--", str(p)],
                                capture_output=True, text=True).stdout.strip()
        chk("but the camera and the date stay", bool(bleift), bleift[:80])
        p.unlink(missing_ok=True)
    else:
        print("  --    no photograph with GPS here — the strip test is skipped")

    # -- 7. D'Bremse -----------------------------------------------------------
    code, body = req("/api/shares", ADMIN, "POST", {
        "action": "new", "collection_id": st["id"], "days": 7})
    d3 = json.loads(body)
    letzt = 0
    for i in range(shares.FAIL_LOCK):
        letzt, _ = req(f"/s/{d3['token']}", GAAST, "POST", form={"password": "kabes"})
    chk("⚠ after five wrong tries the link is shut", letzt == 429, letzt)
    code, _ = req(f"/s/{d3['token']}", GAAST, "POST",
                  form={"password": d3["password"]})
    chk("⚠ and then even the right password does not help", code == 429, code)
    con = _env.connect(DB)
    con.execute("UPDATE shares SET locked_until=NULL, fail_count=0 WHERE token=?",
                (d3["token"],)); con.commit(); con.close()
    code, _ = req(f"/s/{d3['token']}", GAAST, "POST", form={"password": d3["password"]})
    chk("after the lock it works again", code == 303, code)

    # -- 8. Ofgelaf heescht ofgelaf --------------------------------------------
    con = _env.connect(DB)
    con.execute("UPDATE shares SET expires_at='2000-01-01 00:00:00' WHERE token=?",
                (d3["token"],)); con.commit(); con.close()
    jar3 = jar_op()
    code, body = req(f"/s/{d3['token']}", GAAST, jar=jar3)
    chk("an expired page says so", code == 410, code)
    chk("⚠ and it gives away neither title nor photograph",
        (VIR + "album").encode() not in body and b"/img/" not in body)
    code, _ = req(f"/s/{d3['token']}/img/{ids[0]}/400.webp", GAAST, jar=jar)
    chk("⚠ and the images are shut too", code == 404, code)

    # -- 9. Ofdreiwen -----------------------------------------------------------
    sid = q("SELECT id FROM shares WHERE token=?", tok)[0]["id"]
    code, _ = req("/api/shares", ADMIN, "POST", {"action": "revoke", "id": sid})
    chk("ofdreiwen", code == 200, code)
    code, _ = req(f"/s/{tok}/img/{ids[0]}/400.webp", GAAST, jar=jar)
    chk("⚠ and the cookie does not help afterwards", code == 404, code)

    # -- 10. Guest upload --------------------------------------------------------
    code, body = req("/api/shares", ADMIN, "POST", {
        "action": "new", "collection_id": st["id"], "days": 7, "allow_upload": True})
    d4 = json.loads(body)
    jar4 = jar_op()
    req(f"/s/{d4['token']}", GAAST, "POST", form={"password": d4["password"]}, jar=jar4)

    bild = Path("/tmp/gast.jpg")
    subprocess.run(["/opt/family/venv/bin/python3", "-c",
                    "import pyvips;pyvips.Image.gaussnoise(800,600).cast('uchar')"
                    ".copy(interpretation='b-w').colourspace('srgb')"
                    f".jpegsave({str(bild)!r},Q=85)"], check=True, capture_output=True)

    raw, ct = multipart({"guest": "Marc"}, ("meng foto.jpg", bild.read_bytes(), "image/jpeg"))
    code, body = req(f"/s/{d4['token']}/upload", GAAST, "POST", body=raw, ctype=ct, jar=jar4)
    chk("e Gaascht ka lueden", code == 200, f"{code} {body[:150]}")
    stored = json.loads(body)["stored"] if code == 200 else ""
    chk("⚠ the guest's name does NOT reach the file system",
        "meng foto" not in stored and stored.endswith(".jpg"), stored)
    quar = Path(config.INCOMING_DIR) / f"share-{d4['token']}"
    chk("⚠ it lies in quarantine, not in the library",
        (quar / stored).is_file()
        and not list(Path(config.ORIGIN_DIR).rglob(stored)), stored)
    # ⚠ What is counted is the GUEST FILE, not the total number of photographs.
    #   Somebody uploads in parallel, and a total that goes up then means "they
    #   are working", not "something slipped through". A check that fires
    #   during normal work gets ignored -- and then it never helps at all.
    chk("⚠ and it is NOT on the site",
        q("SELECT COUNT(*) n FROM photos WHERE origin_path LIKE ?",
          "%" + stored)[0]["n"] == 0 if stored else False,
        "a guest photograph slipped into the library!")

    # Anything that is not an image does not get in
    raw, ct = multipart({"guest": "Marc"},
                        ("boes.jpg", b"#!/bin/sh\necho pwned\n", "image/jpeg"))
    code, body = req(f"/s/{d4['token']}/upload", GAAST, "POST", body=raw, ctype=ct, jar=jar4)
    chk("⚠ a file that is not an image is refused", code == 400, f"{code} {body[:100]}")
    chk("⚠ and it is not on the disk either",
        len(list(quar.glob("*"))) == 1, [p.name for p in quar.glob("*")])

    # Ouni Passwuert kee Upload
    raw, ct = multipart({"guest": "X"}, ("a.jpg", bild.read_bytes(), "image/jpeg"))
    code, _ = req(f"/s/{d4['token']}/upload", GAAST, "POST", body=raw, ctype=ct)
    chk("⚠ ouni Passwuert kee Upload", code == 404, code)

    # Throw it out -- and the file is gone
    gid_ = q("SELECT id FROM share_uploads WHERE state='guest'")
    code, _ = req("/api/shares", ADMIN, "POST", {
        "action": "reject", "ids": [g["id"] for g in gid_]})
    chk("the admin can throw it out", code == 200, code)
    chk("and the file is gone", not (quar / stored).exists())

    bild.unlink(missing_ok=True)
    _wipe()
    danach = q("SELECT COUNT(*) n FROM photos")[0]["n"]
    # Only downwards is an error -- photographs arrive while people work.
    chk("keng Foto verluer", danach >= photos_before,
        f"{photos_before} -> {danach}")
    print(f"\n  {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
