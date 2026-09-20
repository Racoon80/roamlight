"""D'Memberen aus dem Verzeechnes nei liesen -- dat, wat den Timer rifft.

    python3 -m app.members_cli

⚠ **Firwat dat en Timer ass a kee Knäppchen.** Bis den 08.09.2026 gouf de
  Memberen-Ofgläich nëmme gemaach, wann een op der Astellungssäit drop gedréckt
  huet. Dat war richteg, soulaang de Site hannert Forward-Auth souz: d'nginx huet
  de Ubidder bei ALL Ufro gefrot, also huet en Entzuch souzesoen direkt gegraff.

  Zënter datt de Site selwer entscheet, entscheet hien esou laang, wéi seng
  Sessioun leeft -- **drësseg Deeg** -- an e gepaarten Telefon fir ëmmer. Wien
  am Authentik aus der Grupp geholl gëtt, wier also e Mount laang weider
  eragekomm. E Recht ewechzehuelen, wat eréischt an engem Mount gëllt, ass kee
  Recht ewechzehuelen.

⚠ E Feeler hei ass **keen** Ofbroch. De Ubidder ass mol net erreechbar; dann
  bleift alles, wéi et war, an de Log seet et. Wat net passéiere dierf, ass datt
  eng Panne beim Ubidder d'Famill aussperrt -- dofir hëlt `refresh()` nëmmen
  eppes ewech, wann si eng Äntwert kritt huet.
"""
import logging
import sys


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stdout)
    from . import db, members
    db.init()
    out = members.refresh()
    if not out.get("ok"):
        print("  net gelaf:", out.get("error"))
        # ⚠ Net-Null, sou datt `systemctl status` et seet -- mä ouni datt eppes
        #   um Site geännert gouf.
        return 1
    print(f"  {out['members']} Memberen, {out['groups_seen']} Gruppen gelies")
    if out.get("dropped"):
        print("  erausgefall:", ", ".join(out["dropped"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
