"""Who sees which album.

This is a **real** separation, not a tidy-up: somebody who is not on the list
does not see the album -- and not a single photograph out of it either, not
through search, not on the map, not in a collection, and not by guessing an
image address.

Three rules, and all three matter:

1. **No row means nobody.** An album with no list is visible to the
   administrator only.

   ⚠ That is the opposite of what stood here first. The worry was that a bug
   while saving would close the library without anybody noticing. That worry is
   real -- but it is the SMALL one. The big one is a photograph reaching
   somebody who should not see it, and nobody noticing that at all. So: closed
   until somebody opens it, never the other way round.

2. **The administrator sees everything** and is not on any list. A tick next to
   an administrator would be a lie: they see it either way.

3. **The check lives in SQL**, not in Python. A check you can forget to call is
   not a check -- so it hangs off `gallery._where()` and `serve.allowed()`, and
   those two are the only roads to a photograph.
"""
import logging

from . import db

log = logging.getLogger("family")


def key(year, country, event) -> str:
    return f"{year}/{country}/{event}"


def principals_for(ident) -> list:
    """The handful of names somebody has access under: themselves and their
    groups."""
    out = [f"user:{ident.user}"] if getattr(ident, "user", "") else []
    out += [f"group:{g}" for g in (getattr(ident, "groups", None) or [])]
    return out


def sql_clause(ident, prefix: str = ""):
    """`(SQL, args)` to hang onto a `WHERE`.

    It reads as: *one of the viewer\'s names is on this album\'s list.*

    `prefix` is the table alias when the query is a join (`"ph."`). It is a
    parameter here and not a text substitution afterwards -- a clause you
    rewrite with `.replace()` is a clause that eventually rewrites the wrong
    thing.
    """
    if ident is None or getattr(ident, "is_admin", False):
        return "", []
    who = principals_for(ident)
    album_key = (f"coalesce({prefix}album_year,'—')||'/'||coalesce({prefix}country,'—')"
                 f"||'/'||coalesce({prefix}event,'—')")
    parts, args = [], []
    if who:
        q = ",".join("?" * len(who))
        # A photograph has to BE on a list (closed until somebody opens it).
        parts.append(f"{album_key} IN (SELECT album_key FROM album_acl "
                     f"                WHERE principal IN ({q}))")
        args += who
    # ⚠ The owner always sees their OWN photographs -- even when they did not
    # tick themselves on the list. Otherwise you lock yourself out of your own
    # album, which is exactly what happens the first time somebody sets "who
    # may see" to one other person. It leaks nothing: they are their own
    # photographs.
    u = getattr(ident, "user", "")
    if u:
        parts.append(f"{prefix}owner = ?")
        args.append(u)
    if not parts:
        # No name, no group -- then nothing at all.
        return "0", []
    return "(" + " OR ".join(parts) + ")", args


def may_see(ident, year, country, event) -> bool:
    """For a single question -- the same rule, just not in SQL."""
    if ident is None or getattr(ident, "is_admin", False):
        return True
    rows = [r["principal"] for r in db.connect().execute(
        "SELECT principal FROM album_acl WHERE album_key=?",
        (key(year, country, event),))]
    if set(rows) & set(principals_for(ident)):
        return True
    # The owner always sees their own album (see sql_clause).
    u = getattr(ident, "user", "")
    if u and db.connect().execute(
            "SELECT 1 FROM photos WHERE owner=? AND coalesce(album_year,'—')=? "
            "AND coalesce(country,'—')=? AND coalesce(event,'—')=? LIMIT 1",
            (u, year, country, event)).fetchone():
        return True
    return False


def of_album(year, country, event) -> list:
    return [r["principal"] for r in db.connect().execute(
        "SELECT principal FROM album_acl WHERE album_key=? ORDER BY principal",
        (key(year, country, event),))]


def all_acls() -> dict:
    out = {}
    for r in db.connect().execute("SELECT album_key, principal FROM album_acl"):
        out.setdefault(r["album_key"], []).append(r["principal"])
    return out


def set_audience(year, country, event, principals) -> dict:
    """Set the list. An empty list leaves the album visible **to the
    administrator only** -- that is not a deletion, it is a closing."""
    k = key(year, country, event)
    clean = []
    for p in principals or []:
        p = str(p).strip()
        if p.startswith("user:") or p.startswith("group:"):
            if len(p) > len(p.split(":", 1)[0]) + 1:
                clean.append(p)
    with db.tx() as c:
        c.execute("DELETE FROM album_acl WHERE album_key=?", (k,))
        if clean:
            c.executemany("INSERT OR IGNORE INTO album_acl (album_key, principal) "
                          "VALUES (?,?)", [(k, p) for p in sorted(set(clean))])
    log.info("Album %s: %s", k,
             ", ".join(sorted(set(clean))) or "the administrator only")
    return {"album": k, "audience": sorted(set(clean)),
            "admin_only": not clean}


def rename_album(old_key: str, new_key: str) -> None:
    """⚠ The list has to move when an album is renamed. Otherwise it points at
    a path that no longer exists -- and the album is suddenly open."""
    with db.tx() as c:
        c.execute("UPDATE OR REPLACE album_acl SET album_key=? WHERE album_key=?",
                  (new_key, old_key))
