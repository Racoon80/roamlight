#!/usr/bin/env python3
"""The app's single sign-on: /app/sso -> a code bound to the app's secret.

    python3 tests/app_sso_check.py

Hermetic, like oidc_check.py: its own database under /tmp, no network, no
provider. The provider half is oidc_check's job; what is checked here is the
part after it -- a signed-in browser, the question page, the code, and the
app redeeming it.

What must hold:
  * /api/app/ways answers without an account.
  * /app/sso asks and hands out NOTHING on a GET.
  * the code comes only from a same-site POST, bound to the challenge.
  * without the right verifier the code is worthless -- and burnt.
  * with it: a token, exactly once.
"""
import base64
import hashlib
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TMP = Path(tempfile.mkdtemp(prefix="roamlight-appsso-"))
os.environ.update(
    FAMILY_DATA=str(TMP / "data"), FAMILY_DB=str(TMP / "data" / "t.db"),
    FAMILY_ORIGINS=str(TMP / "o"), FAMILY_WEB=str(TMP / "w"),
    FAMILY_DERIVATIVES=str(TMP / "data" / "d"), FAMILY_INCOMING=str(TMP / "data" / "i"),
    FAMILY_WORK=str(TMP / "data" / "work"), FAMILY_REQUIRE_MOUNT="0",
    FAMILY_AUTH="local", FAMILY_SITE_URL="https://photos.example.com",
)
for d in ("data", "o", "w", "data/d", "data/i", "data/work"):
    (TMP / d).mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient                       # noqa: E402

from app import auth, config, db                                # noqa: E402
from app.main import app                                        # noqa: E402

bad = 0


def check(name, got, want):
    global bad
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" -- expected {want!r}"))
    bad += 0 if ok else 1


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


try:
    db.init()
    group = sorted(config.viewer_groups())[0]
    auth.create_user("aunt", "a long enough password 1", groups=[group])
    c = TestClient(app, base_url="https://photos.example.com")

    r = c.get("/api/app/ways")
    check("ways without an account", (r.status_code, r.json().get("password")), (200, True))

    verifier = b64url(secrets.token_bytes(32))
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())

    # Not signed in: the page is not for strangers.
    r = c.get(f"/app/sso?challenge={challenge}", follow_redirects=False,
              headers={"accept": "application/json"})
    check("question page refused without a session", r.status_code, 403)

    c.cookies.set(config.SESSION_COOKIE, auth.new_session("aunt"))
    r = c.get(f"/app/sso?challenge={challenge}")
    check("question page, signed in", r.status_code, 200)
    check("the page names the account", "aunt" in r.text, True)
    check("a GET makes no code", db.connect().execute(
        "SELECT COUNT(*) FROM app_pairings").fetchone()[0], 0)
    r = c.get("/app/sso?challenge=short")
    check("a malformed challenge", r.status_code, 400)

    r = c.post("/api/app/sso", json={"challenge": challenge},
               headers={"sec-fetch-site": "cross-site"})
    check("cross-site POST refused", r.status_code, 403)

    r = c.post("/api/app/sso", json={"challenge": challenge},
               headers={"sec-fetch-site": "same-origin"})
    check("same-site POST", r.status_code, 200)
    url = r.json().get("url", "")
    check("answer is a roamlight:// address", url.startswith("roamlight://sso?c="), True)
    code = parse_qs(urlparse(url).query)["c"][0]

    app_c = TestClient(app, base_url="https://photos.example.com")    # the phone
    r = app_c.post("/api/app/pair", json={"code": code, "name": "phone",
                                          "verifier": b64url(secrets.token_bytes(32))})
    check("wrong verifier refused", r.status_code, 401)
    r = app_c.post("/api/app/pair", json={"code": code, "name": "phone", "verifier": verifier})
    check("... and the code is burnt by it", r.status_code, 401)

    r = c.post("/api/app/sso", json={"challenge": challenge},
               headers={"sec-fetch-site": "same-origin"})
    code = parse_qs(urlparse(r.json()["url"]).query)["c"][0]
    r = app_c.post("/api/app/pair", json={"code": code, "name": "phone"})
    check("no verifier refused", r.status_code, 401)

    r = c.post("/api/app/sso", json={"challenge": challenge},
               headers={"sec-fetch-site": "same-origin"})
    code = parse_qs(urlparse(r.json()["url"]).query)["c"][0]
    r = app_c.post("/api/app/pair", json={"code": code, "name": "phone", "verifier": verifier})
    check("right verifier: a token", (r.status_code, r.json().get("user")), (200, "aunt"))
    token = r.json().get("token", "")
    r = app_c.post("/api/app/pair", json={"code": code, "name": "phone", "verifier": verifier})
    check("once only", r.status_code, 401)
    r = app_c.get("/api/app/me", headers={"authorization": f"Bearer {token}"})
    check("the token works", (r.status_code, r.json().get("user")), (200, "aunt"))

    # Taken off the site: the phone must hear 401 (it signs itself out on
    # that), not the 403 a stranger gets -- with 403 it was left stranded.
    from app import devices
    with db.tx() as c_:
        c_.execute("UPDATE app_devices SET revoked_at=datetime('now') WHERE username='aunt'")
    devices._CACHE.clear() if hasattr(devices, "_CACHE") else None
    r = app_c.get("/api/app/me", headers={"authorization": f"Bearer {token}"})
    check("a revoked device hears 401", r.status_code, 401)
    r = app_c.get("/api/albums", headers={"authorization": "Bearer fam_nonsense_token"})
    check("a made-up token hears 401", r.status_code, 401)
    r = TestClient(app, base_url="https://photos.example.com").get(
        "/api/albums", headers={"accept": "application/json"})
    check("no token at all still hears 403", r.status_code, 403)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("app sso:", "all good" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
