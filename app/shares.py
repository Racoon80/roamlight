"""Share links -- one collection, for people without an account.

This is the only place on the whole site where something goes out **without a
sign-in**. So different rules apply here, and each of them is deliberate:

| rule | how |
|---|---|
| **expiry** | required. No link without an end. 7/30/90 days, 30 by default, a year at most |
| **password** | required. The site makes it itself -- 4 words + 3 digits = 48 bits |
| **download** | on. What comes out is the **master**, never the original |
| **upload** | off, until somebody explicitly ticks it |

⚠ **Honestly: the password does not protect against the link being passed on**
-- it travels in the same message. Against passing on, the expiry and the
revoke button are what help. The password protects against the **URL leaking**:
referrer, browser history, a chat preview, a proxy log. Both are worth having;
they should not be confused.

⚠ **We cannot count visitors.** The peer a reverse proxy sees is always the
proxy, and what a client sends in `X-Forwarded-For` is a wish list. "This link
was opened from 200 addresses" would be a security statement, and one that can
be forged is worse than none. So what is counted is **how often** it was
opened, not by how many.
"""
import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

from . import collections, config, db
from .wordlist import WORDS

log = logging.getLogger("family")

# Argon2id, not sha256: a stolen database backup would otherwise fall overnight.
_PH = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)

MAX_DAYS = 365
DEFAULT_DAYS = 30
FAIL_LOCK = 5           # after the 5th wrong try, closed for 15 minutes
FAIL_REVOKE = 20        # after the 20th, the link revokes itself
LOCK_MINUTES = 15
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def make_password() -> str:
    """4 Wierder + 3 Zifferen, z.B. `kaz-fluss-blo-mier-427`. 48 Bit."""
    words = "-".join(secrets.choice(WORDS) for _ in range(4))
    return f"{words}-{secrets.randbelow(1000):03d}"


def create(collection_id: int, days: int = DEFAULT_DAYS, allow_download: bool = True,
           allow_upload: bool = False, keep_gps: bool = False,
           owner: str = None, max_views: int = None) -> dict:
    """Create a link. The password is returned **once** -- only the hash is
    stored.

    `max_views`: how often the link may be opened (after the password). NULL =
    unlimited. A contributor's link is capped at 1; an administrator chooses."""
    st = collections.one(int(collection_id))          # raises when there is none
    days = int(days or DEFAULT_DAYS)
    if days < 1 or days > MAX_DAYS:
        raise ValueError(f"the life span must be between 1 and {MAX_DAYS} days")
    if not st["n"]:
        raise ValueError("that collection is empty")
    mv = None if max_views in (None, 0, "", "off") else int(max_views)
    if mv is not None and mv < 1:
        mv = 1
    token = secrets.token_urlsafe(12)                 # ni `random`, ni `uuid1`
    pw_text = make_password()
    runs_ = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with db.tx() as c:
        ident = c.execute(
            "INSERT INTO shares (album_id, token, password_hash, expires_at, "
            "  allow_download, allow_upload, keep_gps, guest_max_files, guest_max_bytes, "
            "  owner, max_views) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (st["id"], token, _PH.hash(pw_text), runs_,
             1 if allow_download else 0, 1 if allow_upload else 0,
             1 if keep_gps else 0, config.GUEST_MAX_FILES,
             config.GUEST_MAX_BYTES, owner, mv)).lastrowid
    log.info("share link %s… for collection %s, expires %s, max_views=%s",
             token[:6], st["slug"], runs_, mv)
    return {"id": ident, "token": token, "password": pw_text,
            "expires_at": runs_, "collection": st["title"], "slug": st["slug"],
            "url": f"{config.SITE_URL.rstrip('/')}/s/{token}",
            "allow_download": bool(allow_download),
            "allow_upload": bool(allow_upload), "keep_gps": bool(keep_gps),
            "max_views": mv, "n": st["n"]}


def view_count(share_id: int) -> int:
    """How often the link has been opened."""
    return db.connect().execute(
        "SELECT COUNT(*) FROM share_hits WHERE share_id=?", (share_id,)).fetchone()[0]


def used_up(row) -> bool:
    """True when the view limit is reached. `max_views` NULL = never."""
    mv = row["max_views"] if "max_views" in row.keys() else None
    return mv is not None and view_count(row["id"]) >= int(mv)


def set_views(share_id: int, max_views) -> dict:
    """Change the view limit of an existing link (owner or administrator only).
    `max_views` None/0/'off' = switch it off (unlimited)."""
    mv = None if max_views in (None, 0, "", "off") else max(1, int(max_views))
    with db.tx() as c:
        c.execute("UPDATE shares SET max_views=? WHERE id=? AND revoked_at IS NULL",
                  (mv, share_id))
    return {"id": share_id, "max_views": mv}


def owner_of(share_id: int) -> str:
    row = db.connect().execute("SELECT owner FROM shares WHERE id=?", (share_id,)).fetchone()
    return row["owner"] if row else None


def new_password(share_id: int) -> dict:
    """A new password, **the same link**. For when it has been lost."""
    pw_text = make_password()
    with db.tx() as c:
        n = c.execute("UPDATE shares SET password_hash=?, fail_count=0, "
                      "locked_until=NULL WHERE id=? AND revoked_at IS NULL",
                      (_PH.hash(pw_text), share_id)).rowcount
    if not n:
        raise ValueError("no such link (or it is already revoked)")
    return {"id": share_id, "password": pw_text}


def revoke(share_id: int) -> dict:
    with db.tx() as c:
        n = c.execute("UPDATE shares SET revoked_at=datetime('now') "
                      "WHERE id=? AND revoked_at IS NULL", (share_id,)).rowcount
    return {"revoked": bool(n)}


def _row(token: str):
    if not TOKEN_RE.match(token or ""):
        return None
    return db.connect().execute(
        "SELECT s.*, a.slug, a.title, "
        "       (SELECT COUNT(*) FROM album_photos p WHERE p.album_id=s.album_id) AS n "
        "FROM shares s JOIN albums a ON a.id=s.album_id WHERE s.token=?",
        (token,)).fetchone()


def state(token: str) -> dict:
    """`{ok, reason, row}` -- what is up with this link, without a password."""
    row = _row(token)
    if row is None:
        return {"ok": False, "reason": "unknown"}
    if row["revoked_at"]:
        return {"ok": False, "reason": "revoked", "row": row}
    if row["expires_at"] <= datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
        return {"ok": False, "reason": "expired", "row": row}
    if row["locked_until"] and row["locked_until"] > datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"):
        return {"ok": False, "reason": "locked", "row": row}
    return {"ok": True, "row": row}


def check_password(token: str, pw_text: str) -> dict:
    """Check the password, with the brake.

    ⚠ The brake hangs off the **token**, not off the address: the client
    address cannot be established here (see above), and a brake somebody gets
    around by changing a header is not a brake.
    """
    st = state(token)
    if not st["ok"]:
        return st
    row = st["row"]
    try:
        _PH.verify(row["password_hash"], pw_text or "")
    except (VerifyMismatchError, VerificationError):
        shut = drifted = False
        with db.tx() as c:
            c.execute("UPDATE shares SET fail_count=fail_count+1 WHERE id=?", (row["id"],))
            n = c.execute("SELECT fail_count FROM shares WHERE id=?",
                          (row["id"],)).fetchone()[0]
            if n >= FAIL_REVOKE:
                c.execute("UPDATE shares SET revoked_at=datetime('now') WHERE id=?",
                          (row["id"],))
                drifted = True
                log.warning("share link %s… retired after %d wrong tries", token[:6], n)
            elif n % FAIL_LOCK == 0:
                c.execute("UPDATE shares SET locked_until=datetime('now', ?) WHERE id=?",
                          (f"+{LOCK_MINUTES} minutes", row["id"]))
                shut = True
                log.warning("share link %s… %d wrong tries -- shut for %d min",
                            token[:6], n, LOCK_MINUTES)
        # ⚠ The attempt that triggers the lock is told "closed", not "wrong"
        # again. Otherwise somebody tries once more and does not understand
        # why nothing works any more.
        if drifted:
            return {"ok": False, "reason": "revoked", "tries": n}
        if shut:
            return {"ok": False, "reason": "locked", "tries": n}
        return {"ok": False, "reason": "wrong", "tries": n}
    with db.tx() as c:
        c.execute("UPDATE shares SET fail_count=0, locked_until=NULL WHERE id=?",
                  (row["id"],))
    return {"ok": True, "row": row}


# ---------------------------------------------------------------------------
#  The cookie -- whoever typed the password should not have to type it again
#  for every single image
# ---------------------------------------------------------------------------
def _secret() -> bytes:
    """Derived from the shared secret, with a separate purpose string.

    ⚠ The purpose string matters: it is what stops a cookie from ever passing
    as the shared secret, or the other way round."""
    basis = (config.proxy_secret() or "").encode()
    return hmac.new(basis, b"family-share-cookie-v1", hashlib.sha256).digest()


def cookie_name(token: str) -> str:
    return "fam_s_" + hashlib.sha256(token.encode()).hexdigest()[:16]


def _pw_mark(token: str) -> str:
    """A short fingerprint of the current password hash.

    ⚠ It goes into the cookie signature. Without it, a new password did **not**
    throw out the people who had already opened the link -- they stayed in for
    up to twelve hours. Meanwhile the form said "anyone holding the old
    password can no longer open it", which was not true.
    """
    row = db.connect().execute(
        "SELECT password_hash FROM shares WHERE token=?", (token,)).fetchone()
    base = (row["password_hash"] if row else "").encode()
    return hmac.new(_secret(), b"pw|" + base, hashlib.sha256).hexdigest()[:16]


def sign(token: str, until: str) -> str:
    mac = hmac.new(_secret(), f"{token}|{until}|{_pw_mark(token)}".encode(),
                   hashlib.sha256).hexdigest()
    return f"{until}|{mac}"


def valid_cookie(token: str, value_: str) -> bool:
    try:
        until, mac = (value_ or "").split("|", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(sign(token, until), value_):
        return False
    return until > datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
#  Wat um Site steet
# ---------------------------------------------------------------------------
def note_hit(share_id: int, ua: str = "") -> None:
    with db.tx() as c:
        c.execute("INSERT INTO share_hits (share_id, ua) VALUES (?,?)",
                  (share_id, (ua or "")[:200]))


def photo_ids(share_id: int) -> set:
    """The ids that belong to THIS link.

    ⚠ Asked on **every** image request. Without it, somebody could push a
    photo id from another link through this one -- one share reading another."""
    row = db.connect().execute("SELECT album_id FROM shares WHERE id=?",
                               (share_id,)).fetchone()
    if row is None:
        return set()
    return {r["photo_id"] for r in db.connect().execute(
        "SELECT ap.photo_id FROM album_photos ap JOIN photos p ON p.id=ap.photo_id "
        "WHERE ap.album_id=? AND p.state='ok' AND p.hidden=0", (row["album_id"],))}


def all_of(owner: str = None) -> list:
    where_ = " WHERE s.owner=?" if owner else ""
    args = (owner,) if owner else ()
    out = []
    for r in db.connect().execute(
            "SELECT s.*, a.title, a.slug, "
            "       (SELECT COUNT(*) FROM share_hits h WHERE h.share_id=s.id) AS opens, "
            "       (SELECT MAX(at) FROM share_hits h WHERE h.share_id=s.id) AS last_open, "
            "       (SELECT COUNT(*) FROM share_uploads u WHERE u.share_id=s.id "
            "        AND u.state='guest') AS waiting, "
            "       (SELECT COUNT(*) FROM album_photos p WHERE p.album_id=s.album_id) AS n "
            "FROM shares s JOIN albums a ON a.id=s.album_id" + where_ +
            " ORDER BY s.id DESC", args):
        d = dict(r)
        d["state"] = ("revoked" if d["revoked_at"] else
                      "expired" if d["expires_at"] <=
                      datetime.now().strftime("%Y-%m-%d %H:%M:%S") else
                      "used up" if (d.get("max_views") and d["opens"] >= d["max_views"])
                      else "live")
        d["url"] = f"{config.SITE_URL.rstrip('/')}/s/{d['token']}"
        out.append(d)
    return out


def purge_expired() -> dict:
    """Expired means expired: the quarantine of expired links is cleared out."""
    import shutil
    n = 0
    for r in db.connect().execute(
            "SELECT id, token FROM shares WHERE revoked_at IS NOT NULL "
            "OR expires_at <= datetime('now')"):
        d = config.INCOMING_DIR / f"share-{r['token']}"
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return {"cleared": n}
