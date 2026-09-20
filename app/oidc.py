"""Signing in through an identity provider -- the app talks to it itself.

    /auth/oidc/login     -> off to the provider
    /auth/oidc/callback  <- back with a code, and a session at the end of it

⚠ **What this replaces.** The site used to sit behind forward-auth: nginx asked
  Authentik on every request and passed the answer along in headers, and the app
  believed those headers because a shared secret came with them. That works, and
  it puts the whole arrangement in the reverse proxy's configuration -- three
  files, an outpost, and a secret that has to match in two places. This does the
  same job in the program: the app is an OpenID Connect client, the way any
  other application is, and nginx goes back to being a reverse proxy.

⚠ **The end of this road is an ordinary session.** The callback creates the same
  cookie `auth.new_session()` gives a password sign-in, so `security.identify()`
  did not gain a fourth road -- it gained a second way of arriving on the first
  one. Nothing else in the site knows the difference.

## The order of operations, and why it is that order

Every step below exists because leaving it out is a known way in:

  * **PKCE (S256)** -- an authorisation code stolen out of a redirect (a log, a
    Referer, a shoulder) is worthless without the verifier, which never leaves
    this process.
  * **`state`** -- one-use, server-side. Without it somebody can hand a victim's
    browser a callback URL of their own and log them into the attacker's account.
  * **`nonce`** -- carried into the request and checked back out of the token.
    Without it a token minted for another session can be replayed into this one.
  * **The signature, `iss`, `aud`, `exp`** -- checked against the provider's
    published keys **before a single claim is read**. An `id_token` is a
    stranger's JSON until that has happened. Reading `email` out of an unverified
    token is the whole vulnerability, and it is an easy one to write by accident:
    the JSON parses perfectly well.

⚠ **No new dependency.** `cryptography` is already here for the push keys, and
  the verification below builds the provider's public key out of the JWKS by
  hand. A JWT library would be one more thing to keep patched for the sake of
  sixty lines.

⚠ **Where the settings come from here.** This module was written for an
  installation that keeps its settings in a page and a database. In this
  repository every setting is an environment variable, like `FAMILY_AUTH` and
  the proxy secret, so the reads were changed to match and nothing else was:

      FAMILY_AUTH=local+oidc
      FAMILY_OIDC_ISSUER=https://auth.example.com/application/o/roamlight/
      FAMILY_OIDC_CLIENT_ID=...
      /etc/roamlight/oidc-secret          (the client secret, or leave it out)

  The checks below -- PKCE, state, the state cookie, nonce, signature, `iss`,
  `aud`, `exp` -- are exactly as they were, and so is the refusal to merge a
  provider account onto a password account, which is the sharpest thing in the
  file.
"""
import base64
import hashlib
import json
import logging
import secrets
import time

from . import config, db

log = logging.getLogger("family")

# How long a sign-in may be in flight. Long enough to type a password and answer
# a second factor; short enough that an abandoned one is gone.
PENDING_MINUTES = 15
# Discovery and the keys are asked for once and then remembered. A provider that
# rotates its keys publishes the new one before it uses it, so a miss is
# refetched (see `_jwks`).
_cache: dict = {}
_CACHE_SECONDS = 3600


class OidcError(Exception):
    """Something in the exchange did not hold up. The message is for the log
    and for an administrator -- never for the person signing in, who gets one
    sentence and no detail."""


# --- the settings -------------------------------------------------------------

def enabled() -> bool:
    return bool(config.AUTH_OIDC and config.OIDC_ISSUER and config.OIDC_CLIENT_ID)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _http():
    import httpx
    return httpx.Client(timeout=15, follow_redirects=False)


def discovery() -> dict:
    """The provider's own description of itself.

    ⚠ Fetched from the issuer, not typed in: the endpoints have to be the ones
      the issuer names, or `iss` checking below is checking against something
      somebody typed. The three manual overrides exist for a provider without
      discovery, and they are an exception, not the road.
    """
    issuer = config.OIDC_ISSUER
    if not issuer:
        raise OidcError("no issuer is configured")
    hit = _cache.get("disc")
    if hit and hit["issuer"] == issuer and hit["at"] > time.time() - _CACHE_SECONDS:
        return hit["doc"]
    url = issuer + "/.well-known/openid-configuration"
    try:
        with _http() as c:
            r = c.get(url)
            r.raise_for_status()
            doc = r.json()
    except Exception as exc:                                     # noqa: BLE001
        raise OidcError(f"the provider did not answer at {url}: {exc}")
    # ⚠ The document has to say it belongs to the issuer we asked. A redirect
    #   to somebody else's well-known would otherwise hand us their endpoints.
    if str(doc.get("issuer", "")).rstrip("/") != issuer:
        raise OidcError(
            f"the provider at {url} calls itself {doc.get('issuer')!r}, not {issuer!r}")
    _cache["disc"] = {"issuer": issuer, "doc": doc, "at": time.time()}
    return doc


def endpoint(name: str) -> str:
    """`authorization`, `token`, `userinfo`, `jwks_uri`, `end_session`."""
    manual = config.OIDC_ENDPOINT_OVERRIDES.get(name, "")
    if manual:
        return manual
    doc = discovery()
    key = {"authorization": "authorization_endpoint", "token": "token_endpoint",
           "userinfo": "userinfo_endpoint", "jwks": "jwks_uri",
           "end_session": "end_session_endpoint"}[name]
    return str(doc.get(key) or "")


def redirect_uri() -> str:
    """Where the provider sends the browser back.

    ⚠ Built from FAMILY_SITE_URL and not from the request. A redirect_uri taken
      from the Host header is a redirect_uri an attacker can choose, and it is
      registered with the provider anyway -- so if it does not match what the
      site really is, the mistake should be loud, not silent.
    """
    return config.OIDC_REDIRECT_URI or (config.SITE_URL.rstrip("/") + "/auth/oidc/callback")


# --- one sign-in in flight ----------------------------------------------------

# ⚠ The cookie that ties a callback to the browser that started it. Short-lived,
#   HttpOnly, and confined to the callback path.
STATE_COOKIE = "roamlight_oidc_state"


def begin(next_url: str = "/"):
    """Start a sign-in. Returns `(url, state)`.

    ⚠ The caller MUST put `state` in a cookie and check it again in the
      callback. `state` on its own is server-side and one-use, which proves that
      SOME browser started this exchange -- not that THIS one did, and that is a
      different sentence. Without the cookie:

        an attacker starts a sign-in, authenticates as themselves at the
        provider, stops before the last redirect, and sends the victim the
        finished callback URL. The victim's browser presents a state that is
        genuine and unused, everything verifies, and the victim ends up signed
        in as the ATTACKER -- uploading into their account, and with their own
        session cookie overwritten.

      The docstring at the top of this file used to claim `state` prevented
      that. It did not; both reviews of 08.09.2026 said so, independently.
    """
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = _b64url(secrets.token_bytes(24))
    nonce = _b64url(secrets.token_bytes(24))
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"
    with db.tx() as c:
        c.execute("DELETE FROM oidc_pending WHERE created_at <= datetime('now', ?)",
                  (f"-{PENDING_MINUTES} minutes",))
        c.execute("INSERT INTO oidc_pending (state, verifier, nonce, next) VALUES (?,?,?,?)",
                  (state, verifier, nonce, next_url))
    from urllib.parse import urlencode
    scopes = config.OIDC_SCOPES or "openid email profile"
    url = endpoint("authorization")
    if not url:
        raise OidcError("the provider names no authorization endpoint")
    return url + ("&" if "?" in url else "?") + urlencode({
        "response_type": "code",
        "client_id": config.OIDC_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "scope": scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }), state


def _take(state: str):
    """The pending sign-in for this state -- once.

    ⚠ Deleted in the same transaction that reads it. Two callbacks with the same
      state must not both succeed, and "read, then delete" is a race that says
      yes twice.
    """
    if not state:
        return None
    with db.tx() as c:
        row = c.execute(
            "DELETE FROM oidc_pending WHERE state=? AND "
            "created_at > datetime('now', ?) RETURNING verifier, nonce, next",
            (state, f"-{PENDING_MINUTES} minutes")).fetchone()
    return row


# --- the provider's keys ------------------------------------------------------

def _jwks(kid: str, force: bool = False) -> dict:
    """The signing key with this id.

    ⚠ Refetched once when the id is unknown: providers rotate, and a cached key
      set is the reason a rotation looks like "everybody is locked out". Once,
      not on every miss -- otherwise a made-up `kid` is a way to make this site
      hammer the provider.
    """
    # ⚠ Keyed by the URL. It was one slot for everything, so after an issuer was
    #   changed the OLD provider's keys stayed in use for up to an hour. That
    #   fails closed (tokens from the new provider are refused), but it looks
    #   like the new provider is broken.
    url = endpoint("jwks")
    hit = _cache.get("jwks")
    if hit and hit.get("url") != url:
        hit = None
    if force or not hit or hit["at"] <= time.time() - _CACHE_SECONDS:
        if not url:
            raise OidcError("the provider names no JWKS endpoint")
        try:
            with _http() as c:
                r = c.get(url)
                r.raise_for_status()
                hit = {"doc": r.json(), "at": time.time(), "url": url}
        except Exception as exc:                                 # noqa: BLE001
            raise OidcError(f"could not read the provider's keys: {exc}")
        _cache["jwks"] = hit
    for k in hit["doc"].get("keys", []):
        if not kid or k.get("kid") == kid:
            return k
    if not force:
        return _jwks(kid, force=True)
    raise OidcError(f"the provider has no signing key {kid!r}")


def _public_key(jwk: dict):
    """A verifying key out of a JWK. RSA and EC, which is every provider."""
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    kty = jwk.get("kty")
    if kty == "RSA":
        n = int.from_bytes(_unb64url(jwk["n"]), "big")
        e = int.from_bytes(_unb64url(jwk["e"]), "big")
        return rsa.RSAPublicNumbers(e, n).public_key()
    if kty == "EC":
        curve = {"P-256": ec.SECP256R1(), "P-384": ec.SECP384R1(),
                 "P-521": ec.SECP521R1()}.get(jwk.get("crv"))
        if curve is None:
            raise OidcError(f"unsupported curve {jwk.get('crv')!r}")
        x = int.from_bytes(_unb64url(jwk["x"]), "big")
        y = int.from_bytes(_unb64url(jwk["y"]), "big")
        return ec.EllipticCurvePublicNumbers(x, y, curve).public_key()
    raise OidcError(f"unsupported key type {kty!r}")


def verify_id_token(token: str, nonce: str) -> dict:
    """Check the token, then -- and only then -- return its claims.

    ⚠ Everything here happens BEFORE the caller sees a claim. The order is the
      point: an `id_token` is a string a stranger sent, and it parses into
      perfectly ordinary JSON whether or not it is genuine.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding

    try:
        h_b64, p_b64, s_b64 = token.split(".")
        head = json.loads(_unb64url(h_b64))
        claims = json.loads(_unb64url(p_b64))
        sig = _unb64url(s_b64)
    except Exception:                                            # noqa: BLE001
        raise OidcError("the id_token is not a token")

    alg = head.get("alg", "")
    # ⚠ `none` and the HMAC family are refused outright. "alg: none" is the
    #   oldest JWT hole there is, and an HS256 token would be verified with the
    #   PUBLIC key as its secret -- which anybody can read.
    if alg not in ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512"):
        raise OidcError(f"refusing signature algorithm {alg!r}")

    key = _public_key(_jwks(head.get("kid", "")))
    digest = {"256": hashes.SHA256(), "384": hashes.SHA384(), "512": hashes.SHA512()}[alg[2:]]
    signed = (h_b64 + "." + p_b64).encode()
    try:
        if alg.startswith("RS"):
            key.verify(sig, signed, padding.PKCS1v15(), digest)
        else:
            # ⚠ A JWS ECDSA signature is r||s, not the DER the library expects.
            from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
            half = len(sig) // 2
            der = encode_dss_signature(int.from_bytes(sig[:half], "big"),
                                       int.from_bytes(sig[half:], "big"))
            key.verify(der, signed, ec.ECDSA(digest))
    except InvalidSignature:
        raise OidcError("the id_token's signature does not hold")
    except Exception as exc:                                     # noqa: BLE001
        raise OidcError(f"the id_token could not be checked: {exc}")

    issuer = config.OIDC_ISSUER
    if str(claims.get("iss", "")).rstrip("/") != issuer:
        raise OidcError(f"the token says it comes from {claims.get('iss')!r}, not {issuer!r}")
    aud = claims.get("aud")
    aud = aud if isinstance(aud, list) else [aud]
    if config.OIDC_CLIENT_ID not in aud:
        raise OidcError("the token was not issued for this site")
    now = time.time()
    # ⚠ `float()` on a claim the provider chose. `"exp": "soon"` or `"exp": null`
    #   raised ValueError/TypeError straight out of here, and the callback only
    #   catches OidcError -- so a malformed token was a 500 instead of a no.
    #   It failed closed, but a stack trace is not an answer.
    try:
        exp = float(claims.get("exp", 0))
        iat = float(claims.get("iat", now))
    except (TypeError, ValueError):
        raise OidcError("the token's exp/iat are not numbers")
    if exp < now - 60:
        raise OidcError("the token has run out")
    if iat > now + 300:
        raise OidcError("the token is dated in the future")
    # ⚠ The nonce is what ties this token to THIS sign-in. Without it a token
    #   the provider minted for another session can be pushed into this one.
    if claims.get("nonce") != nonce:
        raise OidcError("the token belongs to a different sign-in")
    return claims


# --- the exchange -------------------------------------------------------------

def finish(code: str, state: str) -> tuple:
    """Trade the code for a token, check it, and say who this is.

    Returns `(claims, next_url)`.
    """
    row = _take(state)
    if row is None:
        raise OidcError("this sign-in has already been used, or it took too long")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "client_id": config.OIDC_CLIENT_ID,
        "code_verifier": row["verifier"],
    }
    secret = config.oidc_secret()
    if secret:
        data["client_secret"] = secret
    url = endpoint("token")
    if not url:
        raise OidcError("the provider names no token endpoint")
    try:
        with _http() as c:
            r = c.post(url, data=data,
                       headers={"Accept": "application/json"})
    except Exception as exc:                                     # noqa: BLE001
        raise OidcError(f"the provider did not answer: {exc}")
    if r.status_code != 200:
        # ⚠ The provider's own words go to the LOG. What comes back to the
        #   person is one sentence -- a token endpoint's error text has been
        #   known to contain the client secret it was given.
        log.warning("oidc: token endpoint said %s: %s", r.status_code, r.text[:300])
        raise OidcError("the provider refused the exchange")
    tok = r.json()
    if not tok.get("id_token"):
        raise OidcError("the provider sent no id_token")
    claims = verify_id_token(tok["id_token"], row["nonce"])
    return claims, row["next"]


# --- who that is, here --------------------------------------------------------

def identity(claims: dict) -> dict:
    """The claims, turned into this site's idea of a person.

    ⚠ The groups are compared by NAME against the same lists the rest of the
      site uses (`FAMILY_ADMIN_GROUPS` and friends, or what the settings page
      set). That was deliberate: the permission logic does not learn a second
      vocabulary, and an installation moving off forward-auth keeps the group
      names it already had.
    """
    field = config.OIDC_USERNAME_CLAIM or "preferred_username"
    name = str(claims.get(field) or claims.get("email") or claims.get("sub") or "").strip()
    if not name:
        raise OidcError(f"the token carries no {field!r} to use as a name")
    gclaim = config.OIDC_GROUPS_CLAIM or "groups"
    raw = claims.get(gclaim) or []
    if isinstance(raw, str):
        raw = [g.strip() for g in raw.replace(",", " ").split() if g.strip()]
    groups = [str(g) for g in raw]
    # ⚠ EXACTLY as the provider spells it -- do NOT lower-case this.
    #
    #   It did, for about an hour on 08.09.2026, and every album permission on
    #   the live site stopped matching. The rows say `user:Guy`, `user:Cristina`
    #   -- the directory's spelling, which is what forward-auth passed through
    #   for months -- and `acl.principals_for()` compares them exactly. Lowering
    #   the name here did not lose a single row; it quietly made a SECOND person
    #   out of each one, with no albums.
    #
    #   Nothing here may decide how a person is spelled. The provider decides,
    #   and the whole site has to agree with it.
    return {
        "username": name,
        "email": str(claims.get("email") or ""),
        "display_name": str(claims.get("name") or claims.get("preferred_username") or name),
        "groups": groups,
    }


def sign_in(claims: dict) -> str:
    """Write the person down and give them a session. Returns the cookie value.

    ⚠ `is_local=0` and no password hash: this account cannot be signed in to
      with a password, and `has_local_users()` therefore does not count it. A
      site whose people all come from the provider still has no local account,
      which is what keeps `/setup` shut.
    """
    from . import auth
    who = identity(claims)
    known = (set(config.ADMIN_GROUPS) | set(config.VIEWER_GROUPS)
             | set(config.CONTRIBUTOR_GROUPS))
    if not known.intersection(who["groups"]):
        raise OidcError(
            f"{who['username']} is in {who['groups'] or 'no groups'}, and none of "
            f"those is one this site knows ({', '.join(sorted(known))})")
    # ⚠ NEVER onto an account that has a password. Both reviews of 08.09.2026
    #   found this, and it is the sharpest thing in the file:
    #
    #     The upsert keys on the bare user name, and the name comes from a claim
    #     the person can often edit themselves (Authentik's default flow lets a
    #     user change `username`; the fallback chain reaches `email`). So
    #     somebody renames themselves at the provider to the local emergency
    #     admin's name, signs in -- and gets a session as that account, with its
    #     albums and its uploads. Worse, the same statement overwrites that
    #     account's groups with theirs: the administrator is demoted,
    #     `has_local_admin()` turns false, and the way back in when the provider
    #     is down is gone, without anybody touching a setting.
    #
    #   It is not hypothetical: it happened on the live site during testing,
    #   with `guy`, and the groups had to come back out of a backup.
    #
    #   So the two kinds of account do not merge. A name that belongs to a
    #   password account is refused here, loudly, and the person keeps their own.
    row = db.connect().execute(
        "SELECT is_local, password_hash IS NOT NULL AS has_pw FROM members WHERE username=?",
        (who["username"],)).fetchone()
    if row is not None and (row["is_local"] or row["has_pw"]):
        log.warning("oidc: REFUSED -- %r is a password account here", who["username"])
        raise OidcError(
            f"the name {who['username']!r} already belongs to an account that signs in "
            "with a password on this site. The two are not the same person as far as "
            "this site is concerned, and merging them would hand over that account. "
            "Change the name at your provider, or ask an administrator.")
    with db.tx() as c:
        c.execute(
            "INSERT INTO members (username, display_name, email, active, groups_json, is_local) "
            "VALUES (?,?,?,1,?,0) "
            "ON CONFLICT(username) DO UPDATE SET "
            "  display_name=excluded.display_name, email=excluded.email, "
            "  groups_json=excluded.groups_json, active=1",
            (who["username"], who["display_name"], who["email"], json.dumps(who["groups"])))
    log.info("oidc: %s signed in, groups %s", who["username"], who["groups"])
    return auth.new_session(who["username"])
