#!/usr/bin/env python3
"""Acceptance test for tags, people and collections.

Runs on the server as `family`, against the live database -- so the same rule
as everywhere: remove only what the test created itself, and count at the end
whether any photograph that is not ours has been lost.

Tags, people and collections the test creates all begin with `zz-test-`.
"""
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

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

sys.path.insert(0, "/opt/family/app")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env                                              # noqa: E402


BASE = "http://127.0.0.1:8080"
SECRET = Path("/etc/family/proxy-secret").read_text().strip()
ADMIN = {"X-Family-Proxy": SECRET, "X-authentik-username": "siteadmin",
         "X-authentik-groups": ADMIN_GROUP}
FAMILY = {"X-Family-Proxy": SECRET, "X-authentik-username": "zz-test-viewer",
          "X-authentik-groups": VIEWER_GROUP}
DB = _env.need("FAMILY_DB")
VIR = "zz-test-"

ok = bad = 0


def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1; print(f"  ok    {name}")
    else:
        bad += 1; print(f"  FAIL  {name}  {extra}")


def req(path, hdr=None, method="GET", data=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, method=method)
    for k, v in (hdr or ADMIN).items():
        r.add_header(k, v)
    if body:
        r.add_header("Content-Type", "application/json")
        r.add_header("Sec-Fetch-Site", "same-origin")
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            raw = f.read()
            try:
                return f.status, json.loads(raw), raw
            except ValueError:
                return f.status, None, raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw), raw
        except ValueError:
            return e.code, None, raw


def q(sql, *a):
    con = _env.connect(DB); con.row_factory = sqlite3.Row
    r = con.execute(sql, a).fetchall(); con.close()
    return [dict(x) for x in r]


def _foreign():
    return q("SELECT COUNT(*) n FROM photos")[0]["n"]


def _wipe():
    con = _env.connect(DB)
    for tab, link, column in (("tags", "photo_tags", "tag_id"),
                             ("people", "photo_people", "person_id")):
        con.execute(f"DELETE FROM {link} WHERE {column} IN "
                    f"(SELECT id FROM {tab} WHERE name LIKE ?)", (VIR + "%",))
        con.execute(f"DELETE FROM {tab} WHERE name LIKE ?", (VIR + "%",))
    con.execute("DELETE FROM album_photos WHERE album_id IN "
                "(SELECT id FROM albums WHERE title LIKE ?)", (VIR + "%",))
    con.execute("DELETE FROM albums WHERE title LIKE ?", (VIR + "%",))
    con.commit(); con.close()



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


def main():
    from app import collections, tagging
    alien_before = _foreign()
    print("Ofnahm-Test — Tags, Persounen a Sammlungen (Etapp 9)")
    print(f"  ({alien_before} photographs in the database — those stay untouched)\n")
    _wipe()

    photos = [r["id"] for r in q(
        "SELECT id FROM photos WHERE state='ok' AND hidden=0 ORDER BY id LIMIT 5")]
    if len(photos) < 4:
        raise SystemExit("ABORTED: too few photographs on the site for this test")
    three, one_ = photos[:3], photos[3]

    # -- Eng Persoun op one_ Auswiel -----------------------------------------
    st, d, _ = req("/api/mark", method="POST", data={
        "kind": "person", "name": VIR + "Cleo", "ids": three, "on": True})
    chk("one person on three photographs", st == 200 and d["photos"] == 3, f"{st} {d}")
    chk("the name was created", d and d["names"] == [VIR + "Cleo"], d)
    drop = tagging.of_photos("person", three)
    chk("it sits on all three", all(VIR + "Cleo" in drop[i] for i in three), drop)

    # ⚠ The same one twice must not be an error -- somebody drags across the
    #   same row twice, and that is the normal case.
    st, d, _ = req("/api/mark", method="POST", data={
        "kind": "person", "name": VIR + "Cleo", "ids": three, "on": True})
    chk("zweemol setzen ass kee Feeler", st == 200, f"{st} {d}")
    n = q("SELECT COUNT(*) n FROM photo_people pp JOIN people p ON p.id=pp.person_id "
          "WHERE p.name=?", VIR + "Cleo")[0]["n"]
    chk("and there is no duplicate row", n == 3, n)

    # -- Take it off again ---------------------------------------------------
    st, d, _ = req("/api/mark", method="POST", data={
        "kind": "person", "name": VIR + "Cleo", "ids": [three[0]], "on": False})
    chk("take it off again", st == 200, f"{st} {d}")
    n = q("SELECT COUNT(*) n FROM photo_people pp JOIN people p ON p.id=pp.person_id "
          "WHERE p.name=?", VIR + "Cleo")[0]["n"]
    chk("two more added", n == 2, n)

    # -- More than one name at once -----------------------------------------
    # The requirement: put more than one tag on an album, and more than one
    # name at a time. Separated by a comma -- and by a semicolon as well.
    st, d, _ = req("/api/mark", method="POST", data={
        "kind": "person", "names": f"{VIR}Anna, {VIR}Ben ; {VIR}Anna", "ids": [one_]})
    chk("three names at once", st == 200 and d["names"] == [VIR + "Anna", VIR + "Ben"],
        f"{st} {d}")
    chk("and one given twice is taken only once",
        len(d["names"]) == 2, d)
    drop2 = tagging.of_photos("person", [one_])
    chk("both sit on the photograph",
        {VIR + "Anna", VIR + "Ben"} <= set(drop2[one_]), drop2)
    st, d, _ = req("/api/mark", method="POST", data={
        "kind": "tag", "names": [VIR + "Bierg", VIR + "Mier"], "ids": [one_]})
    chk("as a list too", st == 200 and len(d["names"]) == 2, f"{st} {d}")

    # -- Tags run through the same machinery --------------------------------
    st, d, _ = req("/api/mark", method="POST", data={
        "kind": "tag", "name": VIR + "Sonn", "ids": [one_], "on": True})
    chk("en Tag setzen", st == 200 and d["photos"] == 1, f"{st} {d}")
    chk("en Tag ass keng Persoun",
        not any(x["name"] == VIR + "Sonn" for x in tagging.all_of("person")))

    # -- "Recent" -- what sits on the keys 1-9 -------------------------------
    name_list = [p["name"] for p in tagging.recent_people()]
    # ⚠ The last one set was `Ben` (from the block with several names), so that
    #   one is at the top. The order is "recent", not "alphabetical".
    chk("the most recently used person is at the top",
        name_list and name_list[0] == VIR + "Ben", name_list)
    chk("and the earlier ones are still in it", VIR + "Cleo" in name_list, name_list)
    chk("the list is at most nine long", len(name_list) <= 9, len(name_list))

    # -- Filteren ------------------------------------------------------------
    from app import gallery
    res = gallery.list_photos({"person": VIR + "Cleo"})
    chk("no Persoun filteren", res["total"] == 2, res["total"])
    res = gallery.list_photos({"tag": VIR + "Sonn"})
    chk("no Tag filteren", res["total"] == 1, res["total"])
    st, _, body = req(f"/person/{VIR}Cleo")
    chk("the people page opens", st == 200 and (VIR + "Cleo").encode() in body, st)
    st, _, body = req(f"/tag/{VIR}Sonn")
    chk("the tag page opens", st == 200, st)

    # -- "Nothing on it yet" -- the work list -------------------------------
    without_ = gallery.list_photos({"untagged": True})["total"]
    everything = gallery.list_photos({})["total"]
    chk("the work list is smaller than everything", 0 <= without_ < everything, f"{without_}/{everything}")
    chk("and none of the already tagged ones is in it",
        all(p["id"] not in three[1:] for p in
            gallery.list_photos({"untagged": True}, 1, 500)["photos"]))

    # -- Renaming and merging ------------------------------------------------
    ident = tagging.ensure("person", VIR + "maxi")
    st, d, _ = req("/api/names", method="POST", data={
        "kind": "person", "action": "rename", "id": ident, "name": VIR + "Cleo"})
    chk("zwee Nimm ginn zesummegeluecht", st == 200 and d.get("merged_into"), f"{st} {d}")
    chk("an et bleift EEN Numm",
        len([p for p in tagging.all_of("person") if p["name"].lower() == (VIR + "cleo").lower()]) == 1)

    # -- Sammlungen ----------------------------------------------------------
    st, d, _ = req("/api/collections", method="POST", data={
        "action": "new", "title": VIR + "Summer", "ids": three})
    chk("create a collection", st == 200 and d["n"] == 3, f"{st} {d}")
    setid, slug = d["id"], d["slug"]
    chk("si kritt automatesch en Titelbild", d["cover_photo_id"] in three, d)

    st, d, _ = req("/api/collections", method="POST", data={
        "action": "add", "id": setid, "ids": [one_]})
    chk("add a photograph", st == 200 and d["n"] == 4, f"{st} {d}")
    st, d, _ = req("/api/collections", method="POST", data={
        "action": "add", "id": setid, "ids": [one_]})
    chk("the same photograph twice is not an error", st == 200 and d["n"] == 4, f"{st} {d}")

    # ⚠ A photograph can be in ten collections and still lie on the disk once.
    st, d2, _ = req("/api/collections", method="POST", data={
        "action": "new", "title": VIR + "Zweet", "ids": [one_]})
    chk("the same photograph in two collections", st == 200 and d2["n"] == 1, f"{st} {d2}")
    chk("it still lies on the disk only once",
        q("SELECT COUNT(*) n FROM photos WHERE id=?", one_)[0]["n"] == 1)

    st, _, body = req(f"/c/{slug}")
    chk("d'Sammlung geet op", st == 200 and (VIR + "Summer").encode() in body, st)
    chk("the order is the collection's, not by date",
        [p["id"] for p in collections.photos_of(setid)] == three + [one_],
        [p["id"] for p in collections.photos_of(setid)])

    # Remove the cover -> a new one appears, not a hole
    st, d, _ = req("/api/collections", method="POST", data={
        "action": "drop", "id": setid, "ids": [three[0]]})
    chk("take a photograph out again", st == 200 and d["n"] == 3, f"{st} {d}")
    chk("the cover followed, it is not empty",
        d["cover_photo_id"] != three[0] and d["cover_photo_id"] is not None, d)

    # Deleting: the photographs stay
    before_ = _foreign()
    st, d, _ = req("/api/collections", method="POST", data={
        "action": "remove", "id": d2["id"]})
    chk("delete a collection", st == 200, f"{st} {d}")
    chk("⚠ an d'Fotoe bleiwen", _foreign() == before_, f"{before_} -> {_foreign()}")

    # -- What is not allowed -------------------------------------------------
    st, _, _ = req("/api/mark", method="POST", data={
        "kind": "person", "name": VIR + "X", "ids": []})
    chk("an empty selection is refused", st == 400, st)
    st, _, _ = req("/api/mark", method="POST", data={
        "kind": "kabes", "name": "x", "ids": three})
    chk("an unknown kind is refused", st == 400, st)
    st, _, _ = req("/api/collections", method="POST", data={
        "action": "new", "title": "  "})
    chk("a collection without a title is refused", st == 400, st)
    st, _, _ = req("/api/collections", method="POST", data={
        "action": "add", "id": 999999, "ids": three})
    chk("a collection that does not exist", st == 400, st)

    # -- Keen ausser dem Admin ----------------------------------------------
    st, _, _ = req("/admin/tag", hdr=FAMILY)
    chk("a plain viewer cannot reach the tagging page", st == 403, st)
    st, _, _ = req("/api/mark", hdr=FAMILY, method="POST", data={
        "kind": "person", "name": "Whoever", "ids": three})
    chk("a plain viewer cannot tag anything", st == 403, st)
    st, d, _ = req("/api/collections", hdr=FAMILY, method="POST", data={
        "action": "new", "title": "zz-test-family-set"})
    chk("an account holder CAN create a collection of their own", st == 200, st)
    if st == 200:
        req("/api/collections", hdr=ADMIN, method="POST",
            data={"action": "remove", "id": d["id"]})
    # Looking is allowed -- a collection is there for the family
    st, _, _ = req(f"/c/{slug}", hdr=FAMILY)
    chk("kucken dierf d'Famill", st == 200, st)

    # -- A hidden photograph does not come out through a collection ---------
    from app import register
    register.set_hidden([one_], True)
    visible = [p["id"] for p in collections.photos_of(setid)]
    chk("⚠ a hidden photograph is hidden inside a collection too",
        one_ not in visible, visible)
    register.set_hidden([one_], False)

    is_missing = [n for n in ("Family", "Spa")
             if not any(n in g for g in tagging.DEFAULT_TAGS.values())]
    chk('the default tags are in the list', not is_missing, is_missing)
    chosen = tagging.person_choices()
    chk("the people suggestions come from the members", chosen["suggested"], chosen)
    chk("⚠ and the admin is not among them",
        not any(n.lower().endswith("admin") for n in chosen["suggested"]),
        chosen["suggested"])

    _wipe()
    after_ = _foreign()
    chk("no foreign photograph was lost", after_ >= alien_before, f"{alien_before} -> {after_}")
    _drop_test_members()
    print(f"\n  {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
