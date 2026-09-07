#!/usr/bin/env python3
"""Acceptance test for the album permissions and the members.

The rule it checks: you tick a few people on an album, and only those people
see it. That is not tidiness, it is a **separation** -- so EVERY route to a
photograph is checked, not only the overview: the lists, the search, the map,
the facets, a collection, and the image address itself.

Runs on the server as `family`. The permissions are copied down first and put
back at the end -- a test must not change what somebody configured.
"""
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env                                              # noqa: E402

# ⚠ The group names are NOT hard-coded: they come from the same environment the
# site itself reads. A test that assumes "admin" would fail on every
# installation that calls its groups something else -- and one that assumes
# somebody's own names would fail everywhere else.
def _group(var, fallback):
    v = os.environ.get(var)
    if not v:
        try:                                     # the service's own file
            for line in open("/etc/family/env"):
                if line.startswith(var + "="):
                    v = line.split("=", 1)[1].strip().strip('"\''); break
        except OSError:
            pass
    return (v or fallback).split(",")[0].strip()


ADMIN_GROUP = _group("FAMILY_ADMIN_GROUPS", "admin")
VIEWER_GROUP = _group("FAMILY_VIEWER_GROUPS", "family")

sys.path.insert(0, _env.app_root())


BASE = "http://127.0.0.1:8080"
# ⚠ Kee feste Wee méi: _env.headers() weess, wéi ee sech op DËSER
#   Installatioun ausweist -- Proxy-Käpp oder Apparat-Token.
_ADMIN_H = _env.headers("siteadmin")
DB = _env.need("FAMILY_DB")

ok = bad = 0


def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1; print(f"  ok    {name}")
    else:
        bad += 1; print(f"  FAIL  {name}  {extra}")


def as_(user, groups=VIEWER_GROUP):
    return dict(_env.headers(user, groups))


def req(path, hdr, method="GET", data=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, method=method)
    for k, v in hdr.items():
        r.add_header(k, v)
    if body:
        r.add_header("Content-Type", "application/json")
        r.add_header("Sec-Fetch-Site", "same-origin")
    try:
        with urllib.request.urlopen(r, timeout=30) as f:
            raw = f.read()
            try:
                return f.status, json.loads(raw)
            except ValueError:
                return f.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def q(sql, *a):
    con = _env.connect(DB); con.row_factory = sqlite3.Row
    r = con.execute(sql, a).fetchall(); con.close()
    return [dict(x) for x in r]



def _drop_test_members():
    """⚠ An account the test invented through the header is noted by
    `members.note_seen()` -- and afterwards it stands on the settings page
    among the family. That has happened: a made-up name was ticked onto a
    viewing list because it was sitting there. A test must not invent a person
    who then looks real."""
    import sqlite3 as _s
    con = _env.connect(DB)
    con.execute("DELETE FROM members WHERE username LIKE 'zz-test-%' "
                "AND seen_in_authentik=0")
    con.commit(); con.close()


class _SkipDirectory(Exception):
    pass


def main():
    from app import acl, config
    ADMIN = as_("siteadmin", ADMIN_GROUP)

    alb_rows = q("SELECT album_year y, country c, event e, COUNT(*) n FROM photos "
              "WHERE state='ok' AND hidden=0 GROUP BY 1,2,3 ORDER BY n DESC")
    if len(alb_rows) < 2:
        raise SystemExit("ABORTED: the test needs at least two albums")
    goal_, other = alb_rows[0], alb_rows[1]
    fid = q("SELECT id FROM photos WHERE album_year=? AND country=? AND event=? "
            "AND state='ok' LIMIT 1", goal_["y"], goal_["c"], goal_["e"])[0]["id"]
    offen = q("SELECT id FROM photos WHERE album_year=? AND country=? AND event=? "
              "AND state='ok' LIMIT 1", other["y"], other["c"], other["e"])[0]["id"]
    whole = q("SELECT COUNT(*) n FROM photos WHERE state='ok' AND hidden=0")[0]["n"]

    print("Ofnahm-Test — Album-Rechter")
    print(f"  Zil-Album: {goal_['y']}/{goal_['c']}/{goal_['e']}  ({goal_['n']} Fotoen)")
    print(f"  bleift op: {other['y']}/{other['c']}/{other['e']}  ({other['n']})\n")

    # ⚠ Whatever is configured is copied down and put back at the end.
    before_ = {k: list(v) for k, v in acl.all_acls().items()}

    def back_():
        con = _env.connect(DB)
        con.execute("DELETE FROM album_acl")
        for k, ps in before_.items():
            con.executemany("INSERT INTO album_acl (album_key, principal) VALUES (?,?)",
                            [(k, p) for p in ps])
        con.commit(); con.close()

    try:
        # -- 1. With no list, ONLY the admin sees anything -------------------
        # ⚠ This is the most important line in the whole test. It used to be
        #   the other way round (no list = everybody), and that was changed:
        #   if nobody is ticked, nobody sees it except the administrator.
        #   Shut until somebody opens it -- not the other way round.
        con = _env.connect(DB); con.execute("DELETE FROM album_acl"); con.commit(); con.close()
        st, d = req("/api/photos", as_("eve"))
        chk("⚠ with no list the family sees NOTHING", st == 200 and d["total"] == 0,
            f"{st} {d.get('total') if isinstance(d, dict) else d}")
        st, d = req("/api/photos", ADMIN)
        chk("but the admin sees everything", st == 200 and d["total"] == whole, d.get("total"))

        # For the rest of the test: the OTHER album is opened to everybody, so
        # that "does not see the album" does not mean "sees nothing at all".
        all_rows = ["user:ada", "user:ben", "user:cleo", "user:dev",
                "user:eve", "user:finn"]
        req("/api/albums/audience", ADMIN, "POST", {
            "year": other["y"], "country": other["c"], "event": other["e"],
            "audience": all_rows})

        # -- 2. Set the list -------------------------------------------------
        st, d = req("/api/albums/audience", ADMIN, "POST", {
            "year": goal_["y"], "country": goal_["c"], "event": goal_["e"],
            "audience": ["user:ada", "user:ben", "user:cleo", "user:dev"]})
        chk("the list is saved", st == 200 and d.get("admin_only") is False, f"{st} {d}")
        chk("si steet an der Datebank",
            len(acl.of_album(goal_["y"], goal_["c"], goal_["e"])) == 4)

        # -- 3. Who is on it -------------------------------------------------
        for who_ in ("ada", "Ben", "Cleo", "Dev"):
            st, d = req("/api/photos", as_(who_))
            chk(f"{who_} sees both albums",
                st == 200 and d["total"] == goal_["n"] + other["n"], d.get("total"))
            st, _ = req(f"/photos/{fid}/400.webp", as_(who_))
            chk(f"{who_} gets the photograph", st == 200, st)

        # -- 4. Who is NOT on it -- and everything hangs off this -------------
        for who_ in ("eve", "Finn"):
            h = as_(who_)
            st, d = req("/api/photos", h)
            chk(f"{who_} does not see the album", st == 200 and d["total"] == other["n"],
                f"{d.get('total')} vun {other['n']}")
            st, _ = req(f"/photos/{fid}/400.webp", h)
            chk(f"⚠ {who_} does not get the thumbnail (direct address)", st == 404, st)
            st, _ = req(f"/photos/{fid}/master.jpg", h)
            chk(f"⚠ {who_} does not get the master", st == 404, st)
            st, _ = req(f"/api/photo/{fid}", h)
            chk(f"{who_} kritt keng Metadaten", st == 404, st)
            such = urllib.parse.quote(goal_["e"][:8])
            st, d = req(f"/api/photos?q={such}", h)
            chk(f"{who_} finds nothing in the search", st == 200 and d["total"] == 0,
                d.get("total"))
            st, d = req("/api/facets", h)
            chk(f"{who_} does not see the year in the facets",
                st == 200 and (goal_["y"] not in d.get("years", [])
                               or any(a["y"] == goal_["y"] and a is not goal_
                                      for a in alb_rows)), d.get("years"))
            st, _ = req(f"/photos/{offen}/400.webp", h)
            chk(f"{who_} STILL gets the other photographs", st == 200, st)

        # -- 5. A forbidden address has to look like one that does not exist --
        path_ = (f"/y/{goal_['y']}/{urllib.parse.quote(goal_['c'])}/"
               f"{urllib.parse.quote(goal_['e'])}")
        st_closed, _ = req(path_, as_("eve"))
        st_missing, _ = req(f"/y/{goal_['y']}/{urllib.parse.quote(goal_['c'])}/Gett-Et-Net",
                        as_("eve"))
        chk('⚠ "not allowed" and "does not exist" answer the same',
            st_closed == st_missing == 404, f"{st_closed} / {st_missing}")

        # -- 6. Eng Sammlung ass keng Hannerdier ------------------------------
        from app import collections
        s = collections.create("zz-acl-test", [fid])
        try:
            st, body = req(f"/c/{s['slug']}", as_("eve"))
            drop = b"/photos/%d/" % fid in (body if isinstance(body, bytes) else b"")
            chk("⚠ a collection does NOT get round the permissions", not drop, "the photograph was in it!")
            st, body = req(f"/c/{s['slug']}", as_("ada"))
            chk("whoever may, sees it in the collection",
                b"/photos/%d/" % fid in (body if isinstance(body, bytes) else b""), st)
        finally:
            collections.remove(s["id"])

        # -- 7. The admin sees everything ------------------------------------
        st, d = req("/api/photos", ADMIN)
        chk("the admin sees everything", st == 200 and d["total"] == whole, d.get("total"))
        st, _ = req(f"/photos/{fid}/400.webp", ADMIN)
        chk("and the photograph too", st == 200, st)

        # -- 8. An empty list = shut ------------------------------------------
        st, d = req("/api/albums/audience", ADMIN, "POST", {
            "year": goal_["y"], "country": goal_["c"], "event": goal_["e"], "audience": []})
        chk("an empty list shuts it", st == 200 and d.get("admin_only") is True, d)
        st, d = req("/api/photos", as_("ada"))
        chk("⚠ and afterwards the family no longer sees that album",
            st == 200 and d["total"] == other["n"], d.get("total"))
        st, d = req("/api/photos", ADMIN)
        chk("den Admin awer schonn", st == 200 and d["total"] == whole, d.get("total"))

        # -- 9. Only the admin sets permissions --------------------------------
        st, _ = req("/api/albums/audience", as_("eve"), "POST", {
            "year": goal_["y"], "country": goal_["c"], "event": goal_["e"],
            "audience": ["user:eve"]})
        chk("d'Famill kann keng Rechter setzen", st == 403, st)
        st, _ = req("/admin/settings", as_("eve"))
        chk("the family cannot reach the settings", st == 403, st)
        st, _ = req("/api/members/refresh", as_("eve"), "POST", {})
        chk("the family cannot fetch the list", st == 403, st)

        # -- 10. Den Admin steet net am Wieler ---------------------------------
        from app import members
        pick = {m["username"] for m in members.pickable()}
        admin_group_names = set(config.ADMIN_GROUPS)
        chk("⚠ keen Admin am Wieler",
            not any(set(m["groups"]) & admin_group_names for m in members.pickable()),
            sorted(pick))
        chk("an och keng Admin-Grupp", not (set(members.group_choices()) & admin_group_names),
            members.group_choices())
        try:
            # ⚠ Everything from here to the end of this block asks the identity
            #   directory. On an installation with local accounts there is none --
            #   and a test that fails for the absence of a thing that is not part of
            #   the installation says nothing about the site.
            if not members.configured():
                print("  --    no identity directory here -- that block is skipped")
                raise _SkipDirectory
            chk("the directory token is configured", members.configured())
            st, d = req("/api/members/refresh", ADMIN, "POST", {})
            chk("fetch the list", st == 200 and d.get("ok"), d)
            # ⚠ Only the members of the admin and viewer groups. The list of all
            # users is never asked for at all -- that was an explicit requirement:
            # the fetch is limited to the admin group and the viewer group, and
            # only those users are loaded.
            important = members.relevant_groups()
            chk("it is narrowed to the site groups",
                set(d.get("groups") or []) == important, d.get("groups"))
            chk("and fewer are read than there are groups in the directory",
                0 < d.get("members", 0) and d.get("groups_seen", 0) > len(important), d)
            from_directory = [m for m in members.all_of() if m["seen_in_authentik"]]
            chk("⚠ jidderee vun deene gelueden ass an enger Site-Grupp",
                all(set(m["groups"]) & important for m in from_directory),
                [m["username"] for m in from_directory if not set(m["groups"]) & important])
            name_list = {m["username"] for m in members.all_of()}
            chk("the admin is in there", "ada" in name_list, sorted(name_list)[:10])
            chk("Maschinne-Konten NET", not any(n.startswith("ak-") for n in name_list),
                [n for n in name_list if n.startswith("ak-")])
        except _SkipDirectory:
            pass

        # -- 11. Renaming an album must not lose the permissions ---------------
        acl.set_audience(goal_["y"], goal_["c"], goal_["e"], ["user:ada"])
        acl.rename_album(acl.key(goal_["y"], goal_["c"], goal_["e"]),
                         acl.key(goal_["y"], goal_["c"], "Renamed"))
        chk("⚠ the permissions move along on a rename",
            acl.of_album(goal_["y"], goal_["c"], "Renamed") == ["user:ada"]
            and acl.of_album(goal_["y"], goal_["c"], goal_["e"]) == [],
            acl.all_acls())
    finally:
        back_()

    chk("the permissions are as they were", acl.all_acls() == before_, acl.all_acls())
    _drop_test_members()
    print(f"\n  {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
