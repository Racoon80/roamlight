"""The people -- who exists, and which groups they are in.

Two sources, and the order is deliberate:

1. **From the identity provider**, through an account that can only READ. That
   way the site knows about everybody **before** they have ever signed in --
   which is the whole point: you should be able to set the rights on an album
   without the person having to log in first.
2. **From the headers** of every sign-in. Even without a token the site knows
   who has been here since the first login.

⚠ The reader account needs exactly **two** permissions: view_user and
view_group. Verified: creating, changing, flows, providers -- all 403. So if
this site is ever compromised, the single sign-on is **not** compromised with
it. That is the entire reason for a separate token.

Everything is stored locally: a pass over the rights must not depend on the
directory being up, and a page must not make a network call.

⚠ Only used with FAMILY_AUTH=proxy. With local accounts the people are in this
site's own database and there is nothing to fetch.
"""
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

from . import config, db

log = logging.getLogger("family")


def _token() -> str:
    p = getattr(config, "AUTHENTIK_TOKEN_FILE", None)
    if not p:
        return ""
    try:
        return Path(p).read_text().strip()
    except OSError:
        return ""


def configured() -> bool:
    return bool(config.AUTHENTIK_URL and _token())


def _call(path: str):
    tok = _token()
    r = urllib.request.Request(config.AUTHENTIK_URL.rstrip("/") + path)
    r.add_header("Authorization", "Bearer " + tok)
    r.add_header("Accept", "application/json")
    with urllib.request.urlopen(r, timeout=20) as f:
        return json.loads(f.read() or b"null")


def relevant_groups() -> set:
    """The groups this site has any business knowing about.

    Those are the administrator and viewer groups from the configuration, and
    nothing else. Somebody in none of them cannot reach the site anyway.
    """
    return set(config.ADMIN_GROUPS) | set(config.VIEWER_GROUPS)


def refresh() -> dict:
    """Fetch the people from the directory and store them locally.

    ⚠ The user list is **never asked for**. What is asked for are the groups,
    and out of those only the members of the administrator and viewer groups
    are taken.

    That is more than filtering after the download: `/core/users/` is **never**
    called by this site. Somebody who is in none of those groups never comes
    past this point -- and that holds for every account of every other program
    sharing the same directory.

    It is **not** called automatically: somebody presses a button. A page
    waiting on a foreign machine is a page that hangs.
    """
    if not configured():
        return {"ok": False, "error": "no Authentik token configured"}
    important = relevant_groups()
    try:
        groups = _call("/core/groups/?page_size=500")["results"]
    except (urllib.error.URLError, KeyError, ValueError) as exc:
        log.warning("Authentik: %s", exc)
        return {"ok": False, "error": str(exc)}

    # Who is in which group? The groups carry the members, not the other way round.
    in_groups, folks = {}, {}
    for g in groups:
        for_us = g["name"] in important
        for u in (g.get("users_obj") or []):
            uname = u.get("username")
            if not uname:
                continue
            in_groups.setdefault(uname, []).append(g["name"])
            if for_us:
                folks[uname] = u

    rows = []
    for uname, u in folks.items():
        if u.get("type") in ("service_account", "internal_service_account"):
            continue                       # Maschinnen sinn keng Familljememberen
        rows.append((
            uname, u.get("name") or uname, u.get("email") or "",
            1 if u.get("is_active") else 0,
            # ⚠ ONLY this site's groups. That somebody is also in
            # `frigate_admins` or `howler_users` is none of this site's
            # business -- and a photo library should not build a list of who
            # is in which other program.
            json.dumps(sorted(set(in_groups.get(uname, [])) & important),
                       ensure_ascii=False),
        ))
    with db.tx() as c:
        c.execute("UPDATE members SET seen_in_authentik=0")
        # ⚠ Somebody no longer in one of the groups is NOT deleted here: their
        # row stays with `seen_in_authentik=0`. Otherwise a viewing list that
        # hangs on their name would quietly lose it -- and the album would
        # suddenly be open to everyone.
        c.executemany(
            "INSERT INTO members (username, display_name, email, active, groups_json, "
            "                     seen_in_authentik, updated_at) "
            "VALUES (?,?,?,?,?,1,datetime('now')) "
            "ON CONFLICT(username) DO UPDATE SET display_name=excluded.display_name, "
            "  email=excluded.email, active=excluded.active, "
            "  groups_json=excluded.groups_json, seen_in_authentik=1, "
            "  updated_at=datetime('now')", rows)
    db.set_state("members_refreshed", "now")
    log.info("Authentik: %d Memberen aus %d Gruppen (vun %d)",
             len(rows), len(important), len(groups))
    return {"ok": True, "members": len(rows),
            "groups": sorted(important), "groups_seen": len(groups)}


def note_seen(username: str, email: str, groups) -> None:
    """Whoever signs in is noted. No token, no network."""
    if not username:
        return
    with db.tx() as c:
        c.execute(
            "INSERT INTO members (username, display_name, email, active, groups_json, "
            "                     last_seen_at, updated_at) "
            "VALUES (?,?,?,1,?,datetime('now'),datetime('now')) "
            "ON CONFLICT(username) DO UPDATE SET "
            "  email=CASE WHEN excluded.email<>'' THEN excluded.email ELSE members.email END, "
            # ⚠ The groups are ONLY overwritten when the row did NOT come from
            # the directory. A header is what ONE login brought along; the
            # directory is the truth. This line used to be
            # `groups_json=excluded.groups_json` -- and one single request
            # whose header carried only the viewer group cut an administrator's
            # three groups down to that one.
            "  groups_json=CASE WHEN members.seen_in_authentik=1 "
            "                   THEN members.groups_json ELSE excluded.groups_json END, "
            "  last_seen_at=datetime('now')",
            (username, username, email or "",
             json.dumps(sorted(groups or []), ensure_ascii=False)))


def all_of() -> list:
    out = []
    for r in db.connect().execute(
            "SELECT * FROM members ORDER BY active DESC, display_name COLLATE NOCASE"):
        d = dict(r)
        try:
            d["groups"] = json.loads(d.get("groups_json") or "[]")
        except ValueError:
            d["groups"] = []
        out.append(d)
    return out


def group_names() -> list:
    """The groups for the audience picker -- **this site's groups only**.

    This used to list every group any of the people were in: groups belonging
    to other programs that have nothing to do with photographs. A list of forty
    tick boxes, five of them useful, is not help -- it is a place to make a
    mistake.
    """
    return sorted(relevant_groups())


def pickable() -> list:
    """Who belongs in the viewing list of an album.

    ⚠ **Without the administrators.** They see every album anyway -- a tick
    next to an administrator would be a lie: it would look as though it
    changed something.
    """
    admin = set(config.ADMIN_GROUPS)
    return [m for m in all_of()
            if m.get("active") and not (set(m["groups"]) & admin)]


def group_choices() -> list:
    """The groups for the picker -- again without the administrator groups."""
    return [g for g in group_names() if g not in set(config.ADMIN_GROUPS)]


def admin_names() -> set:
    """The user names of the administrators.

    Needed by the templates: a tick that is on a list but has no row in the
    picker is shown as a stray -- and an administrator must not appear as a
    stray. They see it anyway.
    """
    admin = set(config.ADMIN_GROUPS)
    return {m["username"] for m in all_of() if set(m["groups"]) & admin}
