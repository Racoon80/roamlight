#!/usr/bin/env python3
"""Acceptance test for the sync. Runs on the server as `family`.

It checks the table in app/sync.py row by row:

    removed · changed · moved · new · back

plus the two brakes, which are the most important thing in that module.
"""
import os
import shutil
import subprocess
import sys
import sqlite3
import time
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

ORIGINS = Path(os.environ.get("FAMILY_ORIGINS", "/srv/originals"))
WEB = Path(os.environ.get("FAMILY_WEB", "/srv/library"))
DB = os.environ.get("FAMILY_DB", "/opt/family/data/family.db")

TEST_YEAR = "1999"
TEST_COUNTRY = "Testland"
BASIS = f"{TEST_YEAR}/{TEST_COUNTRY}"

ok = bad = 0


def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1; print(f"  ok    {name}")
    else:
        bad += 1; print(f"  FAIL  {name}  {extra}")


def req(path, method="GET", data=None):
    import json as _j, urllib.error, urllib.request
    secret = Path("/etc/family/proxy-secret").read_text().strip()
    body = _j.dumps(data).encode() if data is not None else None
    r = urllib.request.Request("http://127.0.0.1:8080" + path, data=body, method=method)
    r.add_header("X-Family-Proxy", secret)
    r.add_header("X-authentik-username", "siteadmin")
    r.add_header("X-authentik-groups", ADMIN_GROUP)
    if body:
        r.add_header("Content-Type", "application/json")
        r.add_header("Sec-Fetch-Site", "same-origin")
    try:
        with urllib.request.urlopen(r, timeout=30) as f:
            return f.status, _j.loads(f.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def q(sql, *a):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    r = con.execute(sql, a).fetchall(); con.close()
    return [dict(x) for x in r]


def _foreign():
    return q("SELECT COUNT(*) n FROM photos WHERE origin_path NOT LIKE ?",
             TEST_YEAR + "/%")[0]["n"]


def _abort_if_real_data():
    root = ORIGINS / TEST_YEAR
    def fremd(p):
        try:
            return p.relative_to(root).parts[0] != TEST_COUNTRY
        except ValueError:
            return True
    if root.exists() and any(p for p in root.rglob("*") if p.is_file() and fremd(p)):
        raise SystemExit(f"ABORTED: {root} holds files that are not the test's")
    n = q("SELECT COUNT(*) n FROM photos WHERE origin_path LIKE ? "
          "AND origin_path NOT LIKE ?",
          TEST_YEAR + "/%", BASIS + "/%")[0]["n"]
    if n:
        raise SystemExit(f"OFGEBRACH: {n} echt Fotoen am Joer {TEST_YEAR}")


def _wipe():
    from app import convert
    ids = [r["id"] for r in q("SELECT id FROM photos WHERE origin_path LIKE ?",
                              TEST_YEAR + "/%")]
    con = sqlite3.connect(DB)
    if ids:
        m = ",".join("?" * len(ids))
        con.execute(f"DELETE FROM jobs WHERE kind='convert' AND payload IN ({m})",
                    [str(i) for i in ids])
        con.execute(f"DELETE FROM upload_files WHERE photo_id IN ({m})", ids)
        con.execute(f"DELETE FROM photos WHERE id IN ({m})", ids)
    con.execute("DELETE FROM removed WHERE origin_path LIKE ?", (TEST_YEAR + "/%",))
    con.commit(); con.close()
    for i in ids:
        shutil.rmtree(convert.derivative_dir(i), ignore_errors=True)
    for root in (ORIGINS, WEB):
        shutil.rmtree(root / TEST_YEAR / TEST_COUNTRY, ignore_errors=True)
        try:
            (root / TEST_YEAR).rmdir()
        except OSError:
            pass
    # Only the test's own leftovers in the bin
    trash = WEB / ".Poubelle"
    if trash.is_dir():
        for dag in trash.iterdir():
            shutil.rmtree(dag / TEST_YEAR, ignore_errors=True)
            try:
                dag.rmdir()
            except OSError:
                pass
        try:
            trash.rmdir()
        except OSError:
            pass


def make(path: Path, w, h, when, seed=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["/opt/family/venv/bin/python3", "-c",
                    f"import pyvips;pyvips.Image.gaussnoise({w},{h},seed={seed})"
                    f".cast('uchar').copy(interpretation='b-w').colourspace('srgb')"
                    f".jpegsave({str(path)!r},Q=90)"], check=True, capture_output=True)
    subprocess.run(["exiftool", "-overwrite_original", "-q", "-m",
                    f"-DateTimeOriginal={when}", "--", str(path)],
                   check=False, capture_output=True)


def warten(n, state="ok", secs=180):
    for _ in range(secs * 2):
        c = q("SELECT COUNT(*) n FROM photos WHERE origin_path LIKE ? AND state=?",
              TEST_YEAR + "/%", state)[0]["n"]
        if c >= n:
            return c
        time.sleep(.5)
    return c


def main():
    from app import config, convert, sync
    _abort_if_real_data()
    alien_before = _foreign()
    print("Acceptance test — the sync")
    print(f"  ({alien_before} foreign photographs in the database — those stay untouched)\n")
    _wipe()

    dossier = ORIGINS / BASIS / "Spigel"
    for i, when in enumerate(("1999:05:01 09:00:00", "1999:05:01 10:00:00",
                              "1999:05:01 11:00:00"), start=1):
        make(dossier / f"a{i}.jpg", 1400, 933, when, seed=i)

    # -- NEI ---------------------------------------------------------------
    r = sync.run("quick")
    chk("nei Fotoe kommen vun eleng", r["new"] == 3, r)
    chk("the run was not stopped", not r["halted"], r["halted"])
    chk("three converted", warten(3) == 3)

    rows = {Path(r_["origin_path"]).name: r_ for r_ in
            q("SELECT * FROM photos WHERE origin_path LIKE ?", TEST_YEAR + "/%")}
    chk("all three have a master", all(
        (WEB / r_["web_name"]).is_file() for r_ in rows.values()))

    # -- NOTHING CHANGED ----------------------------------------------------
    r = sync.run("quick")
    chk("a second run stirs nothing",
        (r["new"], r["changed"], r["moved"], r["missing"]) == (0, 0, 0, 0), r)

    # -- CHANGED ------------------------------------------------------------
    a1 = rows["a1.jpg"]
    rev_vir = a1["rev"]
    make(dossier / "a1.jpg", 1400, 933, "1999:05:01 09:00:00", seed=99)
    r = sync.run("quick")
    chk("a changed photograph is recognised", r["changed"] == 1, r)
    n = q("SELECT rev, state, origin_sha256 FROM photos WHERE id=?", a1["id"])[0]
    chk("d'Versioun geet erop", n["rev"] == rev_vir + 1, f"{rev_vir} -> {n['rev']}")
    chk("den Hash gouf nogezunn", n["origin_sha256"] != a1["origin_sha256"])
    chk("it is converted again", warten(3) == 3)
    chk("the web sizes were recomputed",
        (convert.derivative_dir(a1["id"]) / "400.webp").is_file())

    # -- MOVED --------------------------------------------------------------
    a2 = rows["a2.jpg"]
    neier = ORIGINS / BASIS / "Anerplaz"
    neier.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dossier / "a2.jpg"), str(neier / "a2.jpg"))
    r = sync.run("quick")
    chk("a move is recognised as a move",
        (r["moved"], r["missing"], r["new"]) == (1, 0, 0), r)
    n = q("SELECT * FROM photos WHERE id=?", a2["id"])[0]
    chk("⚠ it is THE SAME photograph (same id)", n["id"] == a2["id"])
    chk("den neie Wee steet an der Datebank",
        n["origin_path"] == f"{BASIS}/Anerplaz/a2.jpg", n["origin_path"])
    chk("the album followed", n["event"] == "Anerplaz", n["event"])
    chk("the master moved with it, mirrored",
        (WEB / n["web_name"]).is_file() and "Anerplaz" in n["web_name"], n["web_name"])
    chk("the old master is no longer there",
        not (WEB / a2["web_name"]).is_file(), a2["web_name"])

    # -- EWECHGEHOLL -------------------------------------------------------
    a3 = rows["a3.jpg"]
    (dossier / "a3.jpg").unlink()
    # ⚠ The brake is raised for this one case: three photographs out of 117 is
    # 2.6 % and therefore over the limit. That the brake really does fire is
    # checked separately a little further down.
    alt_pct = config.SCAN_MAX_MISSING_PCT
    config.SCAN_MAX_MISSING_PCT = 100
    r = sync.run("quick")
    chk("a removed photograph is missing", r["missing"] == 1, r)
    n = q("SELECT * FROM photos WHERE id=?", a3["id"])[0]
    chk("d'Zeil bleift stoen", n is not None and n["state"] == "missing", n["state"])
    chk("⚠ title/rating/tags stay (the row was not deleted)",
        n["taken_at"] == a3["taken_at"] and n["id"] == a3["id"])
    chk("it is off the site",
        q("SELECT COUNT(*) n FROM photos WHERE id=? AND state='ok'",
          a3["id"])[0]["n"] == 0)
    poub = list((WEB / ".Poubelle").rglob("a3.jpg")) if (WEB / ".Poubelle").is_dir() else []
    chk("the master is in the bin", len(poub) == 1, poub)
    chk("the old master is no longer on the site", not (WEB / a3["web_name"]).is_file())

    # -- BACK ---------------------------------------------------------------
    make(dossier / "a3.jpg", 1400, 933, "1999:05:01 11:00:00", seed=3)
    r = sync.run("quick")
    chk("the photograph comes back", r.get("restored") == 1, r)
    n = q("SELECT * FROM photos WHERE id=?", a3["id"])[0]
    chk("⚠ mat DERSELWECHTER ID -- alles wat drun hong bleift", n["id"] == a3["id"])
    chk("it is on the site again", warten(3) == 3)
    config.SCAN_MAX_MISSING_PCT = alt_pct

    # -- THE MASS BRAKE -----------------------------------------------------
    # ⚠ REALLY out of the tree, not merely renamed. On the first attempt they
    # were renamed to "mirror.gone" -- and the sync quite rightly recognised
    # them as MOVED, because the hashes were still in the tree. That is exactly
    # the behaviour that keeps a tidy-up from counting as a loss. To test the
    # brake they have to be gone.
    baussen = Path("/tmp/sync_check_fort")
    shutil.rmtree(baussen, ignore_errors=True); baussen.mkdir(parents=True)
    shutil.move(str(dossier), str(baussen / "Spigel"))
    shutil.move(str(neier), str(baussen / "Anerplaz"))
    r = sync.run("quick")
    chk("⚠ the mass brake stops the run", bool(r["halted"]), r)
    chk("it counts by folder, not by photograph", r.get("folders") and len(r["folders"]) == 2,
        r.get("folders"))
    chk("⚠ and NOTHING was touched",
        q("SELECT COUNT(*) n FROM photos WHERE origin_path LIKE ? AND state='ok'",
          TEST_YEAR + "/%")[0]["n"] == 3, "photographs were changed despite the brake")
    chk("de Grond steet an der Datebank",
        (q("SELECT halted_reason h FROM scans ORDER BY id DESC LIMIT 1")[0]["h"] or "")
        .startswith("3 photographs"),
        q("SELECT halted_reason h FROM scans ORDER BY id DESC LIMIT 1")[0]["h"])

    # „Jo, dat war ech"
    r = sync.run("quick", confirm_missing=True)
    chk('with "yes, that was me" it goes through', not r["halted"] and r["missing"] == 3, r)

    shutil.move(str(baussen / "Spigel"), str(dossier))
    shutil.move(str(baussen / "Anerplaz"), str(neier))
    shutil.rmtree(baussen, ignore_errors=True)
    sync.run("quick")
    chk("everything comes back", warten(3) == 3)

    # -- D'MOUNT-BREMSE ----------------------------------------------------
    marker = ORIGINS / config.MARKER_NAME
    inhalt = marker.read_bytes() if marker.is_file() else None
    try:
        if inhalt is not None:
            marker.unlink()
        r = sync.run("quick")
        chk("⚠ without the marker file the sync does not run at all", bool(r["halted"]), r)
        chk("an d'Fotoe bleiwen um Site",
            q("SELECT COUNT(*) n FROM photos WHERE origin_path LIKE ? AND state='ok'",
              TEST_YEAR + "/%")[0]["n"] == 3)
    finally:
        if inhalt is not None:
            marker.write_bytes(inhalt)

    # -- DEEP RUN -----------------------------------------------------------
    # A file written back with THE SAME size and THE SAME mtime: the quick run
    # sees nothing, the deep run does.
    a1n = q("SELECT * FROM photos WHERE origin_path LIKE ?", f"{BASIS}/Spigel/a1.jpg")[0]
    p = dossier / "a1.jpg"
    st = p.stat()
    daten = bytearray(p.read_bytes())
    daten[-1] = daten[-1] ^ 0xFF          # one bit -- or the size would change
    p.write_bytes(bytes(daten))
    os.utime(p, (st.st_atime, st.st_mtime))
    chk("size and date really are unchanged",
        p.stat().st_size == st.st_size and abs(p.stat().st_mtime - st.st_mtime) < 1)
    r = sync.run("quick")
    chk("the quick run does not see it (and should not)", r["changed"] == 0, r)
    r = sync.run("deep")
    chk("⚠ the deep run finds it", r["changed"] == 1, r)

    # -- POUBELLE OPRAUMEN --------------------------------------------------
    warten(3)
    trash = WEB / ".Poubelle"
    alen = trash / "1990-01-01" / BASIS
    alen.mkdir(parents=True, exist_ok=True)
    (alen / "alen.jpg").write_bytes(b"x" * 10)
    haut = trash / time.strftime("%Y-%m-%d") / BASIS
    haut.mkdir(parents=True, exist_ok=True)
    (haut / "haut.jpg").write_bytes(b"x" * 10)
    res = sync.empty_trash()
    chk("what has been in the bin too long goes",
        not (trash / "1990-01-01").exists(), res)
    chk("what is not old yet stays", (haut / "haut.jpg").is_file(), res)

    # -- THROUGH THE BUTTON, NOT DIRECTLY -------------------------------------
    # ⚠ Until now this test always called `sync.run()` directly -- and so it did
    # not notice that the handler in the worker had the wrong shape. The worker
    # hands it the WHOLE job row, not the payload, which gave an
    # `AttributeError: 'sqlite3.Row' object has no attribute 'rstrip'`. The path
    # through the button has to be checked, because that is the path that is
    # actually used.
    virdrun = q("SELECT COUNT(*) n FROM scans")[0]["n"]
    st, out = req("/api/sync", method="POST", data={"kind": "quick"})
    chk("the button starts a run", st == 200 and out and out.get("job"), f"{st} {out}")
    finished = None
    for _ in range(120):
        time.sleep(.5)
        r_ = q("SELECT * FROM scans ORDER BY id DESC LIMIT 1")[0]
        if q("SELECT COUNT(*) n FROM scans")[0]["n"] > virdrun and r_["finished_at"]:
            finished = r_
            break
    chk("⚠ the run really does go through the worker", finished is not None,
        q("SELECT * FROM jobs WHERE kind='sync' ORDER BY id DESC LIMIT 1"))
    job = q("SELECT * FROM jobs WHERE kind='sync' ORDER BY id DESC LIMIT 1")
    chk("and the job left no error behind",
        job and job[0]["status"] == "done" and not job[0]["last_error"],
        job[0]["last_error"] if job else "kee Job")
    st, out = req("/api/sync")
    chk("the state can be fetched",
        st == 200 and out and out.get("runs"), st)
    st, _ = req("/api/sync", method="POST", data={"kind": "kabes"})
    chk("an unknown kind is refused", st == 400, st)

    _wipe()
    danach = _foreign()
    chk("no foreign photograph was lost", danach >= alien_before,
        f"{alien_before} -> {danach}")
    print(f"\n  {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
