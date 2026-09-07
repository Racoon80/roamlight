"""Bescheed soen -- an virun allem: NET honnert Mol.

⚠ Dat ass de Kär vun der ganzer Saach. E Familljen-Import vu fënnefhonnert
  Fotoen muss EE Saz um Sperrbildschierm sinn a keng fënnefhonnert, an keen
  dierf iwwer seng eegen Aarbecht Bescheed kréien.

Leeft géint d'Datebank vun der Instanz, mat eegene Reihen déi um Enn nees
ewechkommen -- keng Foto gëtt ugefaasst.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env                                                      # noqa: F401,E402

sys.path.insert(0, _env.app_root())
from app import db, notify                                       # noqa: E402

ALBUM = "1994/Testland/Bescheed"
PEOPLE = ("zz-notify-a", "zz-notify-b")
ok = bad = 0


def check(name, cond, note=""):
    global ok, bad
    if cond:
        ok += 1
        print(f"  ok    {name}" + (f"  — {note}" if note else ""))
    else:
        bad += 1
        print(f"  FAIL  {name}" + (f"  — {note}" if note else ""))


def pending(user=None):
    q = "SELECT * FROM notify_pending WHERE album_key=?"
    a = [ALBUM]
    if user:
        q += " AND username=?"
        a.append(user)
    return db.connect().execute(q, a).fetchall()


def clean():
    with db.tx() as c:
        c.execute("DELETE FROM notify_pending WHERE album_key=?", (ALBUM,))
        c.execute("DELETE FROM album_acl WHERE album_key=?", (ALBUM,))
        for p in PEOPLE:
            c.execute("DELETE FROM notify_devices WHERE username=?", (p,))
            c.execute("DELETE FROM members WHERE username=? AND seen_in_authentik=0 "
                      "AND is_local=0", (p,))


def main():
    clean()
    # Zwee Leit, déi deen Album kucken dierfen.
    with db.tx() as c:
        for p in PEOPLE:
            c.execute("INSERT OR IGNORE INTO members (username, display_name) VALUES (?, ?)",
                      (p, p))
            c.execute("INSERT OR IGNORE INTO album_acl (album_key, principal) VALUES (?, ?)",
                      (ALBUM, f"user:{p}"))

    print("── honnert Fotoen ──")
    for _ in range(100):
        notify.note("photos", ALBUM, actor="zz-notify-a", n=1)
    rows = pending()
    check("een eenzege Reih pro Persoun", len(rows) == 1,
          f"{len(rows)} Reihen fir {len(PEOPLE)} Leit (een ass den Auteur)")
    check("an de Reih zielt honnert", rows and rows[0]["n"] == 100,
          str(rows[0]["n"]) if rows else "-")
    check("deen deen se eropgelueden huet kritt näischt",
          not pending("zz-notify-a"))
    check("dee aneren awer schonn", bool(pending("zz-notify-b")))

    print("\n── an de Saz dee gebaut gëtt ──")
    title, body = notify.message(rows[0])
    check("een Titel", bool(title), title)
    check("eng eenzeg Zeil mat der Zuel", "100" in body, body)

    print("\n── zwee verschidden Albumen ginn zwou Noriichten ──")
    notify.note("photos", "1994/Testland/Anerer", actor="zz-notify-a", n=3)
    n2 = db.connect().execute(
        "SELECT count(*) FROM notify_pending WHERE username='zz-notify-b'").fetchone()[0]
    check("zwee Reihen", n2 >= 2, str(n2))
    with db.tx() as c:
        c.execute("DELETE FROM notify_pending WHERE album_key='1994/Testland/Anerer'")
        c.execute("DELETE FROM album_acl WHERE album_key='1994/Testland/Anerer'")

    print("\n── d'Fënster ──")
    row = pending("zz-notify-b")[0]
    check("d'Fënster läit an der Zukunft",
          row["send_after"] > db.connect().execute(
              "SELECT datetime('now')").fetchone()[0],
          f"send_after={row['send_after']}")
    tally = notify.flush()
    check("flush léisst e roueg, sou laang d'Fënster leeft",
          bool(pending("zz-notify-b")), str(tally))

    # ⚠ D'Fënster op laanscht setzen, amplaz anerhallef Minutt ze waarden.
    with db.tx() as c:
        c.execute("UPDATE notify_pending SET send_after=datetime('now','-1 minute') "
                  "WHERE album_key=?", (ALBUM,))
    tally = notify.flush()
    check("duerno geet e fort", not pending("zz-notify-b"), str(tally))
    check("an ouni Telefon gëtt näischt geschéckt", tally["sent"] == 0,
          f"sent={tally['sent']}, no_device={tally['no_device']}")

    print("\n── Zougang zu engem Album ──")
    notify.note_access(ALBUM, "zz-notify-b", actor="zz-notify-a")
    notify.note_access(ALBUM, "zz-notify-b", actor="zz-notify-a")
    rows = [r for r in pending("zz-notify-b") if r["event"] == "access"]
    check("zweemol gesat = ee Bescheed", len(rows) == 1, f"{len(rows)}")
    check("an een aneren Text", "see" in notify.message(rows[0])[1].lower(),
          notify.message(rows[0])[1])
    check("een deen sech selwer androt kritt näischt",
          notify.note_access(ALBUM, "zz-notify-a", actor="zz-notify-a") == 0)

    print("\n── en Telefon umellen ──")
    notify.register("zz-notify-b", "apns", "zz-token-1", "Telefon")
    notify.register("zz-notify-b", "apns", "zz-token-1", "Telefon")
    n = len(notify.devices_of("zz-notify-b"))
    check("zweemol umellen = een Androen", n == 1, str(n))
    check("een onbekannte Wee gëtt refuséiert",
          _raises(lambda: notify.register("zz-notify-b", "carrier-pigeon", "x")))
    notify.unregister("apns", "zz-token-1")
    check("ofmellen hëlt en ewech", not notify.devices_of("zz-notify-b"))

    clean()
    print(f"\n{'ALLES GRÉNG' if not bad else str(bad) + ' FEELER'} — {ok} ok")
    return 1 if bad else 0


def _raises(fn):
    try:
        fn()
        return False
    except Exception:                                            # noqa: BLE001
        return True


if __name__ == "__main__":
    sys.exit(main())
