#!/usr/bin/env python3
"""Acceptance test for a person's own cloud (app/cloud.py). Runs on the server.

    bash tests/run.sh cloud

A small stand-in for a Nextcloud runs on 127.0.0.1 inside this test: Login
Flow v2, the user endpoint, and enough WebDAV (MKCOL, PUT, GET, DELETE). The
site is pointed at it for the length of the test and pointed back afterwards.

What it proves:
  * connecting through Login Flow v2, and the password stored encrypted;
  * a contributor's upload lands in the cloud, in the site's folders, byte for
    byte -- and only THEN does the staged file go;
  * a cloud that is down: the photograph is still converted and shown, the
    staged file is KEPT, and it goes once the cloud is back;
  * a copy that comes back wrong is removed from the cloud and the staged file
    is kept;
  * disconnecting revokes the app password in the cloud.

It works under 1999/Testland with a `zz-` account, like the other tests, and
removes what it made.
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env                                              # noqa: E402

sys.path.insert(0, _env.app_root())
from app import config, db                               # noqa: E402

BASE = "http://127.0.0.1:8080"
YEAR, COUNTRY = "1999", "Testland"
USER = "zz-cloud"
CONTRIB = (os.environ.get("FAMILY_CONTRIBUTOR_GROUPS") or "family").split(",")[0].strip()
LOGIN, UID, PASSWORD = "zz-login", "zz-uid", "app-" + base64.b32encode(os.urandom(15)).decode()
MY_BATCHES = []

ok = bad = 0


def chk(what, cond, detail=""):
    global ok, bad
    if cond:
        ok += 1; print(f"  ok    {what}")
    else:
        bad += 1; print(f"  FAIL  {what}  {detail}")


# ---------------------------------------------------------------------------
#  The stand-in cloud
# ---------------------------------------------------------------------------
class Cloud:
    files = {}           # path under the user's root -> bytes
    dirs = set()
    approved = False
    mode = "ok"          # ok | down | corrupt
    revoked = False
    puts = 0


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        want = "Basic " + base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()
        return self.headers.get("Authorization") == want

    def _dav_path(self):
        pre = f"/remote.php/dav/files/{UID}/"
        p = unquote(self.path.split("?")[0])
        return p[len(pre):].strip("/") if p.startswith(pre) else None

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_POST(self):
        base = f"http://127.0.0.1:{self.server.server_port}"
        self._body()
        if self.path == "/index.php/login/v2":
            return self._send(200, {"poll": {"token": "tok", "endpoint": base + "/login/v2/poll"},
                                    "login": base + "/login/v2/flow/abc"})
        if self.path == "/login/v2/poll":
            if not Cloud.approved:
                return self._send(404, {})
            return self._send(200, {"server": base, "loginName": LOGIN,
                                    "appPassword": PASSWORD})
        self._send(404, {})

    def do_GET(self):
        if not self._authed():
            return self._send(401, {})
        if self.path.startswith("/ocs/v2.php/cloud/user"):
            return self._send(200, {"ocs": {"data": {"id": UID}}})
        p = self._dav_path()
        if p is not None and p in Cloud.files:
            return self._send(200, Cloud.files[p], "application/octet-stream")
        self._send(404, {})

    def do_MKCOL(self):
        if not self._authed():
            return self._send(401, {})
        p = self._dav_path()
        if p in Cloud.dirs:
            return self._send(405, {})
        Cloud.dirs.add(p)
        self._send(201, {})

    def do_PUT(self):
        body = self._body()
        if not self._authed():
            return self._send(401, {})
        p = self._dav_path()
        if Cloud.mode == "down":
            return self._send(503, {})
        if self.headers.get("If-None-Match") == "*" and p in Cloud.files:
            return self._send(412, {})
        if os.path.dirname(p) not in Cloud.dirs:
            return self._send(409, {})
        Cloud.puts += 1
        Cloud.files[p] = body[::-1] if Cloud.mode == "corrupt" else body
        self._send(201, {})

    def do_DELETE(self):
        if not self._authed():
            return self._send(401, {})
        if self.path.startswith("/ocs/v2.php/core/apppassword"):
            Cloud.revoked = True
            return self._send(200, {})
        Cloud.files.pop(self._dav_path(), None)
        self._send(204, b"")


# ---------------------------------------------------------------------------
#  Talking to the site
# ---------------------------------------------------------------------------
HDR = {}


def call(method, path, data=None, raw=None, ctype="application/json"):
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    req = urllib.request.Request(BASE + path, data=body, method=method,
                                 headers={**HDR, "Content-Type": ctype,
                                          "Sec-Fetch-Site": "same-origin"})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def make_photo(path, dt, seq):
    from PIL import Image
    Image.new("RGB", (900, 600), (40 + seq * 30, 90, 120)).save(path, "JPEG", quality=90)
    subprocess.run(["exiftool", "-overwrite_original", "-q",
                    f"-DateTimeOriginal={dt}", "--", str(path)],
                   check=True, capture_output=True)


def upload_one(path, event):
    st, b = call("POST", "/api/upload/batch")
    batch = b["batch"]; MY_BATCHES.append(batch)
    st, f = call("POST", f"/api/upload/{batch}/file",
                 {"name": path.name, "size": path.stat().st_size})
    fid = f["file_id"]
    call("PUT", f"/api/upload/{batch}/file/{fid}/chunk?offset=0",
         raw=path.read_bytes(), ctype="application/octet-stream")
    call("POST", f"/api/upload/{batch}/file/{fid}/done")
    call("POST", f"/api/upload/{batch}/commit",
         {"year": YEAR, "country": COUNTRY, "event": event, "place": "Testplaz"})
    t0 = time.time()
    while time.time() - t0 < 120:
        st, out = call("GET", f"/api/upload/{batch}/status")
        if out.get("state") == "done":
            return (out.get("stored") or [{}])[0].get("photo_id")
        time.sleep(1)
    return None


def row(pid):
    con = _env.connect(); con.row_factory = __import__("sqlite3").Row
    r = con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
    con.close()
    return r


def wait_for(pid, cond, secs=120):
    t0 = time.time()
    r = row(pid)
    while time.time() - t0 < secs:
        r = row(pid)
        if r is not None and cond(r):
            return r
        time.sleep(1)
    return r


def cleanup(old_url):
    con = _env.connect()
    ids = [r[0] for r in con.execute(
        "SELECT id FROM photos WHERE origin_path LIKE ? AND owner=?",
        (f"{YEAR}/{COUNTRY}/%", USER))]
    staged = [r[0] for r in con.execute(
        "SELECT master_source_path FROM photos WHERE origin_path LIKE ? AND owner=? "
        "AND master_source_path IS NOT NULL", (f"{YEAR}/{COUNTRY}/%", USER))]
    if ids:
        q = ",".join("?" * len(ids))
        con.execute(f"DELETE FROM jobs WHERE kind IN ('convert-upload','cloud-push') "
                    f"AND payload IN ({q})", [str(i) for i in ids])
        con.execute(f"DELETE FROM upload_files WHERE photo_id IN ({q})", ids)
        con.execute(f"DELETE FROM photos WHERE id IN ({q})", ids)
    if MY_BATCHES:
        q = ",".join("?" * len(MY_BATCHES))
        con.execute(f"DELETE FROM upload_files WHERE batch_id IN "
                    f"(SELECT id FROM upload_batches WHERE token IN ({q}))", MY_BATCHES)
        con.execute(f"DELETE FROM upload_batches WHERE token IN ({q})", MY_BATCHES)
    con.execute("DELETE FROM cloud_links WHERE username=?", (USER,))
    con.execute("DELETE FROM cloud_flows WHERE username=?", (USER,))
    if old_url is None:
        con.execute("DELETE FROM state WHERE key='cfg.cloud_url'")
    else:
        con.execute("UPDATE state SET value=? WHERE key='cfg.cloud_url'", (old_url,))
    con.commit(); con.close()
    for s in staged:
        Path(s).unlink(missing_ok=True)
    for i in ids:
        shutil.rmtree(config.DERIVATIVE_DIR / f"{i // 1000:04d}" / str(i), ignore_errors=True)
    shutil.rmtree(Path(_env.need("FAMILY_WEB")) / YEAR / COUNTRY, ignore_errors=True)


def main():
    global HDR
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fake = f"http://127.0.0.1:{srv.server_port}"

    con = _env.connect()
    r = con.execute("SELECT value FROM state WHERE key='cfg.cloud_url'").fetchone()
    con.close()
    old_url = r[0] if r else None
    cleanup(old_url)          # leftovers of an aborted run

    tmp = Path(tempfile.mkdtemp(prefix="cloud_check-"))
    try:
        print("Acceptance test — a person's own cloud\n")
        HDR = _env.headers(USER, CONTRIB)
        st, s = call("GET", "/api/cloud")
        chk("contributor may ask", st == 200, s)

        config.set_setting("cloud_url", fake)
        st, s = call("GET", "/api/cloud")
        chk("the feature shows once a cloud is named", s.get("available") and not s.get("connected"), s)

        # --- connect through Login Flow v2 ------------------------------
        st, d = call("POST", "/api/cloud/start")
        chk("Login Flow v2 hands out the cloud's own page",
            st == 200 and d.get("login_url", "").startswith(fake + "/login/v2/flow/"), d)
        st, p = call("POST", "/api/cloud/poll")
        chk("before approval: waiting", p.get("state") == "waiting", p)
        Cloud.approved = True
        st, p = call("POST", "/api/cloud/poll")
        chk("after approval: connected", p.get("state") == "connected" and p.get("connected"), p)
        chk("the WebDAV id is the account id, not the login", p.get("uid") == UID, p)
        chk("the folder was made at once", "Roamlight" in Cloud.dirs, sorted(Cloud.dirs))
        con = _env.connect()
        enc = con.execute("SELECT secret_enc FROM cloud_links WHERE username=?",
                          (USER,)).fetchone()[0]
        con.close()
        chk("the app password is NOT in the database in the clear", PASSWORD not in enc)
        key = config.secret_path("cloud")
        chk("the key is a file of its own, 0600",
            key.exists() and (key.stat().st_mode & 0o777) == 0o600, oct(key.stat().st_mode))

        st, e = call("POST", "/api/cloud/manual", {"login": LOGIN, "password": "wrong"})
        chk("a wrong app password is refused", st == 400, e)
        st, s = call("GET", "/api/cloud")
        chk("…and the good connection is still there", s.get("connected"), s)

        # --- 1: the normal road ------------------------------------------
        p1 = tmp / "IMG_0001.JPG"; make_photo(p1, "1999:08:01 10:00:00", 1)
        sha1 = hashlib.sha256(p1.read_bytes()).hexdigest()
        pid = upload_one(p1, "Cloudtest")
        chk("the upload was filed", bool(pid))
        r = wait_for(pid, lambda r: r["state"] == "ok" and r["cloud_state"] == "done")
        chk("converted AND copied", r["state"] == "ok" and r["cloud_state"] == "done",
            dict(r) if r else None)
        want = f"Roamlight/{r['origin_path']}"
        chk("in the cloud under the site's own folders", r["cloud_path"] == want,
            (r["cloud_path"], want))
        chk("byte for byte", hashlib.sha256(Cloud.files.get(want, b"")).hexdigest() == sha1)
        r = wait_for(pid, lambda r: r["master_source_path"] is None, 30)
        chk("only then: the staged file is gone", r["master_source_path"] is None,
            r["master_source_path"])

        # --- 2: the cloud is down ----------------------------------------
        Cloud.mode = "down"
        p2 = tmp / "IMG_0002.JPG"; make_photo(p2, "1999:08:02 10:00:00", 2)
        pid2 = upload_one(p2, "Cloudtest")
        r = wait_for(pid2, lambda r: r["state"] == "ok" and r["cloud_state"] == "failed")
        chk("cloud down: the photograph is still on the site", r["state"] == "ok", r["state"])
        chk("cloud down: the copy is marked failed", r["cloud_state"] == "failed",
            (r["cloud_state"], r["cloud_error"]))
        staged2 = r["master_source_path"]
        chk("cloud down: the staged file is KEPT", bool(staged2) and Path(staged2).is_file(),
            staged2)
        st, s = call("GET", "/api/cloud")
        chk("the page says one is waiting", s.get("waiting") == 1, s)

        Cloud.mode = "ok"
        st, s = call("POST", "/api/cloud/retry")
        chk("try now queues it", st == 200 and s.get("queued") == 1, s)
        r = wait_for(pid2, lambda r: r["cloud_state"] == "done" and not r["master_source_path"])
        chk("cloud back: copied, and the staged file is gone",
            r["cloud_state"] == "done" and not Path(staged2).exists(),
            (r["cloud_state"], r["cloud_error"]))

        # --- 3: the copy comes back wrong ---------------------------------
        Cloud.mode = "corrupt"
        p3 = tmp / "IMG_0003.JPG"; make_photo(p3, "1999:08:03 10:00:00", 3)
        pid3 = upload_one(p3, "Cloudtest")
        r = wait_for(pid3, lambda r: r["state"] == "ok" and r["cloud_state"] == "failed")
        chk("a wrong copy is not accepted", r["cloud_state"] == "failed", r["cloud_state"])
        chk("…it is taken out of the cloud again",
            not any(k.endswith(Path(r["origin_path"]).name) for k in Cloud.files),
            sorted(Cloud.files))
        chk("…and the staged file is kept",
            bool(r["master_source_path"]) and Path(r["master_source_path"]).is_file())

        # --- 4: disconnect ------------------------------------------------
        Cloud.mode = "ok"
        st, s = call("POST", "/api/cloud/disconnect")
        chk("disconnected", st == 200 and not s.get("connected"), s)
        chk("the app password was revoked in the cloud", Cloud.revoked)
        r = wait_for(pid3, lambda r: r["cloud_state"] == "dropped", 10)
        chk("what was waiting goes back to the old rule",
            r["cloud_state"] == "dropped" and r["master_source_path"] is None,
            (r["cloud_state"], r["master_source_path"]))

        # --- 5: admins name the server; nobody else ------------------------
        st, e = call("POST", "/api/cloud/server", {"url": "https://example.org"})
        chk("a contributor cannot name the cloud", st == 403, (st, e))
        try:
            config.set_setting("cloud_url", "http://10.0.0.1")
            chk("plain http to another machine is refused", False)
        except ValueError:
            chk("plain http to another machine is refused", True)
    finally:
        cleanup(old_url)
        shutil.rmtree(tmp, ignore_errors=True)
        srv.shutdown()

    print(f"\n{ok} ok, {bad} wrong")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
