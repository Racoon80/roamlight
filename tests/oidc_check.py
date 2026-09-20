"""D'Préifung vum id_token — dat Stéck, wou en Ausrutscher d'Haus opmécht.

E `id_token` ass eng Zeechenkette, déi e Frieme geschéckt huet. Si parst zu ganz
normalem JSON, egal ob se echt ass — an dat ass genee d'Gefor: wien `email`
dorauser liest, ier d'Ënnerschrëft gepréift ass, huet keng Umeldung gebaut, mä
en Uleedungsformulaire fir jiddereen.

Dësen Test mécht sech en eegene Schlëssel, baut sech en eegene JWKS, an
ënnerschreift Tokens selwer. Dann probéiert en all Wee, dee bekannt ass:

  * `alg: none` an `HS256` (dee géif mam ÖFFENTLECHE Schlëssel gepréift)
  * eng Ënnerschrëft vun engem anere Schlëssel
  * en anere Ausgeber, en anert Publikum, ofgelaf
  * eng `nonce`, déi net zu dëser Umeldung gehéiert
  * e `state`, deen zweemol benotzt gëtt

⚠ Hermetesch: eegen Datebank am /tmp, keng Netzverbindung. D'Endpunkte ginn
  vun Hand gesat, sou datt keng Entdeckung leeft.
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="roamlight-oidc-"))
os.environ.update(
    FAMILY_DATA=str(TMP / "data"), FAMILY_DB=str(TMP / "data" / "t.db"),
    FAMILY_ORIGINS=str(TMP / "o"), FAMILY_WEB=str(TMP / "w"),
    FAMILY_DERIVATIVES=str(TMP / "data" / "d"), FAMILY_INCOMING=str(TMP / "data" / "i"),
    FAMILY_WORK=str(TMP / "data" / "work"), FAMILY_REQUIRE_MOUNT="0",
    FAMILY_AUTH="local", FAMILY_SITE_URL="https://photos.example.com",
)


def _app_root() -> str:
    here = Path(__file__).resolve().parent
    for c in (os.environ.get("FAMILY_BASE"), here.parent,
              "/opt/family/app", "/opt/roamlight/app-src"):
        if c and (Path(c) / "app" / "__init__.py").is_file():
            return str(c)
    sys.exit("cannot find the program -- set FAMILY_BASE")


sys.path.insert(0, _app_root())
from app import config, db, oidc                                # noqa: E402

from cryptography.hazmat.primitives.asymmetric import rsa       # noqa: E402

bad = 0
ISSUER = "https://id.example.com"
CLIENT = "roamlight"


def check(name, got, want):
    global bad
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" -- erwaart {want!r}"))
    bad += 0 if ok else 1


def refused(name, fn, *a, **kw):
    """Muss refuséiert ginn — an et zielt NËMMEN, wann et en OidcError ass."""
    global bad
    try:
        fn(*a, **kw)
    except oidc.OidcError as exc:
        print(f"  ok   {name}: refuséiert — {exc}")
        return
    except Exception as exc:                                     # noqa: BLE001
        print(f"  FAIL {name}: falsche Feeler ({exc.__class__.__name__}: {exc})")
        bad += 1
        return
    print(f"  FAIL {name}: GOUF UGEHOLL, an dat dierf net")
    bad += 1


def b64(raw) -> str:
    if isinstance(raw, (dict, list)):
        raw = json.dumps(raw, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks_for(key, kid="k1"):
    n = key.public_key().public_numbers()
    return {"keys": [{
        "kty": "RSA", "kid": kid, "alg": "RS256", "use": "sig",
        "n": b64(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")),
        "e": b64(n.e.to_bytes((n.e.bit_length() + 7) // 8, "big")),
    }]}


def sign(claims, key=KEY, alg="RS256", kid="k1"):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    head = b64({"alg": alg, "typ": "JWT", "kid": kid})
    body = b64(claims)
    if alg == "none":
        return head + "." + body + "."
    sig = key.sign((head + "." + body).encode(), padding.PKCS1v15(), hashes.SHA256())
    return head + "." + body + "." + b64(sig)


def token(**over):
    c = {"iss": ISSUER, "aud": CLIENT, "exp": time.time() + 300,
         "iat": time.time(), "nonce": "N", "sub": "u1",
         "preferred_username": "Guy", "email": "guy@example.com",
         "name": "Guy Gerson", "groups": ["Group-Admin"]}
    c.update(over)
    return c


def main():
    db.init()
    # ⚠ Set on the module and not through a settings page: in this repository
    #   every setting is an environment variable read once at import. The
    #   endpoints are given by hand so that NO discovery runs -- this test does
    #   not touch the network.
    config.AUTH_OIDC = True
    config.OIDC_ISSUER = ISSUER
    config.OIDC_CLIENT_ID = CLIENT
    config.OIDC_ENDPOINT_OVERRIDES = {
        "authorization": ISSUER + "/authorize",
        "token": ISSUER + "/token",
        "jwks": ISSUER + "/jwks",
        "userinfo": "", "end_session": "",
    }
    # ⚠ KENG Netzverbindung -- mä och kee virgefëllte Cache. De Cache ze fëllen
    #   heescht, datt de Wee, deen d'Schlësselen HËLT, ni leeft: an dat ass genee
    #   dee Wee, wou e falsche Schlëssel eran kéim. Dofir gëtt d'HTTP-Schicht
    #   ersat, an net d'Äntwert.
    holl = {"n": 0}

    class FakeAntwert:
        def __init__(self, doc): self._doc = doc
        def raise_for_status(self): pass
        def json(self): return self._doc

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url):
            holl["n"] += 1
            return FakeAntwert(jwks_for(KEY))

    oidc._http = lambda: FakeClient()
    oidc._cache.clear()

    print("\n-- d'Schlësselen ginn wierklech gehollef, net virgefëllt --")
    check("de Cache ass eidel", "jwks" in oidc._cache, False)
    oidc.verify_id_token(sign(token()), "N")
    check("een Hol", holl["n"], 1)
    oidc.verify_id_token(sign(token()), "N")
    check("an duerno aus dem Cache", holl["n"], 1)
    # ⚠ E Schlëssel, deen de Saz net kennt: eemol nofroen, an dann Nee soen.
    #   Dat ass de Wee, deen de virgefëllte Cache verstoppt huet.
    refused("en onbekannte kid", oidc.verify_id_token, sign(token(), kid="gëtt-et-net"), "N")
    check("dofir gouf nach eemol gehollef", holl["n"], 2)

    print("\n-- en echten Token geet duerch --")
    claims = oidc.verify_id_token(sign(token()), "N")
    check("d'Aussoe kommen zréck", claims["preferred_username"], "Guy")

    print("\n-- an elo all Wee, deen ee probéiert --")
    refused("alg: none", oidc.verify_id_token, sign(token(), alg="none"), "N")
    # ⚠ HS256 géif mam ÖFFENTLECHE Schlëssel als Geheimnis gepréift -- an deen
    #   ass ëffentlech. Dat ass déi klassesch Verwiesslung.
    refused("HS256 (symmetresch)", oidc.verify_id_token, sign(token(), alg="HS256"), "N")
    refused("en anere Schlëssel", oidc.verify_id_token, sign(token(), key=OTHER), "N")
    refused("en anere Ausgeber", oidc.verify_id_token, sign(token(iss="https://boes.example")), "N")
    refused("en anert Publikum", oidc.verify_id_token, sign(token(aud="eng-aner-App")), "N")
    refused("ofgelaf", oidc.verify_id_token, sign(token(exp=time.time() - 3600)), "N")
    refused("aus der Zukunft", oidc.verify_id_token, sign(token(iat=time.time() + 9999)), "N")
    refused("eng friem nonce", oidc.verify_id_token, sign(token(nonce="ENG-ANER")), "N")
    refused("guer keng nonce", oidc.verify_id_token, sign(token(nonce=None)), "N")
    refused("kee Token", oidc.verify_id_token, "dat-ass-keen-token", "N")
    # ⚠ D'Nutzlaascht geännert, d'Ënnerschrëft gelooss.
    t = sign(token())
    h, pl, sg = t.split(".")
    refused("Aussoe geännert, Ënnerschrëft gelooss",
            oidc.verify_id_token, h + "." + b64(token(groups=["Group-Admin"], sub="ech")) + "." + sg, "N")

    print("\n-- de Wee eran: PKCE, state, nonce --")
    url, state = oidc.begin("/albums")
    # ⚠ De `state` kënnt elo mat zréck, well den Opruffer en an e Cookie setze
    #   MUSS. `state` eleng seet "iergendee Browser huet dat ugefaang", net
    #   "dëse Browser". Kuck d'Notiz op `oidc.begin()`.
    check("de state kënnt mat zréck", isinstance(state, str) and len(state) > 20, True)
    check("PKCE S256", "code_challenge_method=S256" in url, True)
    check("state derbäi", "state=" in url, True)
    check("nonce derbäi", "nonce=" in url, True)
    check("de Client", "client_id=" + CLIENT in url, True)
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(url).query)
    st = q["state"][0]
    check("... an et ass dee selwechten", st, state)
    row = oidc._take(st)
    check("de state léist d'Umeldung aus", row is not None, True)
    check("... a genee EEMOL", oidc._take(st), None)
    check("de Wee zréck ass gemierkt", row["next"], "/albums")

    print("\n-- e Wee zréck, deen aus dem Haus weist, gëtt net gemierkt --")
    q2 = parse_qs(urlparse(oidc.begin("//evil.example/")[0]).query)
    check("//evil.example -> /", oidc._take(q2["state"][0])["next"], "/")

    print("\n-- vun den Aussoen op eng Persoun --")
    who = oidc.identity(token())
    # ⚠ Genee esou, wéi de Ubidder en schreift. Kleng gemaach huet um Live all
    #   Album-Recht net méi getraff: d'Zeile soen `user:Guy`, an aus `Guy` gouf
    #   e `guy`, deen näischt gesäit.
    check("Numm bleift wéi de Ubidder en schreift", who["username"], "Guy")
    check("Gruppen", who["groups"], ["Group-Admin"])
    config.OIDC_GROUPS_CLAIM = "roles"
    check("en anere Grupp-Anspréch", oidc.identity(token(roles="a b"))["groups"], ["a", "b"])
    config.OIDC_GROUPS_CLAIM = "groups"

    print("\n-- wien an enger frieme Grupp ass, kënnt net eran --")
    config.ADMIN_GROUPS = {"Group-Admin"}
    config.VIEWER_GROUPS = {"Group-Family"}
    config.CONTRIBUTOR_GROUPS = {"Group-Family"}
    refused("keng bekannte Grupp", oidc.sign_in, token(groups=["irgendeppes"]))
    cookie = oidc.sign_in(token())
    from app import auth, security
    check("mat enger bekannter Grupp: eng Sessioun", bool(cookie), True)
    check("... an de Site kennt d'Persoun", auth.session_user(cookie), "Guy")

    row = db.connect().execute(
        "SELECT is_local, password_hash, groups_json FROM members WHERE username='Guy'").fetchone()
    # ⚠ Kee Passwuert an is_local=0: dëse Kont ass keen "lokale Kont", also
    #   zielt en net fir `has_local_users()` -- an domat bleift /setup zou.
    check("kee Passwuert-Kont", (row["is_local"], row["password_hash"]), (0, None))
    check("d'Gruppen aus dem Token", json.loads(row["groups_json"]), ["Group-Admin"])
    check("an d'Rechter stëmmen", security._from_member("Guy").is_admin, True)
    # ⚠ Genee dee Fall, deen um Live opgefall ass: eng Rechter-Zeil, déi op déi
    #   Schreifweis vum Ubidder lautt, muss no der Umeldung treffen.
    from app import acl
    db.connect().execute(
        "INSERT OR IGNORE INTO album_acl (album_key, principal) VALUES (?,?)",
        ("2026/Portugal/Porto", "user:Guy"))
    check("eng Rechter-Zeil op `user:Guy` trëfft",
          "user:Guy" in acl.principals_for(security._from_member("Guy")), True)
    check("/setup bleift zou", auth.has_local_users(), False)

    print("\n-- eng Umeldung dierf keen Passwuert-Kont iwwerhuelen --")
    # ⚠ Béid Duerchgäng vum 08.09.2026 hunn dat als dat Schaarft gefonnt: den
    #   Upsert lauft op de bloussen Numm, an de Numm kënnt aus engem Anspréch,
    #   deen d'Persoun beim Ubidder dacks selwer setze kann. Wien sech do
    #   "admin" nennt, kritt soss eng Sessioun op de Noutfall-Admin -- an
    #   iwwerschreift him dobäi seng Gruppen, sou datt keen Admin méi do ass.
    from app import auth as _auth
    _auth.create_user("notfall", "eng-laang-Noutfall-Sach",
                      groups=[sorted(config.ADMIN_GROUPS)[0]])
    refused("op e Kont mat Passwuert", oidc.sign_in, token(preferred_username="notfall"))
    row = db.connect().execute(
        "SELECT is_local, password_hash IS NOT NULL AS pw, groups_json "
        "FROM members WHERE username='notfall'").fetchone()
    check("de Kont ass onberéiert", (row["is_local"], row["pw"]), (1, 1))
    check("... an huet seng Gruppen nach", json.loads(row["groups_json"]),
          [sorted(config.ADMIN_GROUPS)[0]])
    check("... an et gëtt nach en Admin mat Passwuert", _auth.has_local_admin(), True)

    print("\n-- an d'Adressen, op déi een de Site weise kann --")
    for wert, gëllt in (("https://id.example.com/x", True),
                        ("http://localhost:9000/x", True),
                        ("http://192.168.1.5:9000/x", False),   # kloertext iwwer d'Netz
                        ("ftp://id.example.com", False),
                        ("https://id.example.com/x?a=b", False)):  # wielt de Pfad vun der Entdeckung
        check(f"issuer {wert}", config.oidc_issuer_ok(wert), gëllt)
    config.OIDC_ISSUER = ISSUER

    print(f"\n{'ALLES GRÉNG' if not bad else str(bad) + ' FEELER'}")
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
