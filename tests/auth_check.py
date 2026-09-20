"""Wien dierf eran, a wien dierf dat änneren.

Three things are checked, and they are the three that can lose a family their
photographs or lock them out of them:

  1. A forged identity header is worth nothing without the shared secret --
     whatever else is switched on.
  2. The password road survives single sign-on being switched on. That is the
     whole reason the peer/secret check moved off the gate and onto the header
     road: a wrong line in nginx must not shut a site whose passwords are fine.
  3. Nothing on the settings page can take the password road away, and nothing
     takes effect until it has been confirmed THROUGH the proxy.

⚠ Hermetic on purpose: its own database in a temporary folder, its own secret
  file, set BEFORE `app.config` is imported. An earlier draft of this test used
  the service's environment like the rest of the suite -- which would have
  written `auth_modes` into the LIVE database and changed how the live site
  signs people in. A test must not be able to do that.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="roamlight-auth-"))
SECRET = TMP / "proxy-secret"
SECRET.write_text("s" * 48)
# ⚠ 0640, well de Site en Geheimnis refuséiert, dat jiddereen op der Maschinn
#   liese kann -- an eng Datei, déi esou ugeluecht gëtt, kritt d'umask.
SECRET.chmod(0o640)

# ⚠ Before the import. `config` reads all of this at import time.
os.environ["FAMILY_DATA"] = str(TMP / "data")
os.environ["FAMILY_DB"] = str(TMP / "data" / "test.db")
os.environ["FAMILY_ORIGINS"] = str(TMP / "originals")
os.environ["FAMILY_WEB"] = str(TMP / "library")
os.environ["FAMILY_DERIVATIVES"] = str(TMP / "data" / "derivatives")
os.environ["FAMILY_INCOMING"] = str(TMP / "data" / "incoming")
os.environ["FAMILY_WORK"] = str(TMP / "data" / "work")
os.environ["FAMILY_REQUIRE_MOUNT"] = "0"
os.environ["FAMILY_PROXY_SECRET_FILE"] = str(SECRET)
os.environ["FAMILY_AUTH"] = "local"
os.environ.pop("FAMILY_AUTH_LOCK", None)

# ⚠ `_env` is deliberately NOT imported. It refuses to run anywhere that is not
#   a test instance, and rightly so -- the other tests write into the real
#   database and the real photo trees. This one writes into neither: every path
#   above points into a temporary folder that is thrown away at the end. So it
#   runs on the live machine too, which is where a sign-in rule most needs
#   checking.
#
# ⚠ And the program is not always one folder up. On the live machine the tests
#   sit at /opt/family/tests while the program sits at /opt/family/app/app --
#   so `parent.parent` lands on a folder that merely CONTAINS something called
#   `app`, Python treats that as a namespace package, and the import fails with
#   "unknown location" instead of anything useful. So: look for the folder that
#   really holds the package.
def _app_root() -> str:
    here = Path(__file__).resolve().parent
    for c in (os.environ.get("FAMILY_BASE"), here.parent,
              "/opt/family/app", "/opt/roamlight/app-src"):
        if c and (Path(c) / "app" / "__init__.py").is_file():
            return str(c)
    sys.exit("cannot find the program -- set FAMILY_BASE")


sys.path.insert(0, _app_root())
from app import auth, cli, config, db, security                # noqa: E402

bad = 0


def check(name, got, want):
    global bad
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" -- erwaart {want!r}"))
    bad += 0 if ok else 1
    return ok


def raises(name, fn, *a, **kw):
    global bad
    try:
        fn(*a, **kw)
    except (ValueError, KeyError) as exc:
        print(f"  ok   {name}: refused -- {exc}")
        return True
    print(f"  FAIL {name}: it was ALLOWED, and it must not be")
    bad += 1
    return False


class Headers(dict):
    """Header names do not care about case, and neither does Starlette."""

    def __init__(self, d=None):
        super().__init__({k.lower(): v for k, v in (d or {}).items()})

    def get(self, key, default=None):
        return super().get(str(key).lower(), default)


class FakeRequest:
    class _Client:
        def __init__(self, host):
            self.host = host

    def __init__(self, peer="127.0.0.1", headers=None, cookies=None, query=None):
        self.client = self._Client(peer) if peer else None
        self.headers = Headers(headers)
        self.cookies = cookies or {}
        self.query_params = query or {}


def main():
    db.init()
    config.forget_auth_modes()

    print("\n-- de Standard --")
    check("ouni alles ass et 'local'", sorted(config.auth_modes()), ["local"])
    check("an de Proxy-Wee ass zou", config.auth_oidc(), False)

    print("\n-- en Identitéits-Header ass elo GUER näischt méi wäert --")
    # ⚠ Bis den 08.09.2026 huet de Site engem `X-authentik-username` gegleeft,
    #   wann e gedeelte Geheimnis derbäi war. Dee ganze Wee ass ewech: d'App
    #   schwätzt elo selwer mam Ubidder (app/oidc.py). Dëse Block steet hei, fir
    #   datt keen en aus Versinn nees erabaut -- en Header dierf keng Identitéit
    #   méi maachen, egal wat derbäi steet.
    forged = {"X-authentik-username": "guy", "X-authentik-groups": "admin"}
    withsecret = {**forged, "X-Family-Proxy": "s" * 48}
    for name, hdrs, peer in (
            ("just den Header", forged, "127.0.0.1"),
            ("mat engem gedeelte Geheimnis", withsecret, "127.0.0.1"),
            ("vum Loopback", withsecret, "::1"),
            ("vun ausserhalb", withsecret, "203.0.113.9")):
        check("gefälscht, " + name, security._identify(FakeRequest(peer, hdrs)).user, "")
        check("   ... a keng Rechter", security._identify(FakeRequest(peer, hdrs)).is_viewer, False)
    check("an `proxy_ok` gëtt et net méi", hasattr(security, "proxy_ok"), False)

    print("\n-- de Passwuert-Wee --")
    auth.create_user("mila", "eng-laang-Sach", groups=[sorted(config.ADMIN_GROUPS)[0]])
    cookie = auth.new_session("mila")
    who = security._identify(FakeRequest("203.0.113.9", {},
                                         cookies={config.SESSION_COOKIE: cookie}))
    check("de Cookie gëllt vun iwwerall", who.user, "mila")
    check("... a mat sengen Rechter", who.is_admin, True)
    check("e falsche Cookie gëllt näischt",
          security._identify(FakeRequest("127.0.0.1", {},
                                         cookies={config.SESSION_COOKIE: "aa.bb"})).user, "")

    print("\n-- wat d'Astellungssäit NET dierf --")
    # ⚠ Dat hei ass strukturell, net eng Reegel: `local` kënnt aus
    #   ENV_AUTH_MODES an aus soss näischt. Et gëtt guer keng Funktioun déi et
    #   uréiere kann -- dofir gëtt hei gepréift, datt keng Zuel vu Klicken um
    #   Proxy-Schalter de Passwuert-Wee beweegt.
    config.set_setting("oidc_issuer", "https://id.example.com")
    config.set_setting("oidc_client_id", "roamlight")
    config.set_auth_oidc(True)
    check("Proxy un -> `local` bleift wéi d'YAML et seet",
          sorted(config.auth_modes()), ["local", "oidc"])
    config.set_auth_oidc(False)
    check("de Proxy erëm ausschalten geet", sorted(config.auth_modes()), ["local"])

    print("\n-- an engem Haus wou de Proxy dee EENZEGE Wee ass --")
    # Dat ass de Live. Fréier hunn hei "ausschalten" de Site op `local` gestallt
    # -- an do gëtt et KEEN eenzege Kont mat Passwuert (d'Leit kommen aus dem
    # Verzeechnes). Dann ass /setup nees op, a wien d'Adress erreecht, ass
    # Administrator vun de Fotoen vun enger Famill.
    keep_env = config.ENV_AUTH_MODES
    config.ENV_AUTH_MODES = {"oidc"}
    config.forget_auth_modes()
    # ⚠ An der Datebank steet nach "Proxy aus" vun der Zeil hei uewen -- an dat
    #   dierf de Site NET eidel maachen. D'YAML gewënnt.
    check("e gespäichert 'aus' mécht de Site net eidel",
          sorted(config.auth_modes()), ["oidc"])
    raises("den eenzege Wee ausschalten", config.set_auth_oidc, False)
    check("... an et huet sech näischt geréiert", sorted(config.auth_modes()), ["oidc"])
    check("... a `local` ass ëmmer nach aus", config.auth_local(), False)
    config.ENV_AUTH_MODES = keep_env
    config.forget_auth_modes()
    config.set_auth_oidc(False)

    print("\n-- uschalten a ausschalten --")
    # ⚠ Fréier stoung hei arm-and-confirm: uschalten huet nëmmen eng Wiel mat
    #   enger Frist opgeschriwwen, an eréischt eng Ufro, déi DUERCH de Proxy
    #   koum, huet se festgemaach. Dat huet bewisen, datt de gedeelte Geheimnis
    #   wierklech vum Proxy bis an d'App kënnt. Deen Ëmwee gëtt et net méi: d'App
    #   schwätzt selwer mam Ubidder, also gëtt et kee Geheimnis, dat een duerch
    #   e Réckwee beweise misst.
    config.set_auth_oidc(False)
    config.set_setting("oidc_issuer", "")
    config.set_setting("oidc_client_id", "")
    raises("uschalten ouni Ausgeber", config.set_auth_oidc, True)
    config.set_setting("oidc_issuer", "https://id.example.com")
    raises("uschalten ouni Client", config.set_auth_oidc, True)
    config.set_setting("oidc_client_id", "roamlight")
    config.set_auth_oidc(True)
    check("elo geet et un", config.auth_oidc(), True)
    check("... a `local` ass onberéiert", config.auth_local(), True)
    config.set_auth_oidc(False)
    check("an nees aus", config.auth_oidc(), False)

    print("\n-- d'YAML huet dat lescht Wuert --")
    config.AUTH_LOCKED = True
    config.ENV_AUTH_MODES = {"oidc"}
    config.forget_auth_modes()
    check("gespaart -> d'Datebank gëtt ignoréiert", sorted(config.auth_modes()), ["oidc"])
    raises("gespaart -> d'Säit dierf näischt", config.set_auth_oidc, True)
    config.AUTH_LOCKED = False
    config.ENV_AUTH_MODES = {"local"}
    config.forget_auth_modes()

    print("\n-- d'SSO-Adress dierf net aus dem Haus weisen --")
    # ⚠ Déi zwee mat dem Backslash an dem Tab sinn duerch dee éischte Filter
    #   duerchgaang: e Browser mécht aus `/\x` `//x`, an Tabs a Reihenëmbréch
    #   ginn ewechgehäit IER d'Adress iwwerhaapt geliest gëtt.
    for value, want in (("/outpost.goauthentik.io/start", "/outpost.goauthentik.io/start"),
                        ("/s/abc?x=1&y=2", "/s/abc?x=1&y=2"),
                        ("//evil.example/start", ""),
                        ("/\\evil.example", ""),
                        ("/\tevil.example", ""),
                        ("/\nevil.example", ""),
                        ("/\revil.example", ""),
                        ("https://evil.example/start", ""),
                        ("javascript:alert(1)", ""),
                        ("", "")):
        check(f"sso_path({value!r})", config.sso_path(value), want)

    print("\n-- e Geheimnis dat jiddereen liese kann ass keent --")
    # ⚠ Fréier huet dat den `arm_auth` refuséiert. Dee gëtt et net méi, an d'Fro
    #   bleift: e Geheimnis, dat all Kont op der Maschinn liese kann, ass keent.
    #   Elo mécht `write_secret` d'Datei enk, an d'Gesondheetsofro seet et, wann
    #   eng vun Hand geluechten ze breet steet.
    sec = config.secret_path("oidc")
    config.write_secret("oidc", "e-Client-Geheimnis")
    check("frësch geschriwwen ass 0600", oct(sec.stat().st_mode & 0o777), "0o600")
    sec.chmod(0o644)
    check("ze breet gëtt gemierkt", config.secret_is_readable_by_all("oidc"), True)
    config.write_secret("oidc", "nach eng Kéier")
    check("nei geschriwwen ass et nees enk", oct(sec.stat().st_mode & 0o777), "0o600")
    check("... a gemierkt gëtt näischt méi", config.secret_is_readable_by_all("oidc"), False)

    print("\n-- de Bremszieler dierf net vun engem Ugräifer geläscht ginn --")
    auth._fails.clear()
    for _ in range(auth.MAX_TRIES):
        auth.note_fail("198.51.100.7")
    check("no zéng Feeler ass déi Adress gespaart", auth.blocked("198.51.100.7"), True)
    # Elo iwwerschwemmt en Ugräifer d'Tabell -- fréier huet dat ALLES geläscht,
    # och säin eegene Zieler.
    for i in range(5200):
        auth.note_fail(f"203.0.113.{i // 250}.{i % 250}")
    check("... a bleift et och no 5200 erfonnten Adressen",
          auth.blocked("198.51.100.7"), True)
    auth._fails.clear()

    print("\n-- `Identity.local` gëtt et net méi --")
    # ⚠ Dat Feld gouf NËMMEN um Forward-Auth-Wee gesat. Zënter deen ewech ass,
    #   wier et ëmmer falsch gewiescht -- an e Feld, dat ëmmer falsch ass, ass
    #   eng Fal: deen nächsten baut eng Kontroll drop. "Vun dëser Maschinn" gëtt
    #   elo do gefrot, wou et gebraucht gëtt (`health()` an der main.py).
    check("d'Feld ass fort", hasattr(security._empty(), "local"), False)

    print("\n-- d'Astellunge kommen elo vun der Säit, net méi just aus der YAML --")
    check("Standard kënnt aus der Ëmwelt", sorted(config.admin_groups()), ["admin"])
    config.set_setting("admin_groups", "Group-Admin, Website-Admin")
    check("d'Säit huet se gesat", sorted(config.admin_groups()),
          ["Group-Admin", "Website-Admin"])
    check("... an déi al Nimm gëllen net méi", "admin" in config.admin_groups(), False)
    # ⚠ Déi ~50 Plazen am Programm liesen `config.ADMIN_GROUPS` -- dat muss
    #   deeselwechte Wäert ginn, soss gëllt d'Astellung fir d'Rechter net.
    check("config.ADMIN_GROUPS gëtt datselwecht", sorted(config.ADMIN_GROUPS),
          ["Group-Admin", "Website-Admin"])
    config.set_setting("admin_groups", "admin")
    check("zréckgesat", sorted(config.ADMIN_GROUPS), ["admin"])

    config.set_setting("trusted_peers", "127.0.0.1, 10.1.2.3")
    check("Peeren och", sorted(config.TRUSTED_PEERS), ["10.1.2.3", "127.0.0.1"])
    config.set_setting("trusted_peers", "127.0.0.1,::1")

    raises("eng eidel Grupp-Lëscht", config.set_setting, "viewer_groups", "")
    raises("eng Astellung déi et net gëtt", config.set_setting, "wat-och-ëmmer", "x")

    print("\n-- d'Geheimnisser goen an Dateien, net an d'Datebank --")
    # ⚠ De Backup mécht VACUUM INTO op d'Datebank. Wat do steet, läit an 14
    #   Kopien am Kloertext -- an dës zwee sinn genee dat, wat een aus engem
    #   geklauten Backup wëll.
    config.write_secret("proxy", "n" * 40)
    check("geschriwwen a nees gelies", config.proxy_secret(), "n" * 40)
    check("Modus ass 0600", oct(config.secret_path("proxy").stat().st_mode & 0o777), "0o600")
    rows = db.connect().execute(
        "SELECT COUNT(*) AS n FROM state WHERE value LIKE ?", ("%" + "n" * 40 + "%",)).fetchone()
    check("steet NET an der Datebank", rows["n"], 0)
    config.write_secret("proxy", "s" * 48)

    print("\n-- /setup geet op engem Site mat Proxy NI op --")
    # ⚠ Dat ass déi zweet Spär vun der schlëmmster Saach an dëser Ännerung:
    #   "nach kee Kont" ass op engem Site mam Verzeechnes den NORMALE Zoustand,
    #   net eng frësch Installatioun. Wier d'Éischtstart-Säit do erreechbar,
    #   wier jiddereen ee Formulaire vun der ganzer Bibliothéik ewech.
    config.set_auth_oidc(False)
    keep_token = config.SETUP_TOKEN
    config.SETUP_TOKEN = ""
    check("ouni Proxy an ouni Token: op", config.setup_is_open(), True)
    config.set_setting("oidc_issuer", "https://id.example.com")
    config.set_setting("oidc_client_id", "roamlight")
    config.set_auth_oidc(True)
    check("mam Ubidder: zou", config.setup_is_open(), False)
    # ⚠ Mam RICHTEGE Wuert, net mat engem erfonnten. Virdru stoung hei
    #   `setup_is_open("wat och ëmmer")` bei engem eidele SETUP_TOKEN -- dat
    #   war e falscht Wuert, an d'Ausso wier och duerchgaang, wann
    #   `setup_is_open()` den Ubidder guer net beuecht hätt. Vum zweeten
    #   Duerchgang gemellt: en Test, deen och beim falsche Code gréng ass.
    config.SETUP_TOKEN = "d-richtegt-Wuert"
    check("... an och mam RICHTEGE Wuert nach zou",
          config.setup_is_open("d-richtegt-Wuert"), False)
    config.SETUP_TOKEN = ""
    config.set_auth_oidc(False)
    config.SETUP_TOKEN = "d-Wuert"
    check("ouni Proxy, mä mat engem Token: zou ouni d'Wuert", config.setup_is_open(""), False)
    check("... a falscht Wuert: zou", config.setup_is_open("falsch"), False)
    check("... a richtegt Wuert: op", config.setup_is_open("d-Wuert"), True)
    config.SETUP_TOKEN = keep_token

    print("\n-- d'CLI mécht de Kont, an duerno ass /setup zou --")
    check("virdrun war ee Kont do (mila)", auth.has_local_users(), True)
    import io
    keep_stdin = sys.stdin
    sys.stdin = io.StringIO("nach-eng-laang-Sach\n")
    cli.main(["user", "add", "Rita", "--admin", "--password-stdin"])
    sys.stdin = keep_stdin
    check("de Numm gëtt kleng geschriwwen", auth.check("RITA", "nach-eng-laang-Sach"), "rita")
    check("... a se ass Admin",
          security._identify(FakeRequest("127.0.0.1", {}, cookies={
              config.SESSION_COOKIE: auth.new_session("rita")})).is_admin, True)
    print("\n-- eng Passwuert-Ännerung dréit och d'Telefonen aus --")
    # ⚠ `end_all()` huet just d'Sessiounen geläscht. E gepaarte Telefon huet en
    #   Token, dee ni oflaf a bei all Ufro gëllt -- wien dat al Passwuert hat,
    #   konnt sech ee minten an huet duerno weider Zougang gehat. Eng
    #   Zréckzéiung, déi d'Telefonen stoe léisst, huet näischt zréckgezunn.
    from app import devices
    tok = devices.mint("rita", "en Telefon", "127.0.0.1")["token"]
    check("den Token gëllt", devices.identify(tok), "rita")
    auth.new_session("rita")
    n = auth.end_all("rita")
    check("Sessiounen an Apparater ausgedroen", n >= 2, True)
    check("... an den Token gëllt net méi", devices.identify(tok), None)

    print("\n-- wat de Ubidder ewechhëlt, hëlt de Site och ewech --")
    # ⚠ Bis den 08.09.2026 huet `refresh()` een, dee GUER NET MÉI an de Gruppen
    #   ass, einfach net ugefaasst -- seng al Gruppe stoungen weider do. Ënner
    #   dem Forward-Auth war dat egal (d'nginx huet bei all Ufro nogefrot);
    #   zënter datt de Site selwer entscheet, hätt dat 30 Deeg gedauert.
    from app import members as mem
    config.set_setting("admin_groups", "Group-Admin")
    config.set_setting("viewer_groups", "Website-Family")
    config.set_setting("contributor_groups", "Website-Family")

    def verzeechnes(*leit):
        return [{"name": "Website-Family", "users_obj": [
                    {"username": u, "name": u, "email": "", "is_active": True} for u in leit]},
                {"name": "Group-Admin", "users_obj": []}]

    mem.configured = lambda: True
    mem._call = lambda path: {"results": verzeechnes("alice", "bob")}
    mem.refresh()
    check("béid sinn do", sorted(
        r["username"] for r in db.connect().execute(
            "SELECT username FROM members WHERE seen_in_authentik=1")), ["alice", "bob"])
    check("an dierfe kucken", security._from_member("bob").is_viewer, True)

    # De Bob kritt e Cookie an en Telefon -- soll herno béid net méi hunn.
    bob_cookie = auth.new_session("bob")
    from app import devices as dev
    bob_tok = dev.mint("bob", "Telefon", "127.0.0.1")["token"]

    # An elo hëlt den Authentik de Bob aus der Grupp.
    mem._call = lambda path: {"results": verzeechnes("alice")}
    out = mem.refresh()
    check("de Bob ass erausgefall", out["dropped"], ["bob"])
    check("... an huet keng Gruppen méi", security._from_member("bob").is_viewer, False)
    check("... seng Sessioun ass zou", auth.session_user(bob_cookie), None)
    check("... a säin Telefon och", dev.identify(bob_tok), None)
    check("d'Alice ass onberéiert", security._from_member("alice").is_viewer, True)
    # ⚠ D'Zeil bleift stoen: si steet an de Kucklëschten vun den Albumen, an
    #   eng Lëscht, déi roueg en Numm verléiert, mécht en Album OP.
    check("mä d'Zeil steet nach do", bool(db.connect().execute(
        "SELECT 1 FROM members WHERE username='bob'").fetchone()), True)

    # ⚠ An elo déi Saach, déi dëse Laf ALL STONN kaputt maache kéint: e lokale
    #   Kont kënnt guer net vum Verzeechnes, also ass en ëmmer "net gesinn". Géif
    #   d'Zréckhuelung hien matgräifen, da wier den Noutfall-Admin all Stonn
    #   automatesch degradéiert -- an de Wee eran, wann de Ubidder ausfält, wier
    #   grad dann zou, wann ee en brauch.
    auth.create_user("noutfall", "eng-laang-Noutfall-Sach",
                     groups=[sorted(config.admin_groups())[0]])
    check("virum Laf ass en Admin", security._from_member("noutfall").is_admin, True)
    mem.refresh()
    check("no dem Laf ëmmer nach", security._from_member("noutfall").is_admin, True)
    check("... an et gëtt en Admin mat Passwuert", auth.has_local_admin(), True)

    print("\n-- d'CLI degradéiert keen roueg --")
    sys.stdin = io.StringIO("nach-eng-laang-Sach\n")
    try:
        cli.main(["user", "add", "rita", "--password-stdin"])
        print("  FAIL en Kont deen et scho gëtt gouf roueg iwwerschriwwen")
        globals()['bad'] = globals()['bad'] + 1
    except SystemExit as exc:
        print(f"  ok   e Kont deen et scho gëtt gëtt net roueg iwwerschriwwen")
    sys.stdin = keep_stdin
    check("... a si ass ëmmer nach Admin", auth.has_local_admin(), True)

    sys.stdin = io.StringIO("kuerz\n")
    try:
        cli.main(["user", "add", "Tom", "--password-stdin"])
        print("  FAIL e kuerzt Passwuert gouf ugeholl")
        globals()['bad'] = globals()['bad'] + 1
    except SystemExit as exc:
        print(f"  ok   e kuerzt Passwuert gëtt refuséiert -- {exc}")
    sys.stdin = keep_stdin

    print(f"\n{'ALLES GRÉNG' if not bad else str(bad) + ' FEELER'}")
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
