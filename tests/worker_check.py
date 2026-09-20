"""Zwee Prozesser op enger Waardeschlaang — an keen hëlt dem aneren seng Aarbecht.

D'Konversioun vun eropgeluedene Fotoen ass an eng eege Unit geplënnert
(`family-convert.service`), déi d'Originaler net gemount huet. Béid Prozesser
huelen hir Aarbecht aus derselwechter `jobs`-Tabell. Dräi Saache mussen dofir
halen, a jidderee vun hinnen ass eng Plaz, wou eng Foto soss verluer geet:

  1. De Site rifft **keng** `convert-upload`-Jobs of. Géif en et, da géif en se
     ouni Handler zréckleeën -- an dat schéckt se **eng Stonn** an d'Waarden.
  2. D'Konversiouns-Unit rifft **nëmmen** déi of, a soss näischt.
  3. E Job gëtt genee **eemol** ausginn, och wa béid gläichzäiteg froen.

⚠ Hermetesch: eegen Datebank am /tmp, gesat IER `app.config` importéiert gëtt.
  Réiert keng echt Installatioun un a leeft dofir och um Live.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="roamlight-worker-"))
os.environ["FAMILY_DATA"] = str(TMP / "data")
os.environ["FAMILY_DB"] = str(TMP / "data" / "test.db")
os.environ["FAMILY_ORIGINS"] = str(TMP / "originals")
os.environ["FAMILY_WEB"] = str(TMP / "library")
os.environ["FAMILY_DERIVATIVES"] = str(TMP / "data" / "derivatives")
os.environ["FAMILY_INCOMING"] = str(TMP / "data" / "incoming")
os.environ["FAMILY_WORK"] = str(TMP / "data" / "work")
os.environ["FAMILY_REQUIRE_MOUNT"] = "0"
os.environ["FAMILY_AUTH"] = "local"


def _app_root() -> str:
    here = Path(__file__).resolve().parent
    for c in (os.environ.get("FAMILY_BASE"), here.parent,
              "/opt/family/app", "/opt/roamlight/app-src"):
        if c and (Path(c) / "app" / "__init__.py").is_file():
            return str(c)
    sys.exit("cannot find the program -- set FAMILY_BASE")


sys.path.insert(0, _app_root())
import pathlib                                                 # noqa: E402
from app import db, worker as w                                # noqa: E402

bad = 0


def check(name, got, want):
    global bad
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" -- erwaart {want!r}"))
    bad += 0 if ok else 1


def take(wk):
    """Ee Job ofruffen, wéi `_one()` et mécht -- an de Numm zréckginn."""
    sql, args = wk._where()
    with db.tx() as c:
        row = c.execute(
            "UPDATE jobs SET status='running', attempts=attempts+1, "
            "  updated_at=datetime('now') "
            "WHERE id = (SELECT id FROM jobs WHERE status='pending' "
            "            AND (run_after IS NULL OR run_after <= datetime('now')) "
            f"           {sql} "
            "            ORDER BY id LIMIT 1) "
            "RETURNING *", args).fetchone()
    return None if row is None else (row["kind"], row["payload"])


def main():
    db.init()
    site = w.Worker()                          # de Site: alles ausser dem Friemen
    unit = w.Worker(kinds=list(w.FOREIGN_KINDS), notices=False)   # d'Konversiouns-Unit

    print("\n-- de Site léisst d'Uploads leien --")
    db.connect().execute("DELETE FROM jobs")
    w.enqueue("convert-upload", "1")
    check("de Site fënnt näischt", take(site), None)
    check("d'Unit fënnt en", take(unit), ("convert-upload", "1"))

    print("\n-- an d'Unit réiert de Rescht net un --")
    db.connect().execute("DELETE FROM jobs")
    for k in ("convert", "scan", "sync", "geocode", "tileseed"):
        w.enqueue(k, "x")
    check("d'Unit fënnt näischt dovunner", take(unit), None)
    got = []
    while True:
        r = take(site)
        if r is None:
            break
        got.append(r[0])
    check("de Site huet se all", sorted(got),
          ["convert", "geocode", "scan", "sync", "tileseed"])

    print("\n-- ee Job, zwee Frooen, eemol ausginn --")
    db.connect().execute("DELETE FROM jobs")
    w.enqueue("convert-upload", "7")
    first, second = take(unit), take(unit)
    check("dat éischt Kritt en", first, ("convert-upload", "7"))
    check("dat zweet kritt näischt", second, None)

    print("\n-- de Site setzt d'Uploads NET zréck --")
    # De Fall: d'Unit ass matten an enger Konversioun, an de Site gëtt nei
    # gestart. Fréier huet `requeue_orphans()` ALLES zréckgesat -- an duerno
    # hunn zwee Prozesser un derselwechter Foto geschafft.
    db.connect().execute("DELETE FROM jobs")
    w.enqueue("convert", "10")
    w.enqueue("convert-upload", "11")
    take(site)                                  # béid stinn elo op 'running'
    take(unit)
    n = w.requeue_orphans(exclude=w.FOREIGN_KINDS)
    check("de Site setzt genee säin eegene zréck", n, 1)
    row = db.connect().execute(
        "SELECT status FROM jobs WHERE kind='convert-upload'").fetchone()
    check("dem aneren seng Aarbecht leeft weider", row["status"], "running")

    print("\n-- an d'Unit setzt nëmmen hir eege zréck --")
    n = w.requeue_orphans(kinds=list(w.FOREIGN_KINDS))
    check("d'Unit setzt hiren zréck", n, 1)
    row = db.connect().execute(
        "SELECT status FROM jobs WHERE kind='convert'").fetchone()
    check("an de Site säin ass onugetaascht", row["status"], "pending")

    print("\n-- d'CSP steet zweemol do, an déi zwou mussen d'selwecht sinn --")
    # ⚠ E Browser, deen ZWOU Content-Security-Policy-Kappzeile kritt, setzt
    #   d'SCHNËTTMENG duerch, net d'Verayntegung. D'App setzt eng an d'nginx
    #   setzt eng. Eng méi loosseg an der nginx bréngt also GUER näischt -- se
    #   gesäit just aus, wéi wann do eppes erlaabt wier. Genee dat war de Fall:
    #   d'nginx huet `blob:` an `https://tile.openstreetmap.org` erlaabt, an et
    #   huet ni gegraff, well d'App se net erlaabt. Fonnt den 08.09.2026.
    # ⚠ Déi WIERKSAM Konfiguratioun als éischt, net déi am Bam. Um Live louch
    #   ënner /opt/family/app/deploy/ nach eng al Kopie, déi keen liest -- an
    #   den Test huet déi gelies an eng Ofwäichung gemellt, déi et net gouf.
    #   Déi installéiert Datei ze pruefen ass souwisou déi schaarf Prouf: si
    #   mierkt och, wann de Bam richteg ass an d'Këscht net nei deployéiert gouf.
    from app.security import _HEADERS
    ng = None
    for c in (pathlib.Path("/etc/nginx/sites-available/family"),
              pathlib.Path(_app_root()) / "deploy" / "nginx.conf"):
        if c.is_file():
            ng = c
            break
    if ng is not None:
        line = [l for l in ng.read_text().splitlines()
                if l.strip().startswith("add_header Content-Security-Policy")]
        got = line[0].split('"')[1] if line else "(keng Zeil fonnt)"
        check("d'nginx hir CSP ass Wuert fir Wuert d'selwecht wéi d'App hir",
              got, _HEADERS["Content-Security-Policy"])
    else:
        print("  --   deploy/nginx.conf net do (installéiert Kopie) -- iwwersprongen")

    print("\n-- all Knäppchen op der Astellungssäit huet e Handler --")
    # ⚠ Dat hei ass fir e Feeler, dee scho gelaf ass: bei engem Ëmbau vum
    #   settings.js ass eng Hëllefsfunktioun (`val`) matgaangen, während dräi
    #   Handler se nach gerufft hunn -- an zwee Handler waren ganz fort. E Klick
    #   huet dann e ReferenceError geheit, IER déi éischt Zeil Aarbecht koum, an
    #   d'Säit huet GUER NÄISCHT gemaach. Kee Feeler, keng Meldung, näischt.
    #
    #   E Knäppchen an enger Schabloun ouni Handler am Skript ass genee dee
    #   Feeler, an et ass een, deen ee mat engem `grep` mierkt.
    import re
    root = pathlib.Path(_app_root())
    tpl = root / "templates" / "settings.html"
    js = root / "static" / "settings.js"
    if tpl.is_file() and js.is_file():
        code = js.read_text()
        ids = sorted(set(re.findall(r'<button[^>]*\bid="([a-z0-9-]+)"', tpl.read_text())))
        ouni = [i for i in ids if '"%s"' % i not in code]
        check("Knäppercher op der Säit", len(ids) > 4, True)
        check("... an all eent gëtt am Skript gefaasst", ouni, [])
        # An déi aner Richtung: e Feld, dat de Code liest, muss et ginn.
        felder = sorted(set(re.findall(r'\bval\("([a-z0-9-]+)"\)', code)))
        feelt = [f for f in felder if 'id="%s"' % f not in tpl.read_text()]
        check("all Feld, dat de Skript liest, steet op der Säit", feelt, [])
    else:
        print("  --   settings.html/js net do -- iwwersprongen")

    print(f"\n{'ALLES GRÉNG' if not bad else str(bad) + ' FEELER'}")
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
