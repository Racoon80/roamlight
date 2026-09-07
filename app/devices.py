"""Devices: how a phone or a tablet gets in.

Three steps:

    1. You sign in in a browser and press "connect a device" on the /app page.
    2. The site shows a **QR code**. It holds no token, only a pairing code:
       good for five minutes and for one device.
    3. The app scans it and calls `POST /api/app/pair`, trading the code for
       its real token, which then lives in the phone's keychain.

⚠ **Why not the token in the QR?** A QR on a screen gets photographed, gets
  passed around, stays in a screenshot. A code that is dead after five minutes
  and after one use is not worth any of that.

⚠ **Why sha256 and not argon2?** Share-link passwords are argon2 -- a person
  chooses those, and every guess has to hurt. A device token is 32 random
  bytes: nothing is guessed there, and argon2 would cost 50 ms on EVERY request
  (including each of the sixty images on a gallery page). For secrets with full
  entropy sha256 is the right tool -- and it is what GitHub and Stripe do with
  their tokens.

A token looks like this:

    fam_<ref>_<secret>          e.g. fam_7Qb3n1kZ_x9...   (ref = lookup key)

The `ref` is stored in clear, so the row can be found without walking every
device. What is compared afterwards is the sha256 of the secret, with
`compare_digest`.
"""
import hashlib
import hmac
import secrets
import time

from . import db

PREFIX = "fam_"
PAIR_MINUTES = 5
REF_BYTES = 6         # -> 12 hex characters (no `_`; see `redeem`)
SECRET_BYTES = 32     # 256 Bit

# ⚠ A small cache: one gallery page asks for sixty images, and every one of
# them goes through the token check. Without this that is sixty times the same
# SELECT. Thirty seconds is short enough that taking a device off works at once
# -- and `revoke()` clears the cache anyway.
_CACHE: dict = {}
_CACHE_TTL = 30.0


def _sha(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


# --- pairing (browser -> QR) -------------------------------------------------

def new_pairing(username: str) -> dict:
    """A pairing code for this person. Returns {code, expires_in}."""
    # ⚠ 24 bytes -> 32 characters, 192 bits. The first version used 12
    #   characters, which is plenty against guessing but leaves no room at all
    #   if a code is ever seen and typed later. A QR carries 32 without effort.
    code = secrets.token_urlsafe(24)
    with db.tx() as c:
        c.execute("DELETE FROM app_pairings WHERE username=? AND used_at IS NULL",
                  (username,))               # only ever one open code
        c.execute(
            "INSERT INTO app_pairings (code, username, expires_at) "
            "VALUES (?,?,datetime('now',?))",
            (code, username, f"+{PAIR_MINUTES} minutes"))
    return {"code": code, "expires_in": PAIR_MINUTES * 60}


def redeem(code: str, name: str, ip: str = "") -> dict | None:
    """Trade a code for a token. `None` = invalid, expired or already used --
    which of the three is deliberately not said."""
    code = (code or "").strip()
    if not code:
        return None
    with db.tx() as c:
        row = c.execute(
            "SELECT username FROM app_pairings WHERE code=? AND used_at IS NULL "
            "AND expires_at > datetime('now')", (code,)).fetchone()
        if row is None:
            return None
        c.execute("UPDATE app_pairings SET used_at=datetime('now') WHERE code=?", (code,))
        # ⚠ `token_hex` and NOT `token_urlsafe`: the urlsafe alphabet
        #   contains `_`, and the ref sits BETWEEN two underscores in the
        #   token. A ref with a `_` in it would make the token unsplittable,
        #   and the device would be thrown out on its very first request.
        ref = secrets.token_hex(REF_BYTES)
        secret = secrets.token_urlsafe(SECRET_BYTES)
        c.execute(
            "INSERT INTO app_devices (ref, username, name, secret_hash, last_ip) "
            "VALUES (?,?,?,?,?)",
            (ref, row["username"], (name or "").strip()[:60] or "an apparat",
             _sha(secret), ip))
    return {"token": f"{PREFIX}{ref}_{secret}", "user": row["username"]}


# --- checking (every request from the app) -----------------------------------

def identify(token: str) -> str | None:
    """The user name behind a token, or `None`.

    ⚠ Called on EVERY request from the app, so nothing expensive belongs
    here."""
    token = (token or "").strip()
    if not token.startswith(PREFIX) or token.count("_") < 2:
        return None
    _, ref, secret = token.split("_", 2)
    if not ref or not secret:
        return None
    now = time.monotonic()
    hit = _CACHE.get(ref)
    if hit and hit[2] > now:
        return hit[0] if hmac.compare_digest(hit[1], _sha(secret)) else None
    row = db.connect().execute(
        "SELECT username, secret_hash FROM app_devices "
        "WHERE ref=? AND revoked_at IS NULL", (ref,)).fetchone()
    if row is None:
        return None
    _CACHE[ref] = (row["username"], row["secret_hash"], now + _CACHE_TTL)
    if not hmac.compare_digest(row["secret_hash"], _sha(secret)):
        return None
    return row["username"]


def touch(token: str, ip: str = "") -> None:
    """Update `last_seen` -- at most every five minutes, otherwise every image
    would be a write."""
    ref = token.split("_", 2)[1] if token.count("_") >= 2 else ""
    if not ref:
        return
    try:
        with db.tx() as c:
            c.execute("UPDATE app_devices SET last_seen=datetime('now'), last_ip=? "
                      "WHERE ref=? AND (last_seen IS NULL "
                      "  OR last_seen < datetime('now','-5 minutes'))", (ip, ref))
    except Exception:                                            # noqa: BLE001
        pass          # a counter must never take a request down


# --- managing them (the /app page) -------------------------------------------

def list_for(username: str, all_users: bool = False) -> list:
    """One person's devices — or everybody's, for an administrator."""
    sql = ("SELECT id, username, name, created_at, last_seen, last_ip "
           "FROM app_devices WHERE revoked_at IS NULL")
    args = ()
    if not all_users:
        sql += " AND username=?"
        args = (username,)
    return [dict(r) for r in db.connect().execute(sql + " ORDER BY id DESC", args)]


def revoke(device_id: int, username: str, is_admin: bool = False) -> bool:
    """Take a device off. One click, immediately -- no dialog in between.

    ⚠ An administrator may take off any device (somebody else's lost phone);
    everyone else only their own."""
    sql = "UPDATE app_devices SET revoked_at=datetime('now') WHERE id=? AND revoked_at IS NULL"
    args = [device_id]
    if not is_admin:
        sql += " AND username=?"
        args.append(username)
    with db.tx() as c:
        n = c.execute(sql, args).rowcount
    _CACHE.clear()
    return bool(n)


# --- the QR code -------------------------------------------------------------

def pair_url(code: str) -> str:
    """What the QR code contains.

    ⚠ The code sits in the **fragment** (`#c=...`), not in the query (`?c=...`).
    A fragment is never sent to a server -- so a pairing code never lands in
    anybody's access log, not the proxy's and not this site's.
    """
    from . import config
    return f"{config.SITE_URL}/app/pair#c={code}"


def qr_svg(text: str) -> str | None:
    """The QR as inline SVG (no image file -- the content policy allows
    nothing from outside anyway).

    ⚠ **Dark modules on a light ground.** The site is dark and it is tempting
    to invert the code -- but many scanners will not read an inverted QR. So:
    ink on paper, and the quiet zone around it stays light.

    Without the library this returns `None` and the page shows the code as
    text. An empty box would be worse than a code you can type.
    """
    try:
        import segno
    except ImportError:
        return None
    # ⚠ `BytesIO`, not `StringIO`: segno's SVG writer emits **bytes**. With a
    #   text buffer it raises a TypeError -- and that would only have shown up
    #   when somebody pressed the button.
    import io
    buf = io.BytesIO()
    segno.make(text, error="m").save(
        buf, kind="svg", scale=5, border=3,
        dark="#1a1815", light="#f2ede4", xmldecl=False, svgns=True)
    return buf.getvalue().decode("utf-8")
