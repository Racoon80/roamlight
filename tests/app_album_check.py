#!/usr/bin/env python3
"""What the app's album screens rely on.

    python3 tests/app_album_check.py

Hermetic: its own database under /tmp, no network.

  * /api/photos says per photograph whether THIS person may delete it
    (`may_remove`) -- the same rule the delete itself enforces. On 28.09.2026
    the app offered a bin on a library album to a contributor; the server said
    403 and the photographs simply stayed.
  * /api/albums/settings: filled in for somebody who may edit, 403 otherwise.
  * a renamed album takes its journey along (it used to be left behind).
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="roamlight-album-"))
os.environ.update(
    FAMILY_DATA=str(TMP / "data"), FAMILY_DB=str(TMP / "data" / "t.db"),
    FAMILY_ORIGINS=str(TMP / "o"), FAMILY_WEB=str(TMP / "w"),
    FAMILY_DERIVATIVES=str(TMP / "data" / "d"), FAMILY_INCOMING=str(TMP / "data" / "i"),
    FAMILY_WORK=str(TMP / "data" / "work"), FAMILY_REQUIRE_MOUNT="0",
    FAMILY_AUTH="local", FAMILY_SITE_URL="https://photos.example.com",
)
for d in ("data", "o", "w", "data/d", "data/i", "data/work"):
    (TMP / d).mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient                       # noqa: E402

from app import acl, auth, config, db                           # noqa: E402
from app.main import app                                        # noqa: E402

bad = 0


def check(name, got, want):
    global bad
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" -- expected {want!r}"))
    bad += 0 if ok else 1


def photo(event, owner, root="my_photos", n=0):
    with db.tx() as c:
        c.execute(
            "INSERT INTO photos (origin_path, web_name, album_year, country, event, place, "
            "state, origin_root, owner, taken_at, width, height, kind) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"2017/Lux/{event}/{n}.jpg", f"2017/Lux/{event}/{n}.jpg", "2017", "Lux",
             event, "Kanddaaf", "ok", root, owner, f"2017-01-01 12:00:0{n}", 100, 100, "photo"))


def client(user):
    c = TestClient(app, base_url="https://photos.example.com")
    c.cookies.set(config.SESSION_COOKIE, auth.new_session(user))
    return c


try:
    db.init()
    admin_g = sorted(config.ADMIN_GROUPS)[0]
    contrib_g = sorted(config.CONTRIBUTOR_GROUPS)[0]
    auth.create_user("boss", "a long enough password 1", groups=[admin_g])
    auth.create_user("aunt", "a long enough password 2", groups=[contrib_g])

    photo("Library", None, n=1)                     # nobody's: the family library
    photo("Library", None, n=2)
    photo("Mine", "aunt", root="user", n=1)          # all hers
    photo("Mixed", "aunt", root="user", n=1)
    photo("Mixed", None, n=2)
    acl.set_audience("2017", "Lux", "Library", ["group:" + contrib_g])
    acl.set_audience("2017", "Lux", "Mixed", ["group:" + contrib_g])

    aunt, boss = client("aunt"), client("boss")

    def flags(c, event):
        r = c.get(f"/api/photos?year=2017&country=Lux&event={event}")
        return sorted(p["may_remove"] for p in r.json()["photos"])

    check("contributor, library album: no bins", flags(aunt, "Library"), [False, False])
    check("contributor, mixed album: only her own", flags(aunt, "Mixed"), [False, True])
    check("admin, library album: all", flags(boss, "Library"), [True, True])

    r = aunt.post("/api/photos/bulk", json={"action": "remove", "ids": [1]},
                  headers={"sec-fetch-site": "same-origin"})
    check("... and the server agrees (403)", r.status_code, 403)

    q = "year=2017&country=Lux&event="
    check("settings: contributor, library album", aunt.get(f"/api/albums/settings?{q}Library").status_code, 403)
    check("settings: contributor, mixed album", aunt.get(f"/api/albums/settings?{q}Mixed").status_code, 403)
    r = aunt.get(f"/api/albums/settings?{q}Mine")
    check("settings: contributor, her own album", r.status_code, 200)
    s = boss.get(f"/api/albums/settings?{q}Library").json()
    check("settings: place", s.get("place"), "Kanddaaf")
    check("settings: audience", s.get("audience"), ["group:" + contrib_g])
    check("settings: journey form", sorted(s.get("journey", {})), ["departure", "legs", "multi", "transport"])
    check("settings: admins are not offered", any(p["principal"] == "user:boss" for p in s["people"]), False)

    with db.tx() as c:
        c.execute("INSERT INTO album_journey (album_key, departure, transport) VALUES (?,?,?)",
                  ("2017/Lux/Library", "Rumelange", "car"))
    acl.rename_album("2017/Lux/Library", "2017/Lux/Renamed")
    row = db.connect().execute(
        "SELECT album_key FROM album_journey WHERE departure='Rumelange'").fetchone()
    check("the journey moves with a rename", row["album_key"] if row else None, "2017/Lux/Renamed")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("app album:", "all good" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
