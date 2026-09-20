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
    #
    # ⚠ It used to be `_fails.clear()`, and that handed the throttle to whoever
    #   wanted it: an attacker walks through 5001 invented addresses, the whole
    #   table is emptied, and EVERY counter -- including the one counting their
    #   own guesses -- is back at nought.
    #
    # ⚠ And shedding the OLDEST is no better, which is the mistake this comment
    #   exists to stop somebody making again. The oldest entry is the one that
    #   has been blocked longest; the newest are the thousands of invented
    #   addresses the attacker is writing right now. Evicting by age therefore
    #   throws away exactly the block that matters and keeps the noise.
    #
    #   What has to survive is a COUNT that is holding somebody back. So the
    #   order is: entries outside the window first (they hold nobody back),
    #   then the ones with the fewest failures. An address with one failure is
    #   worth nothing to keep; one that has reached MAX_TRIES is the whole
    #   point of the table.
    if len(_fails) > 5000:
        cutoff = _now() - LOCK_MINUTES * 60
        for k in [k for k, v in _fails.items() if not v or max(v) <= cutoff]:
            _fails.pop(k, None)
        if len(_fails) > 5000:
            order = sorted(_fails, key=lambda k: (len(_fails[k]), max(_fails[k])))
            for k in order[:len(_fails) - 4000]:
                _fails.pop(k, None)
    _fails.setdefault(ip, []).append(_now())


def note_ok(ip: str) -> None:
    _fails.pop(ip, None)


# --- accounts ----------------------------------------------------------------

def has_local_users() -> bool:
    """Is there any account at all? If not, the site shows the first-run page."""
    return bool(db.connect().execute(
        "SELECT 1 FROM members WHERE is_local=1 AND password_hash IS NOT NULL "
        "LIMIT 1").fetchone())


def has_local_admin() -> bool:
    """Is there somebody with a PASSWORD who can administer this site?

    ⚠ Not the same question as `has_local_users()`, and the difference is what
      makes it worth its own function: an installation can have a password
      account that may only look, and switching single sign-on off there leaves
      a site nobody can manage. It is asked before the settings page is allowed
      to take the proxy road away.

    ⚠ The group names come from the configuration, so this asks what THIS
      installation calls an administrator, not what the code guesses.
    """
    import json
    from . import config
    for row in db.connect().execute(
            "SELECT groups_json FROM members "
            "WHERE is_local=1 AND active=1 AND password_hash IS NOT NULL"):
        try:
            groups = set(str(g) for g in json.loads(row["groups_json"] or "[]"))
        except (ValueError, TypeError):
            continue
        if config.ADMIN_GROUPS.intersection(groups):
            return True
    return False


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


def end_all(username: str, devices: bool = True) -> int:
    """End every way that person is currently signed in.

    ⚠ Sessions AND device tokens. It was sessions only, and both callers told
      the person "every session of that person ends" -- which was true and
      useless: a paired phone carries a `fam_…` token that never expires and is
      checked on every request. Somebody who had the old password could mint one
      through `/api/app/login` and keep full access after the password was
      changed, until a human noticed the device on the phone page.

      A password is changed either because it was forgotten or because somebody
      else might have it. In the second case a revocation that leaves the
      phones alone has revoked nothing.

    ⚠ `devices=False` is for the case where only the browser side should go.
      Nothing uses it today; it exists so that a future caller has to say so.
    """
    with db.tx() as c:
        n = c.execute("DELETE FROM sessions WHERE username=?", (username,)).rowcount or 0
    if devices:
        # ⚠ Through `devices`, not with an UPDATE from here. `devices.identify()`
        #   answers out of an in-memory cache for thirty seconds, and a row
        #   marked revoked behind that cache is a token that still works. The
        #   module that keeps the cache is the one that may empty it.
        from . import devices as _dev
        n += _dev.revoke_all(username)
    return n


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
