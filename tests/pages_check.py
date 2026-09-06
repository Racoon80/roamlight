sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env                                              # noqa: E402
#!/usr/bin/env python3
"""Every page, opened once. Looking for the 500 that nobody clicks on.

⚠ Why this exists: a context variable was renamed in the code and not in the
   template (`roles` vs `rollen`, `alb_rows` vs `alben`). Jinja does not fail at
   startup -- it fails when somebody opens that one page. /admin/settings was
   dead on the live site and every other test was green.

   So: walk every GET route the app has, as an administrator, and treat a 5xx as
   a failure. It needs no fixtures and it changes nothing.

Runs on any instance -- see tests/_env.py.
"""
import http.cookiejar as cj
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, _env.app_root())

BASE = os.environ.get("ROAMLIGHT_BASE", "http://127.0.0.1:8080")
ok = bad = 0


def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1
    else:
        bad += 1
        print(f"  FAIL  {name}   {extra}")


def _admin_headers():
    """However this instance lets an administrator in -- see tests/_env.py."""
    return _env.headers("pages-check")


def main():
    from app import config, db, gallery, main as app_main

    hdr = _admin_headers()
    jar = cj.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    if not config.AUTH_PROXY:
        # A local account: sign in with the one this instance keeps for tests.
        pw_file = Path("/root/roamlight-test-admin.txt")
        if not pw_file.exists():
            sys.exit("ABORTED: local sign-in and no /root/roamlight-test-admin.txt")
        pw = [l.split(": ", 1)[1].strip() for l in pw_file.read_text().splitlines()
              if l.startswith("password")][0]
        body = urllib.parse.urlencode({"username": "admin", "password": pw,
                                       "next": "/"}).encode()
        r = urllib.request.Request(BASE + "/login", data=body, method="POST")
        r.add_header("Content-Type", "application/x-www-form-urlencoded")
        r.add_header("Accept", "text/html")
        op.open(r, timeout=60).read()

    # Real values, so a page is not empty by accident.
    alb = (gallery.albums() or [{}])[0]
    photo = (gallery.list_photos({}, 1, 1)["photos"] or [{}])[0]
    y = alb.get("year", "2026")
    c = urllib.parse.quote(str(alb.get("country", "—")), safe="")
    e = urllib.parse.quote(str(alb.get("event", "—")), safe="")
    pid = photo.get("id", 1)

    paths = []
    for route in app_main.app.routes:
        p = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if "GET" not in methods or not p.startswith("/"):
            continue
        if "{" in p:
            p = (p.replace("{year}", str(y)).replace("{country}", c)
                  .replace("{event}", e).replace("{photo_id}", str(pid))
                  .replace("{width}", "400").replace("{ext}", "webp")
                  .replace("{slug}", "nothing").replace("{token}", "not-a-token")
                  .replace("{z}", "3").replace("{x}", "4").replace("{y}", "2")
                  .replace("{name}", "nobody").replace("{batch}", "none")
                  .replace("{file_id}", "1").replace("{ident}", "1")
                  .replace("{id}", "1"))
        if "{" in p or p.startswith(("/static", "/photos")):
            continue
        paths.append(p)

    print(f"  {len(paths)} Weeër")
    for p in sorted(set(paths)):
        r = urllib.request.Request(BASE + p)
        for k, v in {**hdr, "Accept": "text/html,*/*"}.items():
            r.add_header(k, v)
        try:
            with op.open(r, timeout=120) as f:
                st = f.status
        except urllib.error.HTTPError as exc:
            st = exc.code
        except Exception as exc:                                   # noqa: BLE001
            chk(p, False, repr(exc)[:90]); continue
        chk(f"{p} -> {st}", st < 500, st)

    print(f"\n  {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
