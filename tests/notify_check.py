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
from app import config, db, notify                               # noqa: E402

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
    # ⚠ Nëmmen ONS Leit zielen. D'Zilgrupp ëmfaasst och all Administrateur vun
    #   der Instanz -- dat ass richteg (en Admin gesäit jo alles), mee et
    #   hänkt dovunner of, wéi vill Kont'en op där Maschinn stinn. En Test
    #   deen dat matzielt, misst d'Instanz kennen.
    rows = [r for r in pending() if r["username"] in PEOPLE]
    check("een eenzege Reih fir eis zwee (een ass den Auteur)", len(rows) == 1,
          f"{len(rows)} Reihen; am Ganzen {len(pending())} mat den Admins")
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
    other = "1994/Testland/Anerer"
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO album_acl (album_key, principal) VALUES (?, ?)",
                  (other, "user:zz-notify-b"))
    notify.note("photos", other, actor="zz-notify-a", n=3)
    n2 = db.connect().execute(
        "SELECT count(*) FROM notify_pending WHERE username='zz-notify-b'").fetchone()[0]
    check("zwee Reihen, well et zwee Albume sinn", n2 == 2, str(n2))
    with db.tx() as c:
        c.execute("DELETE FROM notify_pending WHERE album_key=?", (other,))
        c.execute("DELETE FROM album_acl WHERE album_key=?", (other,))

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

    # ⚠ Ouni ee Wee no baussen dierf d'Schlaang GUER net ugefaasst ginn --
    #   soss géif de ganze Réckstand roueg verschwannen, wärend een nach e
    #   Schlëssel besuergt. Op enger Testinstanz ass genee dat de Fall.
    tally = notify.flush()
    check("ouni Transport bleift alles stoen",
          tally.get("no_transport") and pending("zz-notify-b"), str(tally))

    # An elo mat engem Wee -- ouni en echte Schlëssel: `apns_ready` gëtt fir
    # dësen Test op True gesat, an `deliver` gëtt duerch ee ersat deen zielt.
    echt_ready, echt_deliver = config.apns_ready, notify.deliver
    geschéckt = []
    config.apns_ready = lambda: True
    notify.deliver = lambda d, ti, bo, da: geschéckt.append((d["token"], ti, bo))
    try:
        notify.register("zz-notify-b", "apns", "zz-token-flush", "Telefon")
        tally = notify.flush()
    finally:
        config.apns_ready, notify.deliver = echt_ready, echt_deliver

    check("mat engem Wee geet e fort", not pending("zz-notify-b"), str(tally))
    check("an all Reih vun deem Album ass fort", not pending(), f"{len(pending())} bliwwen")
    check("an et gouf tatsächlech eppes geschéckt", len(geschéckt) == 1,
          str(geschéckt[:1]))
    check("mat der Zuel dran", geschéckt and "100" in geschéckt[0][2],
          geschéckt[0][2] if geschéckt else "-")
    notify.unregister("zz-notify-b", "apns", "zz-token-flush")

    print("\n── eng GRUPP op der Lëscht ──")
    grupp = "1994/Testland/Grupp"
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO album_acl (album_key, principal) VALUES (?, ?)",
                  (grupp, "group:zz-notify-grupp"))
        c.execute("UPDATE members SET groups_json=? WHERE username=?",
                  ('["zz-notify-grupp"]', "zz-notify-b"))
    notify.note("photos", grupp, actor="zz-notify-a", n=2)
    got = db.connect().execute(
        "SELECT n FROM notify_pending WHERE username='zz-notify-b' AND album_key=?",
        (grupp,)).fetchone()
    check("wien iwwer eng Grupp dobäi ass, kritt och Bescheed", got is not None,
          f"n={got['n'] if got else '-'}")
    with db.tx() as c:
        c.execute("DELETE FROM notify_pending WHERE album_key=?", (grupp,))
        c.execute("DELETE FROM album_acl WHERE album_key=?", (grupp,))
        c.execute("UPDATE members SET groups_json='[]' WHERE username='zz-notify-b'")

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
    # ⚠ Een aneren dierf en NET ofmellen -- en Token ass kee Geheimnis.
    notify.unregister("zz-notify-a", "apns", "zz-token-1")
    check("en anere kann en NET ofmellen", len(notify.devices_of("zz-notify-b")) == 1)
    notify.unregister("zz-notify-b", "apns", "zz-token-1")
    check("de Besëtzer awer schonn", not notify.devices_of("zz-notify-b"))

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
