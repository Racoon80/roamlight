#!/usr/bin/env python3
"""Acceptance test for the undo of an album rebuild.

Runs on the server as `family`. Builds a synthetic throw-away album (files in
both trees plus a database row), renames it, turns that back with the undo, and
checks that the files AND the database are back where they were. It touches
nothing real and clears up after itself.
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, _env.app_root())
from app import album, config, db  # noqa: E402

O, W = Path(config.ORIGIN_DIR), Path(config.WEB_DIR)
REL = "1990/Testland/zz-undo"
REL2 = "1990/Testland/zz-undo-ren"

ok = bad = 0


def chk(name, cond):
    global ok, bad
    if cond:
        ok += 1; print(f"  ok    {name}")
    else:
        bad += 1; print(f"  FAIL  {name}")


def _mk(root):
    (root / REL).mkdir(parents=True, exist_ok=True)
    (root / REL / "t.jpg").write_bytes(b"\xff\xd8\xff\xe0test")


def _cleanup():
    with db.tx() as c:
        c.execute("DELETE FROM photos WHERE event LIKE 'zz-undo%'")
        c.execute("DELETE FROM moves WHERE src LIKE '1990/Testland/zz-undo%' "
                  "OR dst LIKE '1990/Testland/zz-undo%'")
        c.execute("DELETE FROM album_acl WHERE album_key LIKE '1990/Testland/zz-undo%'")
        c.execute("DELETE FROM album_published WHERE album_key LIKE '1990/Testland/zz-undo%'")
    for root in (O, W):
        for r in (REL, REL2):
            shutil.rmtree(root / r, ignore_errors=True)
        shutil.rmtree(root / "1990/Testland", ignore_errors=True)
        shutil.rmtree(root / "1990", ignore_errors=True)


def main():
    print("Ofnahm-Test — Album-Undo\n")
    _cleanup()
    _mk(O); _mk(W)
    with db.tx() as c:
        c.execute(
            "INSERT INTO photos (origin_path,web_name,master_source_path,album_year,"
            "country,event,place,state,origin_root,taken_at,width,height,kind) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (REL + "/t.jpg", REL + "/t.jpg", REL + "/t.jpg", "1990", "Testland",
             "zz-undo", "Testville", "ok", "my_photos", "1990-01-01 12:00:00",
             100, 100, "photo"))

    d = album.edit("1990", "Testland", "zz-undo",
                   new_event="zz-undo-ren", new_place="Newville")
    chk("the undo batch came back", bool(d.get("batch")))
    chk("files moved (originals tree)", (O / REL2 / "t.jpg").is_file() and not (O / REL).exists())
    chk("files moved (web tree)", (W / REL2 / "t.jpg").is_file() and not (W / REL).exists())
    row = db.connect().execute(
        "SELECT event, place, origin_path FROM photos WHERE album_year='1990'").fetchone()
    chk("database: the name changed", row["event"] == "zz-undo-ren")
    chk("DB Uertschaft gesat", row["place"] == "Newville")
    chk("database: the path was rewritten", row["origin_path"] == REL2 + "/t.jpg")

    u = album.undo(d["batch"])
    chk("Undo mellt de richtege Wee", u["to"] == REL)
    chk("files back (originals tree)", (O / REL / "t.jpg").is_file() and not (O / REL2).exists())
    chk("files back (web tree)", (W / REL / "t.jpg").is_file() and not (W / REL2).exists())
    row2 = db.connect().execute(
        "SELECT event, place, origin_path FROM photos WHERE album_year='1990'").fetchone()
    chk("database: the name was put back", row2["event"] == "zz-undo")
    chk("database: the place was put back", row2["place"] == "Testville")
    chk("database: the path was put back", row2["origin_path"] == REL + "/t.jpg")
    and_ = db.connect().execute(
        "SELECT undone_at FROM moves WHERE batch_id=?", (d["batch"],)).fetchone()
    chk("moves marked as undone", and_ and and_["undone_at"] is not None)

    try:
        album.undo(d["batch"]); chk("a second undo is refused", False)
    except ValueError:
        chk("a second undo is refused", True)

    _cleanup()
    print(f"\n  {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
