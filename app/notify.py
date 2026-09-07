"""Wien kritt Bescheed, wéini -- an virun allem: wéi dacks.

⚠ THE POINT OF THIS FILE IS THE COUNTER, NOT THE SENDING. Sending a message is
  twenty lines. The hard part is that a family import of five hundred
  photographs must be ONE line on somebody's lock screen and not five hundred,
  and that nobody hears about their own upload.

How it works: nothing is ever sent at the moment it happens. It goes into
`notify_pending` -- one row per person, per kind of event, per album -- and the
counter goes up. `flush()` picks up rows whose small window has passed and
sends one message for each.

⚠ The window is set when the row is MADE and never moved. Extending it on every
  new photograph looks tidier and is wrong: a long import would keep pushing the
  moment away and nothing would ever be announced.
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from . import config, db

log = logging.getLogger("family")

# How long the counter gathers before the message goes out.
WINDOW_SECONDS = int(getattr(config, "NOTIFY_WINDOW", 90))


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stamp(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# --- Wien däerf en Album gesinn -------------------------------------------

def _audience(album_key: str, actor: str) -> list:
    """Everyone who may see this album -- except whoever caused the event.

    ⚠ `album_acl` is the list of names, and an empty list means the album is
      not published to anybody. Administrators see everything, so they are in
      it too -- an administrator who does not want to hear about every upload
      turns the whole thing off for themselves, which is a setting, not a
      guess made here.
    """
    conn = db.connect()
    named, wanted_groups = set(), set()
    for r in conn.execute("SELECT principal FROM album_acl WHERE album_key=?", (album_key,)):
        p = (r["principal"] or "").strip()
        if p.lower().startswith("user:"):
            named.add(p[5:])
        elif p.lower().startswith("group:"):
            # ⚠ A group is a first-class name on that list -- `group:family` is
            #   what the tick boxes on the album page write, and it is how an
            #   album is normally published. Reading only `user:` rows meant
            #   that for an ordinary album the audience was the administrators
            #   and nobody else: the family heard nothing at all.
            wanted_groups.add(p[6:])

    people = set(named)
    for r in conn.execute("SELECT username, groups_json FROM members WHERE active=1"):
        try:
            groups = set(json.loads(r["groups_json"] or "[]"))
        except (ValueError, TypeError):
            groups = set()
        if config.ADMIN_GROUPS.intersection(groups) or wanted_groups.intersection(groups):
            people.add(r["username"])
    people.discard(actor)
    people.discard("")
    return sorted(people)


# --- Wat geschitt ass ------------------------------------------------------

def note(event: str, album_key: str, actor: str = "", n: int = 1) -> int:
    """Something happened. Count it; do not send anything.

    Returns how many people it was counted for -- so a test can see it without
    having to wait for the window.
    """
    if event not in ("photos", "access"):
        return 0
    who = _audience(album_key, actor)
    if not who:
        return 0
    due = _stamp(_now() + timedelta(seconds=WINDOW_SECONDS))
    with db.tx() as c:
        for user in who:
            c.execute(
                "INSERT INTO notify_pending (username, event, album_key, n, send_after) "
                "VALUES (?, ?, ?, ?, ?) "
                # ⚠ `send_after` is NOT touched here. See the note at the top.
                "ON CONFLICT (username, event, album_key) DO UPDATE SET n = n + excluded.n",
                (user, event, album_key, n, due))
    return len(who)


def note_access(album_key: str, user: str, actor: str = "") -> int:
    """One person was let into one album."""
    if not user or user == actor:
        return 0
    due = _stamp(_now() + timedelta(seconds=WINDOW_SECONDS))
    with db.tx() as c:
        c.execute(
            "INSERT INTO notify_pending (username, event, album_key, n, send_after) "
            "VALUES (?, 'access', ?, 1, ?) "
            "ON CONFLICT (username, event, album_key) DO NOTHING",
            (user, album_key, due))
    return 1


# --- Wat drasteet ----------------------------------------------------------

def _album_title(album_key: str) -> str:
    parts = album_key.split("/")
    if len(parts) >= 3:
        from . import gallery
        return gallery.album_title(parts[0], "/".join(parts[2:]))
    return album_key


def message(row) -> tuple:
    """(title, body) for one pending row. One sentence, in plain words."""
    album = _album_title(row["album_key"])
    if row["event"] == "access":
        return ("Roamlight", f"You can now see {album}.")
    n = int(row["n"] or 0)
    if n <= 1:
        return (album, "One new photograph.")
    return (album, f"{n} new photographs.")


# --- Ofschécken ------------------------------------------------------------

def _devices(username: str) -> list:
    return db.connect().execute(
        "SELECT * FROM notify_devices WHERE username=? AND failures < 5",
        (username,)).fetchall()


class NotReady(Exception):
    """The way out is not set up, or is having a bad day.

    ⚠ Its own kind, because it says nothing about the PHONE. Counting it as a
      failure of the device was enough to bury every phone in the house: five
      pending rows with APNs unconfigured and `failures` reaches five, after
      which `_devices()` hides them and only a re-registration brings them
      back.
    """


def flush(limit: int = 200) -> dict:
    """Send what is due. Returns a small tally, so a caller can log it.

    ⚠ The row goes whether the sending worked or not. A message about a
      photograph from an hour ago, retried until it succeeds, is worse than no
      message: the phone was off, the moment is gone, and a queue that never
      empties eventually announces yesterday.
    """
    sent = failed = dropped = 0
    # ⚠ With no way out at all, do not touch the queue. Sending would fail for
    #   every row, and every row would be deleted anyway -- the whole backlog
    #   would quietly evaporate while somebody was still fetching a key.
    if not (config.apns_ready() or config.fcm_ready()):
        return {"rows": 0, "sent": 0, "failed": 0, "no_device": 0, "no_transport": True}
    rows = db.connect().execute(
        "SELECT * FROM notify_pending WHERE send_after <= ? ORDER BY id LIMIT ?",
        (_stamp(_now()), limit)).fetchall()
    for row in rows:
        title, body = message(row)
        targets = _devices(row["username"])
        if not targets:
            dropped += 1
        for d in targets:
            try:
                deliver(d, title, body,
                        {"album": row["album_key"], "event": row["event"]})
                sent += 1
                with db.tx() as c:
                    c.execute("UPDATE notify_devices SET last_ok=datetime('now'), "
                              "failures=0 WHERE id=?", (d["id"],))
            except Unregistered:
                # The phone said this address is dead. Take it out; it will
                # never work again.
                with db.tx() as c:
                    c.execute("DELETE FROM notify_devices WHERE id=?", (d["id"],))
                failed += 1
            except NotReady as exc:
                # Nothing to do with this phone -- do not hold it against it.
                log.warning("notify: %s not ready -- %s", d["kind"], exc)
                failed += 1
            except Exception as exc:                             # noqa: BLE001
                log.warning("notify: %s failed -- %s", d["kind"], exc)
                with db.tx() as c:
                    c.execute("UPDATE notify_devices SET failures=failures+1 WHERE id=?",
                              (d["id"],))
                failed += 1
        # ⚠ `AND n=?`: a photograph that arrived WHILE this was being sent has
        #   already put the counter up. Deleting the row on the id alone would
        #   throw that increment away and those photographs would never be
        #   announced. With the count in the condition the row simply survives
        #   and goes out on the next pass with the remainder.
        #
        # ⚠ It is also what stops two flushes sending the same row twice: the
        #   second one finds nothing to delete and, having deleted nothing,
        #   knows it lost the race.
        with db.tx() as c:
            c.execute("DELETE FROM notify_pending WHERE id=? AND n=?",
                      (row["id"], row["n"]))
    return {"rows": len(rows), "sent": sent, "failed": failed, "no_device": dropped}


class Unregistered(Exception):
    """The address is dead for good -- the app was removed, or the token rolled."""


def deliver(device, title: str, body: str, data: dict) -> None:
    """One message to one phone. The transports live in `push.py`."""
    from . import push
    if device["kind"] == "apns":
        push.apns(device["token"], title, body, data)
    elif device["kind"] == "fcm":
        push.fcm(device["token"], title, body, data)
    else:
        raise ValueError(f"unknown kind {device['kind']!r}")


# --- Umellen ---------------------------------------------------------------

def register(username: str, kind: str, token: str, name: str = "") -> dict:
    if kind not in ("apns", "fcm") or not token:
        raise ValueError("kind has to be apns or fcm, and a token is needed")
    with db.tx() as c:
        # ⚠ The same phone may come back with the same address after a
        #   reinstall, and it may have belonged to somebody else before -- a
        #   family shares phones. So the row is claimed, not duplicated.
        #
        # ⚠ Claiming it is safe here BECAUSE Apple mints a new address per app
        #   install: whoever holds it really is the one with the app in their
        #   hand. What is NOT safe is the other direction, and that is why
        #   `unregister` is bound to the owner -- naming a token you do not own
        #   must not switch off somebody else's notices.
        c.execute(
            "INSERT INTO notify_devices (username, kind, token, name) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (kind, token) DO UPDATE SET username=excluded.username, "
            "name=excluded.name, failures=0",
            (username, kind, token, name[:60]))
    return {"ok": True}


def unregister(username: str, kind: str, token: str) -> dict:
    """⚠ Only one's own. A push token is not a secret -- it travels to Apple and
    to this site -- so anybody signed in could otherwise switch off somebody
    else's notices simply by naming their token."""
    with db.tx() as c:
        c.execute("DELETE FROM notify_devices WHERE kind=? AND token=? AND username=?",
                  (kind, token, username))
    return {"ok": True}


def devices_of(username: str) -> list:
    return [{"kind": r["kind"], "name": r["name"], "created_at": r["created_at"],
             "last_ok": r["last_ok"]} for r in _devices(username)]
