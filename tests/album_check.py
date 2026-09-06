#!/usr/bin/env python3
"""Acceptance test for the album workshop. Runs on the server as `family`.

The path it checks is the real one:
   1. A folder is copied STRAIGHT into the originals tree -- not in our shape.
   2. The scan takes it in.
   3. In the admin pages the year, country, name and place are put right.
   4. Both trees, the database, the album and the map have to follow.
"""
import json
import os
import shutil
import subprocess
import sys
import sqlite3
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

BASE = "http://127.0.0.1:8080"
SECRET = Path("/etc/family/proxy-secret").read_text().strip()
ADMIN = {"X-Family-Proxy": SECRET, "X-authentik-username": "siteadmin",
         "X-authentik-groups": ADMIN_GROUP}
ORIGINS = Path(os.environ.get("FAMILY_ORIGINS", "/mnt/my-photos"))
WEB = Path(os.environ.get("FAMILY_WEB", "/mnt/family-website"))
DB = os.environ.get("FAMILY_DB", "/opt/family/data/family.db")

# ---------------------------------------------------------------------------
#  Runs against the LIVE database and the LIVE originals tree -- the same rules
#  as the other tests: only under a year that does not exist, only removing what
#  the test created itself, and counting at the end whether any photograph that
#  is not ours has disappeared.
# ---------------------------------------------------------------------------
TEST_YEAR = "1999"
TEST_COUNTRY = "Testland"
# The albums the test creates -- in EVERY shape they take along the way.
MEng = [(TEST_YEAR, TEST_COUNTRY, "Copied straight in"),
        (TEST_YEAR, TEST_COUNTRY, "Geriicht"),
        ("1998", TEST_COUNTRY, "Geriicht")]

ok = bad = 0


def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1; print(f"  ok    {name}")
    else:
        bad += 1; print(f"  FAIL  {name}  {extra}")


def req(path, hdr=None, method="GET", data=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, method=method)
    for k, v in (hdr or {}).items():
        r.add_header(k, v)
    if body:
        r.add_header("Content-Type", "application/json")
        r.add_header("Sec-Fetch-Site", "same-origin")
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            return f.status, f.read(), dict(f.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _foreign():
    con = sqlite3.connect(DB)
    n = con.execute("SELECT COUNT(*) FROM photos WHERE origin_path NOT LIKE ? "
                    "AND origin_path NOT LIKE ?",
                    (TEST_YEAR + "/%", "1998/%")).fetchone()[0]
    con.close()
    return n


def _abort_if_real_data():
    for jar in (TEST_YEAR, "1998"):
        root = ORIGINS / jar
        def alien(p):
            try:
                return p.relative_to(root).parts[0] != TEST_COUNTRY
            except ValueError:
                return True
        if root.exists() and any(p for p in root.rglob("*") if p.is_file() and alien(p)):
            raise SystemExit(f"ABORTED: {root} holds files that are not the test's")
    con = sqlite3.connect(DB)
    n = con.execute(
        "SELECT COUNT(*) FROM photos WHERE (origin_path LIKE ? OR origin_path LIKE ?) "
        "AND origin_path NOT LIKE ? AND origin_path NOT LIKE ?",
        (TEST_YEAR + "/%", "1998/%",
         f"{TEST_YEAR}/{TEST_COUNTRY}/%", f"1998/{TEST_COUNTRY}/%")).fetchone()[0]
    con.close()
    if n:
        raise SystemExit(f"ABORTED: {n} real photographs in the years 1998/1999")


def _wipe():
    for jar in (TEST_YEAR, "1998"):
        for root in (ORIGINS, WEB):
            shutil.rmtree(root / jar / TEST_COUNTRY, ignore_errors=True)
            try:
                (root / jar).rmdir()
            except OSError:
                pass
    con = sqlite3.connect(DB)
    ids = [r[0] for r in con.execute(
        "SELECT id FROM photos WHERE origin_path LIKE ? OR origin_path LIKE ?",
        (TEST_YEAR + "/%", "1998/%"))]
    if ids:
        q = ",".join("?" * len(ids))
        con.execute(f"DELETE FROM jobs WHERE kind='convert' AND payload IN ({q})",
                    [str(i) for i in ids])
        con.execute(f"DELETE FROM upload_files WHERE photo_id IN ({q})", ids)
        con.execute(f"DELETE FROM photos WHERE id IN ({q})", ids)
    con.execute("DELETE FROM removed WHERE origin_path LIKE ? OR origin_path LIKE ?",
                (TEST_YEAR + "/%", "1998/%"))
    con.commit(); con.close()
    from app import convert as _cv
    for i in ids:
        shutil.rmtree(_cv.derivative_dir(i), ignore_errors=True)


def make(path: Path, w, h, when):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["/opt/family/venv/bin/python3", "-c",
                    f"import pyvips;pyvips.Image.gaussnoise({w},{h}).cast('uchar')"
                    f".copy(interpretation='b-w').colourspace('srgb')"
                    f".jpegsave({str(path)!r},Q=90)"], check=True, capture_output=True)
    subprocess.run(["exiftool", "-overwrite_original", "-q", "-m",
                    f"-DateTimeOriginal={when}", "--", str(path)],
                   check=False, capture_output=True)


def wait_converted(n, secs=120):
    """Wait until the worker is done -- the scan queues the conversion."""
    for _ in range(secs * 2):
        con = sqlite3.connect(DB)
        c = con.execute("SELECT COUNT(*) FROM photos WHERE origin_path LIKE ? "
                        "AND state='ok'", (TEST_YEAR + "/%",)).fetchone()[0]
        con.close()
        if c >= n:
            return c
        time.sleep(.5)
    return c



def _drop_test_members():
    """⚠ An account the test invented through the header is noted by
    `members.note_seen()` -- and afterwards it stands on the settings page
    among the family. That has happened: a made-up name was ticked onto a
    viewing list because it was sitting there. A test must not invent a person
    who then looks real."""
    import sqlite3 as _s
    con = _s.connect(DB)
    con.execute("DELETE FROM members WHERE username LIKE 'zz-test-%' "
                "AND seen_in_authentik=0")
    con.commit(); con.close()


def main():
    sys.path.insert(0, "/opt/family/app")
    _abort_if_real_data()
    alien_before = _foreign()
    print("Ofnahm-Test — Album-Wierkstat")
    print(f"  ({alien_before} foreign photographs in the database — those stay untouched)\n")
    _wipe()

    # -- 1. A folder as it gets copied in by hand ---------------------------
    #    Deliberately WITH the year at the end of the name -- exactly as it happens.
    raw_ = ORIGINS / TEST_YEAR / TEST_COUNTRY / "Copied straight in"
    for i, when in enumerate(("1999:03:04 10:00:00", "1999:03:04 11:30:00"), start=1):
        make(raw_ / f"DSC_000{i}.jpg", 1600, 1067, when)
    chk("the folder is in the originals tree", raw_.is_dir() and len(list(raw_.glob("*.jpg"))) == 2)

    # -- 2. Scannen --------------------------------------------------------
    st, body, _ = req("/api/scan", hdr=ADMIN, method="POST", data={})
    res = json.loads(body) if st == 200 else {}
    chk("Scan leeft", st == 200, f"{st} {body[:120]}")
    chk("the scan takes both in", res.get("new", 0) >= 2, res)
    n = wait_converted(2)
    chk("both converted", n == 2, n)

    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM photos WHERE origin_path LIKE ?",
                       (TEST_YEAR + "/%",)).fetchall()
    con.close()
    chk("Album-Joer aus dem Wee", all(r["album_year"] == TEST_YEAR for r in rows),
        [r["album_year"] for r in rows])
    chk("Album-Numm aus dem Wee", all(r["event"] == "Copied straight in" for r in rows),
        [r["event"] for r in rows])
    chk("Land aus dem Wee", all(r["country"] == TEST_COUNTRY for r in rows),
        [r["country"] for r in rows])

    st, body, _ = req("/admin/albums", hdr=ADMIN)
    chk("the album is in the workshop",
        st == 200 and b"Direkt kop" in body, st)

    # -- 3. Change all four fields at once ---------------------------------
    #    The name WITH the year after it: that has to come off.
    st, body, _ = req("/api/albums/edit", hdr=ADMIN, method="POST", data={
        "year": TEST_YEAR, "country": TEST_COUNTRY, "event": "Copied straight in",
        "new_year": "1998", "new_country": TEST_COUNTRY,
        "new_event": "Geriicht 1998", "new_place": "Testplaz"})
    out = json.loads(body) if st == 200 else {}
    chk("the edit goes through", st == 200, f"{st} {body[:200]}")
    chk("the year at the end of the name is gone", out.get("to") == f"1998/{TEST_COUNTRY}/Geriicht",
        out.get("to"))

    # -- 4. Both trees ------------------------------------------------------
    new_o = ORIGINS / "1998" / TEST_COUNTRY / "Geriicht"
    new_w = WEB / "1998" / TEST_COUNTRY / "Geriicht"
    chk("the originals tree was moved", new_o.is_dir() and len(list(new_o.glob("*.jpg"))) == 2,
        list(new_o.glob("*")) if new_o.exists() else "net do")
    chk("the web tree was moved", new_w.is_dir() and len(list(new_w.glob("*.jpg"))) == 2,
        list(new_w.glob("*")) if new_w.exists() else "net do")
    chk("the old folder is gone (originals)", not raw_.exists())
    chk("the old folder is gone (library)",
        not (WEB / TEST_YEAR / TEST_COUNTRY / "Copied straight in").exists())
    chk("⚠ keng Foto verluer", len(list(new_o.glob("*.jpg"))) == 2)

    # -- 5. D'Datebank ------------------------------------------------------
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM photos WHERE origin_path LIKE '1998/%'").fetchall()
    con.close()
    chk("the paths followed", len(rows) == 2 and all(
        r["origin_path"].startswith(f"1998/{TEST_COUNTRY}/Geriicht/") and
        (r["web_name"] or "").startswith(f"1998/{TEST_COUNTRY}/Geriicht/")
        for r in rows), [dict(r) for r in rows][:1])
    chk("Album-Felder nogezunn", all(
        r["album_year"] == "1998" and r["event"] == "Geriicht" and
        r["place"] == "Testplaz" for r in rows))
    chk("the master is where the database says it is",
        all((WEB / r["web_name"]).is_file() for r in rows))

    # -- 6. Album a Kaart ---------------------------------------------------
    st, body, _ = req(f"/y/1998/{TEST_COUNTRY}/Geriicht", hdr=ADMIN)
    chk("the new album opens", st == 200, st)
    chk("the title is the one built from the structure"'"', b"1998 Geriicht" in body, st)
    from app import gallery
    alt = gallery.list_photos({"year": TEST_YEAR, "country": TEST_COUNTRY,
                               "event": "Copied straight in"})
    chk("no photograph is under the old path any more", alt["total"] == 0, alt["total"])

    alb_rows = {(a["year"], a["country"], a["event"]): a for a in gallery.albums()}
    chk("the album stands under its new name",
        ("1998", TEST_COUNTRY, "Geriicht") in alb_rows, sorted(alb_rows))
    chk("and no longer under the old one",
        (TEST_YEAR, TEST_COUNTRY, "Copied straight in") not in alb_rows, sorted(alb_rows))

    # The map is built from the same albums. A point is only given with GPS or
    # with a place that can be looked up -- our test place does not exist, so
    # there MUST NOT be a point. What must certainly not be there is a point
    # still pointing at the OLD path.
    pts = gallery.map_points()
    chk("the map has no old address left",
        not any("Direkt kop" in (p.get("url") or "") for p in pts),
        [p.get("url") for p in pts])
    chk("a place that does not exist gets no guessed point",
        not any((p.get("url") or "").startswith("/y/1998/") for p in pts),
        [p.get("url") for p in pts])

    st, body, _ = req("/", hdr=ADMIN)
    chk("the front page shows the new title", b"1998 Geriicht" in body)

    # -- 7. Change only the place, without moving anything ------------------
    st, body, _ = req("/api/albums/edit", hdr=ADMIN, method="POST", data={
        "year": "1998", "country": TEST_COUNTRY, "event": "Geriicht",
        "new_year": "1998", "new_country": TEST_COUNTRY,
        "new_event": "Geriicht", "new_place": "Aner Plaz"})
    out = json.loads(body) if st == 200 else {}
    chk("changing only the place", st == 200 and out.get("from") == out.get("to"),
        f"{st} {out}")
    chk("the folder was NOT moved", new_o.is_dir())
    con = sqlite3.connect(DB)
    pl = con.execute("SELECT DISTINCT place FROM photos WHERE origin_path LIKE '1998/%'").fetchall()
    con.close()
    chk("nei Uertschaft an der Datebank", pl == [("Aner Plaz",)], pl)

    # -- 8. What is not allowed --------------------------------------------
    for field, value_ in (("new_year", "abcd"), ("new_year", "99"),
                       ("new_country", ""), ("new_event", "")):
        d = {"year": "1998", "country": TEST_COUNTRY, "event": "Geriicht",
             "new_year": "1998", "new_country": TEST_COUNTRY, "new_event": "Geriicht"}
        d[field] = value_
        st, _, _ = req("/api/albums/edit", hdr=ADMIN, method="POST", data=d)
        chk(f"refuses {field}={value_!r}", st == 400, st)
    st, _, _ = req("/api/albums/edit", hdr=ADMIN, method="POST", data={
        "year": "1998", "country": TEST_COUNTRY, "event": "Does not exist",
        "new_year": "1998", "new_country": TEST_COUNTRY, "new_event": "X"})
    chk("refuses an album that does not exist", st == 400, st)

    # -- 9. Nobody but the admin --------------------------------------------
    # ⚠ An account holder reaches the workshop but sees ONLY their own albums
    # (none here) -- and cannot rename somebody ELSE's album (the next check).
    st, _, _ = req("/admin/albums", hdr={
        "X-Family-Proxy": SECRET, "X-authentik-username": "zz-test-viewer",
        "X-authentik-groups": VIEWER_GROUP})
    chk("an account holder reaches the workshop (sees their own)", st == 200, st)
    st, _, _ = req("/api/albums/edit", hdr={
        "X-Family-Proxy": SECRET, "X-authentik-username": "zz-test-viewer",
        "X-authentik-groups": VIEWER_GROUP}, method="POST", data={
        "year": "1998", "country": TEST_COUNTRY, "event": "Geriicht",
        "new_event": "Geklaut"})
    chk("a plain viewer cannot rename an album", st == 403, st)

    _wipe()
    # ⚠ Only downwards is an error. Photographs turning UP during a run is
    # normal: somebody copies a folder into the originals tree and the test's
    # scan takes it in. This used to be `==`, and then the test raised the
    # alarm although nothing had been lost.
    after_ = _foreign()
    chk("no foreign photograph was lost", after_ >= alien_before,
        f"{alien_before} -> {after_}: the test deleted rows that were not "
        f"its own!")
    _drop_test_members()
    print(f"\n  {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
