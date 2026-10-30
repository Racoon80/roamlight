"""A person's own cloud: their originals, kept where THEY keep their files.

A contributor's upload has no original in the library -- that was decided on
purpose (see upload.file_away): the family's archive is the administrator's,
and a contributor's photograph lives on the site only as the 4000-pixel master.
That left the person holding no full copy unless they still had it on their
phone. On 30.10.2026 it turned out that six hundred photographs had gone that
way.

So a contributor may connect a Nextcloud (or any WebDAV server) of their own,
and every original they upload is put there FIRST, in the same folders as on
the site:

    <folder>/<year>/<country>/<album>/<file>        e.g. Roamlight/2026/Portugal/Madeira/…

    upload  ->  staged (incoming/staged)
                 |  cloud-push: PUT, then read it BACK and compare the sha256
                 v
                the person's cloud  -- and only THEN may the staged file go

⚠ The one rule in here: a staged upload is thrown away only once its copy in
  the cloud has been read back and matched. Not when the PUT answered 201 --
  when the bytes that came back are the bytes that went. If the cloud is down,
  the file waits in staging and the push is tried again later, for as long as
  it takes (see `sweep`). The photograph itself is converted and shown on the
  site in the meantime; only the discarding waits.

⚠ One way only. Deleting a file in the cloud does nothing to the site, and
  taking a photograph off the site does nothing to the cloud. The cloud copy is
  the person's own, not a mirror the site keeps in step.

⚠ The credential is an app password, created by the person's own Nextcloud
  through Login Flow v2 -- they approve it there, and can revoke it there. It is
  stored ENCRYPTED: the database is copied into every backup in the clear
  (deploy/db-backup.sh), so the key lives in a file next to the other secrets
  (`config.secret_path("cloud")`), never in the database.

⚠ Which server: ONE, named by the administrator (`cloud_url`). The site talks
  to it from the server side, so letting every person type their own address
  would be a way to make the site send requests anywhere on the inside network.
"""
import hashlib
import logging
import posixpath
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import config, db
from .worker import enqueue, handler

log = logging.getLogger("family")

# How long a Login Flow v2 stays open. Nextcloud's own limit is twenty minutes.
FLOW_MINUTES = 20
# The retry steps for a push that failed: 5 min, 10, 20, … up to six hours.
RETRY_FIRST_S = 300
RETRY_MAX_S = 6 * 3600
DEFAULT_FOLDER = "Roamlight"


class CloudError(Exception):
    """Something went wrong talking to the cloud -- a message a person can read."""


# ---------------------------------------------------------------------------
#  The key and the secrets
# ---------------------------------------------------------------------------
def _fernet():
    from cryptography.fernet import Fernet
    key = config.read_secret("cloud")
    if not key:
        # ⚠ Made on first use, 0600, next to the other secrets. Losing it means
        #   every person has to connect again; it never means a credential is
        #   readable out of a backup.
        key = Fernet.generate_key().decode()
        config.write_secret("cloud", key)
    return Fernet(key.encode())


def _seal(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _open(value: str) -> str:
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        raise CloudError("the stored password cannot be read any more -- "
                         "connect again")


# ---------------------------------------------------------------------------
#  The server
# ---------------------------------------------------------------------------
def server() -> str:
    """The one cloud the site may talk to, or "" = the feature is off."""
    return config.setting("cloud_url").rstrip("/")


def _same_host(a: str, b: str) -> bool:
    ua, ub = urlsplit(a), urlsplit(b)
    return (ua.scheme, (ua.hostname or "").lower(), ua.port) == \
           (ub.scheme, (ub.hostname or "").lower(), ub.port)


def _client(auth=None):
    import httpx
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), auth=auth,
                        follow_redirects=False,
                        headers={"User-Agent": f"Roamlight ({config.SITE_TITLE})"})


def clean_folder(value: str) -> str:
    """A folder in the person's cloud: plain names separated by `/`, no `..`."""
    parts = [p.strip() for p in (value or "").replace("\\", "/").split("/")]
    parts = [p for p in parts if p and p not in (".", "..")]
    if not parts:
        return DEFAULT_FOLDER
    for p in parts:
        if re.search(r"[\x00-\x1f]", p) or len(p) > 120:
            raise ValueError("that folder name is not allowed")
    return "/".join(parts)


# ---------------------------------------------------------------------------
#  Connecting: Login Flow v2
# ---------------------------------------------------------------------------
def start_flow(username: str) -> dict:
    """Ask the cloud for a sign-in page. The person approves there; we poll."""
    base = server()
    if not base:
        raise CloudError("no cloud is set up on this site")
    try:
        with _client() as c:
            r = c.post(base + "/index.php/login/v2")
    except Exception as exc:                                     # noqa: BLE001
        raise CloudError(f"the cloud did not answer ({type(exc).__name__})")
    if r.status_code != 200:
        raise CloudError(f"the cloud answered {r.status_code}")
    d = r.json()
    login, poll = d.get("login") or "", d.get("poll") or {}
    endpoint, token = poll.get("endpoint") or "", poll.get("token") or ""
    # ⚠ Both addresses must be the cloud's own. The login URL is handed to the
    #   browser, the endpoint is called by the server -- neither may point
    #   anywhere the administrator did not name.
    if not (login and endpoint and token and _same_host(login, base)
            and _same_host(endpoint, base)):
        raise CloudError("the cloud's answer pointed somewhere else -- refused")
    with db.tx() as conn:
        conn.execute("DELETE FROM cloud_flows WHERE username=?", (username,))
        conn.execute(
            "INSERT INTO cloud_flows (username, endpoint, token_enc, expires_at) "
            "VALUES (?, ?, ?, datetime('now', ?))",
            (username, endpoint, _seal(token), f"+{FLOW_MINUTES} minutes"))
    return {"login_url": login}


def poll_flow(username: str) -> dict:
    """Has the person approved yet? `waiting`, `connected` or `expired`."""
    row = db.connect().execute(
        "SELECT *, expires_at < datetime('now') AS late FROM cloud_flows "
        "WHERE username=?", (username,)).fetchone()
    if row is None:
        return {"state": "none"}
    if row["late"]:
        db.connect().execute("DELETE FROM cloud_flows WHERE username=?", (username,))
        return {"state": "expired"}
    try:
        with _client() as c:
            r = c.post(row["endpoint"], data={"token": _open(row["token_enc"])})
    except CloudError:
        raise
    except Exception:                                            # noqa: BLE001
        return {"state": "waiting"}
    if r.status_code == 404:
        return {"state": "waiting"}
    if r.status_code != 200:
        raise CloudError(f"the cloud answered {r.status_code}")
    d = r.json()
    got_server = (d.get("server") or "").rstrip("/")
    if not _same_host(got_server or server(), server()):
        raise CloudError("the cloud named a different server -- refused")
    db.connect().execute("DELETE FROM cloud_flows WHERE username=?", (username,))
    _save_link(username, d.get("loginName") or "", d.get("appPassword") or "")
    return {"state": "connected", **status(username)}


def connect_manual(username: str, login: str, app_password: str) -> dict:
    """The same, with an app password the person made by hand."""
    if not server():
        raise CloudError("no cloud is set up on this site")
    if not login or not app_password:
        raise CloudError("a user name and an app password are needed")
    _save_link(username, login.strip(), app_password.strip())
    return status(username)


def _whoami(login: str, password: str) -> str:
    """The account's id. ⚠ Not always the login name: behind single sign-on a
    person logs in as one thing and their files live under another, and the
    WebDAV path wants the id."""
    with _client(auth=(login, password)) as c:
        r = c.get(server() + "/ocs/v2.php/cloud/user",
                  params={"format": "json"}, headers={"OCS-APIRequest": "true"})
    if r.status_code == 401:
        raise CloudError("the cloud refused that user name and password")
    if r.status_code != 200:
        raise CloudError(f"the cloud answered {r.status_code}")
    uid = (((r.json().get("ocs") or {}).get("data") or {}).get("id") or "").strip()
    if not uid:
        raise CloudError("the cloud did not say who this is")
    return uid


def _save_link(username: str, login: str, password: str) -> None:
    if not login or not password:
        raise CloudError("the cloud did not hand over a password")
    uid = _whoami(login, password)
    old = link(username)
    folder = old["folder"] if old else DEFAULT_FOLDER
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO cloud_links (username, server, login, uid, secret_enc, folder) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(username) DO UPDATE SET "
            "server=excluded.server, login=excluded.login, uid=excluded.uid, "
            "secret_enc=excluded.secret_enc, last_error=NULL, "
            "connected_at=datetime('now')",
            (username, server(), login, uid, _seal(password), folder))
    # Make the folder now, so a wrong permission shows at once and not on the
    # first upload.
    _mkdirs(link(username), folder)
    db.connect().execute(
        "UPDATE cloud_links SET last_ok_at=datetime('now') WHERE username=?", (username,))
    log.info("cloud: %s connected as %s", username, uid)
    # Anything that was waiting for a connection can go now.
    nudge(username)


def link(username: str):
    if not username:
        return None
    return db.connect().execute(
        "SELECT * FROM cloud_links WHERE username=?", (username,)).fetchone()


def linked(username: str) -> bool:
    lk = link(username)
    return bool(lk) and bool(server()) and _same_host(lk["server"], server())


def set_folder(username: str, folder: str) -> dict:
    lk = link(username)
    if lk is None:
        raise CloudError("not connected")
    folder = clean_folder(folder)
    _mkdirs(lk, folder)
    db.connect().execute("UPDATE cloud_links SET folder=? WHERE username=?",
                         (folder, username))
    return status(username)


def disconnect(username: str) -> dict:
    """Forget the connection -- and ask the cloud to revoke the password.

    ⚠ What is still waiting to be pushed is NOT pushed anywhere else and is NOT
      kept for ever: it goes back to the old rule (converted, then discarded).
      The page says so before the button is pressed.
    """
    lk = link(username)
    if lk is not None:
        try:
            with _client(auth=(lk["login"], _open(lk["secret_enc"]))) as c:
                c.delete(lk["server"].rstrip("/") + "/ocs/v2.php/core/apppassword",
                         headers={"OCS-APIRequest": "true"})
        except Exception:                                        # noqa: BLE001
            # It is revoked in the cloud's own settings just as well. Not a
            # reason to keep a connection the person asked to end.
            log.warning("cloud: could not revoke the app password of %s", username)
        dropped = [r["id"] for r in db.connect().execute(
            "SELECT id FROM photos WHERE owner=? AND origin_root='user' "
            "AND cloud_state IN ('pending','failed')", (username,))]
        with db.tx() as conn:
            conn.execute("DELETE FROM cloud_links WHERE username=?", (username,))
            conn.execute("DELETE FROM cloud_flows WHERE username=?", (username,))
        for pid in dropped:
            _settle(pid, "dropped", None, "disconnected before it was copied")
    else:
        db.connect().execute("DELETE FROM cloud_flows WHERE username=?", (username,))
    return status(username)


def status(username: str) -> dict:
    lk = link(username)
    out = {"available": bool(server()), "server": server(), "connected": False}
    if lk is None:
        return out
    c = db.connect()
    n = {r["cloud_state"]: r["n"] for r in c.execute(
        "SELECT cloud_state, COUNT(*) n FROM photos WHERE owner=? "
        "AND origin_root='user' AND cloud_state IS NOT NULL GROUP BY cloud_state",
        (username,))}
    out.update({
        "connected": linked(username), "login": lk["login"], "uid": lk["uid"],
        "folder": lk["folder"], "connected_at": lk["connected_at"],
        "last_ok_at": lk["last_ok_at"], "last_error": lk["last_error"],
        "copied": n.get("done", 0), "waiting": n.get("pending", 0) + n.get("failed", 0),
        "web": lk["server"].rstrip("/") + "/apps/files/?dir=/" + quote(lk["folder"]),
    })
    return out


# ---------------------------------------------------------------------------
#  WebDAV
# ---------------------------------------------------------------------------
def _dav(lk, rel: str = "") -> str:
    base = f"{lk['server'].rstrip('/')}/remote.php/dav/files/{quote(lk['uid'], safe='')}"
    rel = rel.strip("/")
    return base + ("/" + quote(rel) if rel else "")


def _mkdirs(lk, rel: str) -> None:
    """MKCOL every level; 405 means it is there already."""
    with _client(auth=(lk["login"], _open(lk["secret_enc"]))) as c:
        path = ""
        for part in rel.strip("/").split("/"):
            if not part:
                continue
            path = f"{path}/{part}" if path else part
            r = c.request("MKCOL", _dav(lk, path))
            if r.status_code == 401:
                raise CloudError("the cloud refused the password -- connect again")
            if r.status_code not in (201, 405):
                raise CloudError(f"could not make the folder {path} ({r.status_code})")


def _remote_sha(c, url: str):
    """The sha256 of what the cloud holds at `url`, read back in full -- or None."""
    h = hashlib.sha256()
    with c.stream("GET", url) as r:
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise CloudError(f"reading it back failed ({r.status_code})")
        for chunk in r.iter_bytes(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _sha_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _chunks(path: Path):
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            yield chunk


def _mtime_of(taken_at: str):
    """The capture time as a Unix timestamp, so the cloud shows the file under
    the day it was TAKEN rather than the day it was uploaded."""
    if not taken_at:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(taken_at[:19], fmt)
                       .replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def put_file(lk, src: Path, rel: str, sha: str, taken_at: str = "") -> str:
    """Put one file at <folder>/<rel> and prove it arrived. Returns the path used.

    ⚠ Never overwrites. A file already there with the same bytes counts as done
      (a push that ran twice); one with different bytes is left alone and this
      one goes next to it as `name-2.ext`.
    """
    folder = lk["folder"]
    parent, name = posixpath.split(rel.strip("/"))
    stem, ext = posixpath.splitext(name)
    _mkdirs(lk, posixpath.join(folder, parent) if parent else folder)
    with _client(auth=(lk["login"], _open(lk["secret_enc"]))) as c:
        for n in range(1, 50):
            cand = name if n == 1 else f"{stem}-{n}{ext}"
            path = posixpath.join(folder, parent, cand) if parent else posixpath.join(folder, cand)
            url = _dav(lk, path)
            head = {"If-None-Match": "*", "OC-Checksum": f"SHA256:{sha}"}
            mt = _mtime_of(taken_at)
            if mt:
                head["X-OC-MTime"] = str(mt)
            # ⚠ A length, not chunked transfer encoding: some PHP setups in
            #   front of a Nextcloud read a chunked body as empty.
            head["Content-Length"] = str(src.stat().st_size)
            r = c.put(url, content=_chunks(src), headers=head)
            if r.status_code in (412, 405, 409) or (
                    r.status_code == 400 and "exist" in r.text.lower()):
                # Something is there already. Ours from an earlier run?
                if _remote_sha(c, url) == sha:
                    return path
                continue
            if r.status_code == 401:
                raise CloudError("the cloud refused the password -- connect again")
            if r.status_code == 507:
                raise CloudError("the cloud is full")
            if r.status_code not in (200, 201, 204):
                raise CloudError(f"the cloud answered {r.status_code} to the upload")
            back = _remote_sha(c, url)
            if back != sha:
                # ⚠ Take the bad copy away again, so nobody mistakes it for the
                #   original -- and do NOT let the staged file go.
                c.delete(url)
                raise CloudError("the copy in the cloud did not match -- removed, "
                                 "will try again")
            return path
    raise CloudError("too many files of that name in the cloud folder")


# ---------------------------------------------------------------------------
#  The queue
# ---------------------------------------------------------------------------
def _settle(photo_id: int, state: str, path, error) -> None:
    """Write the outcome, and let the staged file go if it may.

    ⚠ The decision and the write are ONE transaction, and `convert.py` makes the
      mirror-image decision the same way: whichever of the two finishes SECOND
      sees the other's mark and removes the staged file. Both run `BEGIN
      IMMEDIATE`, so they cannot both decide "not yet".
    """
    gone = None
    with db.tx() as conn:
        row = conn.execute("SELECT state, master_source_path FROM photos WHERE id=?",
                           (photo_id,)).fetchone()
        if row is None:
            return
        conn.execute(
            "UPDATE photos SET cloud_state=?, cloud_path=COALESCE(?, cloud_path), "
            "cloud_error=?, cloud_at=datetime('now') WHERE id=?",
            (state, path, error, photo_id))
        if state in ("done", "dropped") and row["state"] == "ok" and row["master_source_path"]:
            gone = row["master_source_path"]
            conn.execute("UPDATE photos SET master_source_path=NULL WHERE id=?", (photo_id,))
    if gone:
        Path(gone).unlink(missing_ok=True)


def releasable(cloud_state) -> bool:
    """May convert.py throw the staged upload away? Only if nothing is waiting
    to copy it."""
    return cloud_state not in ("pending", "failed")


@handler("cloud-push")
def push(job) -> None:
    photo_id = int(job["payload"])
    row = db.connect().execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
    if row is None or row["origin_root"] != "user":
        return
    if row["cloud_state"] not in ("pending", "failed"):
        return
    lk = link(row["owner"])
    if lk is None or not linked(row["owner"]):
        _settle(photo_id, "dropped", None, "no cloud connected")
        return
    src = Path(row["master_source_path"] or "")
    if not src.is_file():
        _settle(photo_id, "lost", None, "the staged upload is gone")
        return
    sha = row["origin_sha256"] or _sha_of(src)
    try:
        if _sha_of(src) != sha:
            raise CloudError("the staged file does not match its upload")
        path = put_file(lk, src, row["origin_path"], sha, row["taken_at"] or "")
    except Exception as exc:                                     # noqa: BLE001
        # ⚠ Not raised. A cloud that is down tonight is not a broken job: the
        #   worker would try five times in twenty-five minutes and then give
        #   up for good. `sweep` brings it back on a schedule that grows to six
        #   hours and never stops.
        msg = str(exc) if isinstance(exc, CloudError) else f"{type(exc).__name__}: {exc}"
        tries = (row["cloud_tries"] or 0) + 1
        wait = min(RETRY_MAX_S, RETRY_FIRST_S * 2 ** (tries - 1))
        db.connect().execute(
            "UPDATE photos SET cloud_state='failed', cloud_error=?, cloud_tries=?, "
            "cloud_next_at=datetime('now', ?) WHERE id=?",
            (msg[:300], tries, f"+{wait} seconds", photo_id))
        db.connect().execute(
            "UPDATE cloud_links SET last_error=? WHERE username=?", (msg[:300], row["owner"]))
        log.warning("cloud: photo %s not copied (%s) -- again in %ss", photo_id, msg, wait)
        return
    _settle(photo_id, "done", path, None)
    db.connect().execute(
        "UPDATE cloud_links SET last_ok_at=datetime('now'), last_error=NULL "
        "WHERE username=?", (row["owner"],))
    log.info("cloud: photo %s copied to %s", photo_id, path)


def nudge(username: str = None) -> int:
    """Queue every push that is due now (or all of one person's, at once)."""
    c = db.connect()
    if username:
        rows = c.execute(
            "SELECT id FROM photos WHERE origin_root='user' AND owner=? "
            "AND cloud_state IN ('pending','failed')", (username,)).fetchall()
    else:
        rows = c.execute(
            "SELECT id FROM photos WHERE origin_root='user' "
            "AND cloud_state IN ('pending','failed') "
            "AND (cloud_next_at IS NULL OR cloud_next_at <= datetime('now'))").fetchall()
    for r in rows:
        enqueue("cloud-push", str(r["id"]))
    return len(rows)


def sweep() -> dict:
    """The slow loop: retries that are due, and staged files that may go.

    Called from the site's worker every few minutes. The second half catches a
    crash between "copied" and "deleted" -- the file would otherwise lie in
    staging for ever.
    """
    queued = nudge()
    freed = 0
    for r in db.connect().execute(
            "SELECT id FROM photos WHERE origin_root='user' AND state='ok' "
            "AND master_source_path IS NOT NULL "
            "AND cloud_state IN ('done','dropped')").fetchall():
        _settle(r["id"], db.connect().execute(
            "SELECT cloud_state FROM photos WHERE id=?", (r["id"],)).fetchone()[0],
            None, None)
        freed += 1
    db.connect().execute("DELETE FROM cloud_flows WHERE expires_at < datetime('now')")
    return {"queued": queued, "freed": freed}
