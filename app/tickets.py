"""E kuerzliewegen Ticket fir GENEE eng Adress.

Why this exists: a video is played by the operating system's own player --
`AVPlayer` on iOS, the platform player on Android -- and neither of them will
put an `Authorization` header on the requests it makes. iOS has a private
option for it; using a private key in a shipped app is how an app stops being
shippable one release later.

So the address carries its own proof instead. The app asks the site for a
ticket, the site signs *that one address* for a quarter of an hour, and the
player fetches it with no header at all. Range requests keep working, because
the query string travels with them.

⚠ THE SIGNATURE COVERS THE PATH. A ticket for
  `/photos/12/video.mp4` is worthless on `/photos/13/video.mp4`, and worthless
  on `/api/albums`. That is the whole point: what leaks is one video for
  fifteen minutes, not an account.

⚠ AND IT COVERS THE PERSON. The ticket says who asked for it, and the ordinary
  album permissions are then applied to that person -- a ticket does not widen
  what its owner may see, it only carries the identity to a player that cannot.
"""
import base64
import hashlib
import hmac
import secrets
import time

from . import db

MINUTES = 15
_KEY_NAME = "ticket_key"


def _key() -> bytes:
    """The signing key, made once and kept in the database.

    ⚠ In the database and not in a file: it has to survive a restart (a ticket
      minted a minute ago must still work) and it must NOT survive a restore
      onto a different machine as something an attacker could have seen. It is
      never sent anywhere.
    """
    raw = db.get_state(_KEY_NAME)
    if not raw:
        raw = secrets.token_urlsafe(32)
        db.set_state(_KEY_NAME, raw)
    return raw.encode()


def _sign(path: str, exp: int, user: str) -> str:
    msg = f"{path}|{exp}|{user}".encode()
    return hmac.new(_key(), msg, hashlib.sha256).hexdigest()[:40]


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _unb64(s: str) -> str:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


def mint(path: str, user: str, minutes: int = MINUTES) -> str:
    """A ticket for this one address, for this one person."""
    exp = int(time.time()) + minutes * 60
    return f"{exp}.{_b64(user)}.{_sign(path, exp, user)}"


def user_for(path: str, ticket: str) -> str | None:
    """Whose ticket is this -- or None if it is not one, or not for here."""
    if not ticket or ticket.count(".") != 2:
        return None
    exp_s, user_b64, sig = ticket.split(".", 2)
    try:
        exp = int(exp_s)
        user = _unb64(user_b64)
    except (ValueError, UnicodeDecodeError):
        return None
    if exp < time.time():
        return None
    # compare_digest, because otherwise how long the comparison takes says how
    # many characters were right.
    if not hmac.compare_digest(sig, _sign(path, exp, user)):
        return None
    return user
