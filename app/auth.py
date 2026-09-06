"""Signing in with a name and a password -- for installations without SSO.

The site was built for one road: an identity proxy in front sets the headers,
and the app trusts them because the shared secret matches. That is right for a
house that already runs such a proxy, and useless for everybody who simply
installs this.

So: a second road. Both can run side by side (`FAMILY_AUTH=local+proxy`), and
both end in the same place -- `security.identify()` returns an `Identity`, and
no permission check in the site knows which road it came from.

⚠ **Argon2 here, sha256 for device tokens.** A password is chosen by a person:
  every guess has to hurt. A session secret is 32 random bytes -- nothing is
  guessed there, and argon2 would cost 50 ms on every single request, including
  each of the sixty images on a gallery page. Same reasoning as `devices.py`.

⚠ **A brake, not a lock.** Closing an account after N failures lets a stranger
  break something they do not own: they type the wrong password three times and
  the owner is locked out of their own site. So the ADDRESS is slowed down (see
  `blocked()` below) -- and that has to happen in the program, because in
  Docker and in an LXC there is no proxy in front to do it.
"""
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from . import config, db

# Same settings as the share-link passwords (shares.py): 64 MB, three passes.
_PH = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)

MIN_PASSWORD = 10


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# After MAX_TRIES failures from the same address, nothing works from there for
# LOCK_MINUTES.
MAX_TRIES = 10
LOCK_MINUTES = 10
_fails: dict = {}


def _now() -> float:
    import time
    return time.monotonic()


def blocked(ip: str) -> bool:
    tries = [t for t in _fails.get(ip, []) if t > _now() - LOCK_MINUTES * 60]
    if tries:
        _fails[ip] = tries
    else:
        _fails.pop(ip, None)
    return len(tries) >= MAX_TRIES


def note_fail(ip: str) -> None:
    if not ip:
        return
    # Bounded on purpose: what falls out of the window falls out of memory.
    if len(_fails) > 5000:
        _fails.clear()
    _fails.setdefault(ip, []).append(_now())


def note_ok(ip: str) -> None:
    _fails.pop(ip, None)


# --- accounts ----------------------------------------------------------------

def has_local_users() -> bool:
    """Is there any account at all? If not, the site shows the first-run page."""
    return bool(db.connect().execute(
        "SELECT 1 FROM members WHERE is_local=1 AND password_hash IS NOT NULL "
        "LIMIT 1").fetchone())


def create_user(username: str, password: str, display_name: str = "",
                groups=None, email: str = "") -> None:
    """Create a local account, or set the password of one that exists.

    `groups` are the group names from the configuration (admin / viewer /
    contributor) -- exactly the names a directory would otherwise supply. That
    is what lets the permission logic stay ignorant of where an account came
    from.
    """
    username = (username or "").strip().lower()
    if not username:
        raise ValueError("a name is needed")
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"the password needs at least {MIN_PASSWORD} characters")
    import json
    with db.tx() as c:
        c.execute(
            "INSERT INTO members (username, display_name, email, active, groups_json, "
            "  password_hash, is_local) VALUES (?,?,?,1,?,?,1) "
            "ON CONFLICT(username) DO UPDATE SET "
            "  password_hash=excluded.password_hash, is_local=1, active=1, "
            "  display_name=CASE WHEN excluded.display_name<>'' "
            "                    THEN excluded.display_name ELSE members.display_name END, "
            "  groups_json=excluded.groups_json",
            (username, (display_name or username).strip(), (email or "").strip(),
             json.dumps(sorted(groups or [])), _PH.hash(password)))


def set_password(username: str, password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"the password needs at least {MIN_PASSWORD} characters")
    with db.tx() as c:
        n = c.execute("UPDATE members SET password_hash=?, is_local=1 WHERE username=?",
                      (_PH.hash(password), (username or "").strip().lower())).rowcount
    if not n:
        raise ValueError("no such person")


def check(username: str, password: str) -> str | None:
    """Check a name and a password. Returns the user name, or `None`.

    ⚠ The reason is never given away: "no such account" and "wrong password"
    get the same answer. Otherwise the sign-in page is a list of who has an
    account here.
    """
    username = (username or "").strip().lower()
    row = db.connect().execute(
        "SELECT username, password_hash FROM members "
        "WHERE username=? AND active=1 AND password_hash IS NOT NULL",
        (username,)).fetchone()
    if row is None:
        # ⚠ Hash anyway. Otherwise an account that does not exist answers in
        #   a millisecond and one with a password takes fifty -- and the time
        #   itself is the answer to "does this person have an account?".
        _PH.hash(password or "x")
        return None
    try:
        _PH.verify(row["password_hash"], password or "")
    except (VerifyMismatchError, VerificationError):
        return None
    # Argon2's recommended parameters get harder over time; when they do, the
    # stored hash is brought up to date on the next correct sign-in.
    if _PH.check_needs_rehash(row["password_hash"]):
        try:
            set_password(username, password)
        except ValueError:
            pass
    return row["username"]


# --- sessions ----------------------------------------------------------------

def new_session(username: str, agent: str = "") -> str:
    """Start a session. Returns the cookie value (`<ref>.<secret>`)."""
    ref = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    with db.tx() as c:
        c.execute(
            "INSERT INTO sessions (ref, username, secret_hash, expires_at, agent) "
            "VALUES (?,?,?,datetime('now',?),?)",
            (ref, username, _sha(secret), f"+{config.SESSION_DAYS} days", (agent or "")[:120]))
    return f"{ref}.{secret}"


def session_user(cookie: str) -> str | None:
    """The user behind a cookie, or `None`."""
    if not cookie or "." not in cookie:
        return None
    ref, secret = cookie.split(".", 1)
    row = db.connect().execute(
        "SELECT username, secret_hash FROM sessions "
        "WHERE ref=? AND expires_at > datetime('now')", (ref,)).fetchone()
    if row is None or not hmac.compare_digest(row["secret_hash"], _sha(secret)):
        return None
    return row["username"]


def end_session(cookie: str) -> None:
    if cookie and "." in cookie:
        with db.tx() as c:
            c.execute("DELETE FROM sessions WHERE ref=?", (cookie.split(".", 1)[0],))


def end_all(username: str) -> int:
    """End every session of one person -- after a password change, say."""
    with db.tx() as c:
        return c.execute("DELETE FROM sessions WHERE username=?", (username,)).rowcount or 0


def sweep() -> int:
    """Drop expired sessions. Runs at start-up."""
    with db.tx() as c:
        return c.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')").rowcount or 0


def touch(cookie: str) -> None:
    """Note that a session is in use -- at most once an hour, so that reading a
    gallery page does not turn into sixty writes."""
    if cookie and "." in cookie:
        try:
            with db.tx() as c:
                c.execute("UPDATE sessions SET last_seen=datetime('now') WHERE ref=? "
                          "AND (last_seen IS NULL OR last_seen < datetime('now','-1 hour'))",
                          (cookie.split(".", 1)[0],))
        except Exception:                                        # noqa: BLE001
            pass
