#!/usr/bin/env python3
"""Acceptance test for the ownership model. Runs on the server as `family`.

An account holder may upload, manage their own photographs (not the ones they
are merely tagged in) and share them. The original is thrown away after the
conversion. No other user may touch their photographs.
"""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
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

BASE = "http://127.0.0.1:8080"
SECRET = Path("/etc/family/proxy-secret").read_text().strip()
DB = os.environ.get("FAMILY_DB", "/opt/family/data/family.db")
YEAR, EVENT = "1997", "zz-owner-test"

ok = bad = 0


def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1; print(f"  ok    {name}")
    else:
        bad += 1; print(f"  FAIL  {name}  {extra}")


def hdr(user, groups):
    return {"X-Family-Proxy": SECRET, "X-authentik-username": user,
            "X-authentik-groups": groups, "X-Forwarded-For": "127.0.0.1",
            "Sec-Fetch-Site": "same-origin"}


MEMBER = hdr("zz-cleo", VIEWER_GROUP)
OTHER = hdr("zz-eve", VIEWER_GROUP)
ADMIN = hdr("siteadmin", ADMIN_GROUP)


def req(path, h, method="GET", data=None, raw=None, ctype=None):
    body = json.dumps(data).encode() if data is not None else raw
    r = urllib.request.Request(BASE + path, data=body, method=method)
    for k, v in h.items():
        r.add_header(k, v)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    elif ctype:
        r.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            b = f.read()
            try:
                return f.status, json.loads(b)
            except ValueError:
                return f.status, b
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def q(sql, *a):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    r = con.execute(sql, a).fetchall(); con.close()
    return [dict(x) for x in r]


def _wipe():
    from app import convert, config
    ids = [r["id"] for r in q("SELECT id FROM photos WHERE owner LIKE 'zz-%'")]
    con = sqlite3.connect(DB)
    if ids:
        m = ",".join("?" * len(ids))
        con.execute(f"DELETE FROM jobs WHERE payload IN ({m})", [str(i) for i in ids])
        con.execute(f"UPDATE upload_files SET photo_id=NULL WHERE photo_id IN ({m})", ids)
        con.execute(f"DELETE FROM photos WHERE id IN ({m})", ids)
    con.execute("DELETE FROM album_acl WHERE album_key LIKE ?", (YEAR + "/%",))
    con.execute("DELETE FROM album_published WHERE album_key LIKE ?", (YEAR + "/%",))
    con.execute("DELETE FROM albums WHERE title LIKE 'zz-%'")
    con.execute("DELETE FROM members WHERE username LIKE 'zz-%'")
    con.commit(); con.close()
    for i in ids:
        shutil.rmtree(convert.derivative_dir(i), ignore_errors=True)
    shutil.rmtree(Path(config.WEB_DIR) / YEAR, ignore_errors=True)
    shutil.rmtree(Path(config.ORIGIN_DIR) / YEAR, ignore_errors=True)


def _upload_as(h, name):
    """Upload one image through the chunked API. Returns the batch."""
    img = Path("/tmp/" + name)
    subprocess.run(["/opt/family/venv/bin/python3", "-c",
                    f"import pyvips;pyvips.Image.gaussnoise(1400,1000).cast('uchar')"
                    f".copy(interpretation='b-w').colourspace('srgb')"
                    f".jpegsave({str(img)!r},Q=88)"], check=True, capture_output=True)
    data = img.read_bytes()
    st, b = req("/api/upload/batch", h, "POST")
    batch = b["batch"]
    st, b = req(f"/api/upload/{batch}/file", h, "POST",
                data={"name": name, "size": len(data)})
    fid = b["file_id"]
    req(f"/api/upload/{batch}/file/{fid}/chunk?offset=0", h, "PUT", raw=data,
        ctype="application/octet-stream")
    req(f"/api/upload/{batch}/file/{fid}/done", h, "POST")
    img.unlink(missing_ok=True)
    return batch


def main():
    from app import config
    _wipe()
    print("Acceptance test — the ownership model\n")

    # -- 1. The member uploads a photograph ---------------------------------
    batch = _upload_as(MEMBER, "mine.jpg")
    st, d = req(f"/api/upload/{batch}/commit", MEMBER, "POST", data={
        "year": YEAR, "country": "Testland", "event": EVENT, "place": "Testplaz"})
    chk("the member can upload", st == 200 and len(d.get("stored", [])) == 1,
        f"{st} {d}")
    for _ in range(60):
        r = q("SELECT * FROM photos WHERE owner='zz-cleo'")
        if r and r[0]["state"] == "ok":
            break
        time.sleep(0.5)
    rows = q("SELECT * FROM photos WHERE owner='zz-cleo'")
    chk("the photograph belongs to the member", len(rows) == 1 and rows[0]["owner"] == "zz-cleo")
    mine_id = rows[0]["id"]
    chk("⚠ origin_root ass 'user' (kee NAS-Original)", rows[0]["origin_root"] == "user")
    chk("it was converted", rows[0]["state"] == "ok" and rows[0]["web_name"])
    chk("⚠ NO original in the originals tree",
        not list(Path(config.ORIGIN_DIR).glob(f"{YEAR}/**/*.jpg")),
        list(Path(config.ORIGIN_DIR).glob(f"{YEAR}/**/*")))
    chk("the master is in the web tree",
        (Path(config.WEB_DIR) / rows[0]["web_name"]).is_file())
    chk("⚠ the staged upload was thrown away",
        not list((Path(config.INCOMING_DIR) / "staged").glob("*"))
        if (Path(config.INCOMING_DIR) / "staged").is_dir() else True)
    chk("the album belongs to the member (viewing list)",
        q("SELECT principal FROM album_acl WHERE album_key=?",
          f"{YEAR}/Testland/{EVENT}") == [{"principal": "user:zz-cleo"}])

    # -- 2. Only that member (and an admin) sees the photograph -------------
    st, _ = req(f"/photos/{mine_id}/400.webp", MEMBER)
    chk("the member sees their photograph", st == 200, st)
    st, _ = req(f"/photos/{mine_id}/400.webp", OTHER)
    chk("⚠ another person does NOT see it", st == 404, st)
    st, _ = req(f"/photos/{mine_id}/400.webp", ADMIN)
    chk("the admin sees it", st == 200, st)

    # -- 3. The member manages their own, nobody else's ------------------
    st, _ = req("/api/photos/bulk", MEMBER, "POST", {"action": "hide", "ids": [mine_id]})
    chk("the member can hide their photograph", st == 200, st)
    req("/api/photos/bulk", MEMBER, "POST", {"action": "show", "ids": [mine_id]})
    friem = q("SELECT id FROM photos WHERE owner IS NULL AND state='ok' LIMIT 1")
    if friem:
        fid = friem[0]["id"]
        st, _ = req("/api/photos/bulk", MEMBER, "POST", {"action": "hide", "ids": [fid]})
        chk("⚠ the member may NOT touch somebody else's photograph", st == 403, st)
        st, _ = req("/api/photos/bulk", MEMBER, "POST", {"action": "remove", "ids": [fid]})
        chk("⚠ and cannot delete it either", st == 403, st)
    st, _ = req("/api/photos/bulk", OTHER, "POST", {"action": "remove", "ids": [mine_id]})
    chk("⚠ another person cannot delete their photograph", st == 403, st)

    # -- 4. The member shares their own collection ------------------------
    st, s = req("/api/collections", MEMBER, "POST",
                {"action": "new", "title": "zz-cleo-set", "ids": [mine_id]})
    chk("the member creates a collection", st == 200 and s.get("n") == 1, f"{st} {s}")
    setid = s["id"]
    st, sh = req("/api/shares", MEMBER, "POST",
                 {"action": "new", "collection_id": setid, "days": 7})
    chk("the member shares it as a link", st == 200 and sh.get("token"), f"{st} {sh}")

    # Another person cannot share their collection and cannot edit it
    st, _ = req("/api/collections", OTHER, "POST",
                {"action": "rename", "id": setid, "title": "geklaut"})
    chk("⚠ another person cannot rename their collection", st == 403, st)
    st, _ = req("/api/shares", OTHER, "POST",
                {"action": "new", "collection_id": setid, "days": 7})
    chk("⚠ an net deelen", st == 403, st)

    # -- 5. The member fixes the metadata of THEIR album ------------------
    st, d = req("/api/albums/audience", MEMBER, "POST", {
        "year": YEAR, "country": "Testland", "event": EVENT,
        "audience": ["user:zz-cleo", "user:zz-eve"]})
    chk("the member sets the viewing list of their album", st == 200, f"{st} {d}")
    st, _ = req(f"/photos/{mine_id}/400.webp", OTHER)
    chk("now the other person sees it (let in)", st == 200, st)

    _wipe()
    print(f"\n  {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
