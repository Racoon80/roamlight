"""Who is asking, and may they.

Every request in the site funnels through `identify()`, and identity can arrive
by three different roads:

  1. a **session cookie**, from someone who signed in with a password
  2. a **device token** (`Authorization: Bearer fam_…`), from the phone app
  3. **headers from an identity proxy** in front (Authentik, Authelia, …)

Whatever the road, the answer is the same `Identity`, so not one of the
permission checks in the rest of the site has to know which one it was.

⚠ The third road is the dangerous one, and it is why `FAMILY_AUTH` exists. A
  header can be sent by anybody. It only means something when a proxy in front
  overwrites it — and to prove that it really was that proxy, the site also
  demands a shared secret it holds in a file. No secret, no answer.
"""
import hmac
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse

from . import config

# Paths that go through without a signed-in user. The shared secret still
# applies to them — they are exceptions to identity, not to the lock.
#
# ⚠ `/static/` belongs here. A share page is shown to a guest who has no
#   account, and without this they got a 403 on the stylesheet and the script:
#   the page arrived with no layout and the upload silently did nothing. No
#   photograph lives there — only CSS, fonts and scripts.
_NO_USER_PREFIXES = ("/s/", "/static/")
# ⚠ `/api/app/authz` and `/api/app/pair` too: the first is the guard the proxy
#   calls BEFORE an identity exists, the second is where the app trades its
#   pairing code. Both still sit behind the shared secret.
# ⚠ And `/login`, `/logout`, `/setup`: somebody who is not signed in has to be
#   able to reach the sign-in page — a 403 there would be a dead end.
_NO_USER_EXACT = ("/api/health", "/robots.txt", "/api/app/authz", "/api/app/pair",
                  "/login", "/logout", "/setup")


@dataclass(frozen=True)
class Identity:
    user: str
    email: str
    groups: tuple
    is_admin: bool        # sees and manages everything
    is_contributor: bool  # may upload and manage THEIR OWN photographs
    is_viewer: bool       # may look (admins and contributors may too)
    local: bool           # the request came from the machine itself


def _no_access(request: Request):
    """Not signed in — or signed in with no rights.

    ⚠ A **page** in a browser is sent to the sign-in form; a 403 in a window is
    a dead end. A **request** (API, image) gets the 403: an app needs an error,
    not a sign-in page it will then try to read as JSON.
    """
    if config.AUTH_LOCAL and "text/html" in (request.headers.get("accept") or ""):
        from urllib.parse import quote
        from fastapi.responses import RedirectResponse
        target = request.url.path + (("?" + request.url.query) if request.url.query else "")
        return _harden(RedirectResponse(
            f"/login?next={quote(target, safe='/?=&')}", status_code=303))
    return _deny("role")


def _deny(reason: str) -> JSONResponse:
    # One word out, no more. Somebody who did not come through the front door
    # does not get free diagnostics.
    return _harden(JSONResponse({"error": "forbidden"}, status_code=403))


def _harden(response):
    """Put the security headers on a response.

    ⚠ They used to be set only on the way BACK -- so only on answers that had
      been through the app. A refusal from the gate (a 403, and the redirect to
      the sign-in page) had none: no `noindex`, no `nosniff`, no CSP, no
      `X-Frame-Options`. Those are exactly the answers a stranger gets.
    """
    for key, value in _HEADERS.items():
        response.headers.setdefault(key, value)
    return response


def client_ip(request: Request) -> str:
    """The address the client really came from.

    ⚠ The forwarding header is only read when the request arrives FROM a proxy
    named in `FAMILY_TRUSTED_PROXIES`. It is a header: anybody can type one.
    Read it from just anyone and the sign-in throttle stops working — a fresh
    invented address on every attempt and nothing ever counts to ten — and,
    behind an identity proxy, `X-Forwarded-For: 127.0.0.1` would make a
    stranger look like the machine itself.

    ⚠ With nothing configured this falls back to the TCP peer. Behind a proxy
    that is the proxy for everybody, so the throttle becomes one shared
    counter. That is a nuisance and it is the right way round: a throttle
    everyone shares still throttles, a throttle that can be stepped around
    does nothing.

    The example nginx configuration sets `X-Forwarded-For` to `$remote_addr` —
    only the hop it knows — and NOT to `$proxy_add_x_forwarded_for`, which
    would append whatever the client asked for. Behind Cloudflare, point
    `FAMILY_CLIENT_IP_HEADER` at `CF-Connecting-IP`: that is the header
    Cloudflare sets itself. What it does with `X-Forwarded-For` depends on the
    account's transform rules, and a throttle should not rest on a header whose
    handling is a setting somewhere else.
    """
    peer = request.client.host if request.client else ""
    if not config.trusts_proxy(peer):
        return peer
    fwd = (request.headers.get(config.CLIENT_IP_HEADER) or "").split(",")[0].strip()
    return fwd or peer


def identify(request: Request) -> Identity:
    """Who is this — by one of three roads.

    ⚠ The order is deliberate:
      1. **session** (cookie) — whoever just signed in is who they say.
      2. **device token** — the app. This road works in every mode, because it
         has nothing to do with any directory.
      3. **proxy headers** — only when `FAMILY_AUTH` says so. With no proxy
         overwriting them, any client can send them; that is why it is not the
         default.

    The answer is remembered for the request: `identify()` is called a dozen
    times while one page is built (gate, route, template), and roads 1 and 2
    each cost a query.
    """
    hit = getattr(request.state, "family_ident", None)
    if hit is not None:
        return hit
    ident = _identify(request)
    request.state.family_ident = ident
    return ident


def _from_member(username: str) -> Identity:
    """An identity out of the `members` table — for sessions and devices.

    ⚠ The groups come from the database, and they are the SAME names that would
    otherwise come from a directory. That is what lets every permission check
    in the site stay ignorant of how somebody signed in.
    """
    import json
    from . import db
    row = db.connect().execute(
        "SELECT username, display_name, email, groups_json FROM members "
        "WHERE username=? AND active=1", (username,)).fetchone()
    if row is None:
        return _empty()
    try:
        groups = tuple(str(g) for g in json.loads(row["groups_json"] or "[]"))
    except (ValueError, TypeError):
        groups = ()
    return _built(row["username"], row["email"] or "", groups, local=False)


def _empty() -> Identity:
    return Identity(user="", email="", groups=(), is_admin=False,
                    is_contributor=False, is_viewer=False, local=False)


def _built(user: str, email: str, groups: tuple, local: bool) -> Identity:
    return Identity(
        user=user, email=email, groups=groups,
        is_admin=bool(config.ADMIN_GROUPS.intersection(groups)),
        is_contributor=bool(config.ADMIN_GROUPS.intersection(groups)
                            or config.CONTRIBUTOR_GROUPS.intersection(groups)),
        is_viewer=bool(config.ADMIN_GROUPS.intersection(groups)
                       or config.VIEWER_GROUPS.intersection(groups)),
        local=local)


def _identify(request: Request) -> Identity:
    if config.AUTH_LOCAL:
        cookie = request.cookies.get(config.SESSION_COOKIE)
        if cookie:
            from . import auth
            user = auth.session_user(cookie)
            if user:
                auth.touch(cookie)
                return _from_member(user)

    head = request.headers.get("authorization", "")
    if head[:7].lower() == "bearer ":
        from . import devices
        user = devices.identify(head[7:].strip())
        if user:
            return _from_member(user)

    # ⚠ A signed ticket, and ONLY for the address it was signed for. This is
    #   how a video gets played: the operating system's player will not put a
    #   header on its requests, so the address carries the proof instead. See
    #   app/tickets.py -- the signature covers the path, so a ticket is
    #   worthless anywhere else, and it carries no rights of its own: the
    #   person it names is looked up and their ordinary permissions apply.
    ticket = request.query_params.get("t", "")
    if ticket:
        from . import tickets
        user = tickets.user_for(request.url.path, ticket)
        if user:
            return _from_member(user)

    if not config.AUTH_PROXY:
        return _empty()

    user = (request.headers.get(config.HDR_USER) or "").strip()
    raw = (request.headers.get(config.HDR_GROUPS) or "").strip()
    groups = tuple(g.strip() for g in raw.replace(",", "|").split("|") if g.strip())
    return Identity(
        user=user,
        email=(request.headers.get(config.HDR_EMAIL) or "").strip(),
        groups=groups,
        is_admin=bool(config.ADMIN_GROUPS.intersection(groups)),
        is_contributor=bool(config.ADMIN_GROUPS.intersection(groups)
                            or config.CONTRIBUTOR_GROUPS.intersection(groups)),
        is_viewer=bool(config.ADMIN_GROUPS.intersection(groups)
                       or config.VIEWER_GROUPS.intersection(groups)),
        # "local" means: the request came from the machine itself (monitoring,
        # an ssh tunnel) — not "came through the proxy".
        local=client_ip(request) in config.TRUSTED_PEERS,
    )


_HEADERS = {
    "X-Robots-Tag": "noindex, nofollow, noimageindex, noarchive",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "Content-Security-Policy": (
        # Map tiles normally run through this server (see app/tiles.py), which
        # is why everything else is 'self'. The OpenStreetMap entry is here for
        # installations that would rather let the browser fetch tiles directly;
        # drop it and nothing in a browser talks to anyone but you.
        "default-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "font-src 'self'; connect-src 'self'; object-src 'none'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
}


async def gate(request: Request, call_next):
    """Middleware. Runs before anything else."""
    path = request.url.path

    # ⚠ This lock belongs to the proxy road: there a program outside claims an
    #   identity, and the secret is the proof that it really was the proxy.
    #   With a local sign-in nobody claims anything — the proof is in the
    #   cookie, and there is no secret to check.
    if config.REQUIRE_AUTH and config.AUTH_PROXY:
        peer = request.client.host if request.client else ""
        if peer not in config.TRUSTED_PEERS:
            return _deny("peer")

        want = config.proxy_secret()
        got = request.headers.get(config.PROXY_HEADER, "")
        # compare_digest, because otherwise how long the comparison takes says
        # how many characters were right.
        # ⚠ .encode(): a header value that is not ASCII raises a TypeError in
        #   compare_digest — a 500 instead of a 403. Not reachable through the
        #   proxy (which overwrites the value), but a loopback call could do it.
        if not want or not hmac.compare_digest(
                got.encode("latin-1", "replace"), want.encode("latin-1", "replace")):
            return _deny("proxy")

    if config.REQUIRE_AUTH:
        if not (path in _NO_USER_EXACT or path.startswith(_NO_USER_PREFIXES)):
            # ⚠ The roles are worked out from configuration and asked here. An
            #   earlier version trusted whatever the proxy said about groups
            #   and nothing else — which made "who may look" depend entirely on
            #   a machine outside this one.
            ident = identify(request)
            if not ident.is_viewer:
                return _no_access(request)
            # Who was here gets noted — no token, no network request. That way
            # the administrator sees a list of people even when the directory
            # is unreachable. Errors are swallowed on purpose: a counter must
            # never take a page down.
            try:
                from . import members
                members.note_seen(ident.user, ident.email, ident.groups)
            except Exception:                                    # noqa: BLE001
                pass

    # While share links are switched off, the path does not exist at all.
    if path.startswith("/s/") and not config.SHARES_ENABLED:
        return _harden(JSONResponse({"error": "not found"}, status_code=404))

    response = await call_next(request)
    # ⚠ These headers are also set by the example nginx configuration, and
    #   setting them twice is deliberate: nginx `add_header` does NOT inherit
    #   into a location that has one of its own — and the share page, the tiles
    #   and the images all have one. So the one page a stranger gets to see was
    #   exactly the page with no content policy and no X-Frame-Options. The app
    #   sets them itself, on every answer.
    #   `setdefault`: whatever the proxy already set stays as it is.
    for key, value in _HEADERS.items():
        response.headers.setdefault(key, value)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response
