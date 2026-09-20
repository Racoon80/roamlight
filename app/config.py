"""Every setting, in one place.

All of it comes from environment variables, so the same code runs from a
checkout on a laptop and from /opt in a container without a line changing.
Each setting says what it is for; the ones with a ⚠ say what went wrong when
they were not there.
"""
import os
import sys
from pathlib import Path


def _path(env: str, default) -> Path:
    return Path(os.environ.get(env, str(default))).expanduser()


def _bool(env: str, default: bool) -> bool:
    return os.environ.get(env, str(default)).strip().lower() in ("1", "true", "yes", "on")


# --- where things live -------------------------------------------------------
BASE_DIR = _path("FAMILY_BASE", Path(__file__).resolve().parent.parent)
DATA_DIR = _path("FAMILY_DATA", BASE_DIR / "data")
INCOMING_DIR = _path("FAMILY_INCOMING", DATA_DIR / "incoming")
DB_PATH = _path("FAMILY_DB", DATA_DIR / "roamlight.db")

# The two trees:
#   ORIGIN_DIR  your photographs — only ever added to, never rewritten
#   WEB_DIR     what the site serves — this one it owns
ORIGIN_DIR = _path("FAMILY_ORIGINS", "/originals")
WEB_DIR = _path("FAMILY_WEB", "/library")

# The web sizes are computed locally, outside the web tree, from the master --
# so no RAW file is ever decoded twice.
DERIVATIVE_DIR = _path("FAMILY_DERIVATIVES", DATA_DIR / "derivatives")

# ⚠ Scratch space for a conversion in progress -- its OWN folder, not DATA_DIR
#   itself. A conversion that is killed halfway (a restart, an OOM) leaves its
#   temporary folder behind; `sweep_work()` clears those out at startup, and it
#   may only do that where nothing else lives.
WORK_DIR = _path("FAMILY_WORK", DATA_DIR / "work")

TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# ⚠ A share that failed to mount looks exactly like an empty folder. The site
#   would happily write to the local disk, and the next sync would conclude
#   that every photograph had been deleted. So both trees have to be a real
#   mount point AND carry a marker file.
REQUIRE_MOUNT = _bool("FAMILY_REQUIRE_MOUNT", True)
MARKER_NAME = os.environ.get("FAMILY_MARKER_NAME", ".roamlight-marker")

# --- who may in ---------------------------------------------------------------
# A list, because the two go together:
#
#   local   accounts with a password, kept in this site's database. For anyone
#           who does not run single sign-on at home — which is nearly everyone.
#   proxy   an identity provider in front (Authentik, Authelia, oauth2-proxy)
#           authenticates and passes the user and groups as headers.
#
# ⚠ Only switch `proxy` on when a proxy really is in front and really does
#   OVERWRITE those headers. Behind an ordinary reverse proxy, any client can
#   send `X-authentik-username: you` and be you. The shared secret below is
#   what makes the headers trustworthy — and it is why `local` is the default.
#
# ⚠ This one setting is NOT read-only, unlike every other one in this file. An
#   administrator can switch the proxy road on from the settings page, because
#   asking somebody to edit a YAML file and restart a container in order to try
#   single sign-on is asking them not to try it. What the page may do is
#   NARROW, on purpose:
#
#     * it may add `proxy` and it may take `proxy` away again,
#     * it may NEVER take `local` away. That road is what you still have when
#       the proxy is misconfigured, and a click must not be able to shut the
#       last door. Removing it is `FAMILY_AUTH=proxy` in the YAML and nothing
#       else,
#     * and `FAMILY_AUTH_LOCK=1` turns the page's switch off altogether, for an
#       installation where the configuration is the only authority.
ENV_AUTH_MODES = set(m.strip().lower() for m in
                     os.environ.get("FAMILY_AUTH", "local").replace(",", "+").split("+")
                     if m.strip())
# When this is on, whatever stands in the database is ignored — the YAML wins.
AUTH_LOCKED = _bool("FAMILY_AUTH_LOCK", False)

# The database answer, remembered. `auth_oidc()` is asked several times per
# request (the gate, then `identify()` for every image on a gallery page), and
# that must not be a query each time. Written only through `set_auth_oidc()`,
# which drops it -- and the site is one uvicorn process, so there is no second
# cache to keep in step.
_auth_cache: set | None = None
_auth_lock = __import__("threading").Lock()


_ASK_AGAIN = object()     # the database could not be read -- do NOT remember


def _db_oidc():
    """Has the settings page switched the proxy road on or off?

    `None` = never touched, so the environment decides.
    `_ASK_AGAIN` = the database could not be read at all.

    ⚠ Imported inside the function: `db` imports this module, so a plain import
    at the top is a circle. It also keeps the CLI and the tests able to read
    the configuration before there is a database at all.

    ⚠ The two "no answer" cases are NOT the same, and treating them as one was
      a bug worth a comment. A read that fails -- the database busy, the disk
      briefly gone -- used to come back as "never touched", and the answer was
      then cached for the whole life of the process. One unlucky moment on the
      first request after a restart, and a site whose administrator had
      switched single sign-on off would run with it on until somebody noticed
      and restarted it again.
    """
    try:
        from . import db
        raw = db.get_state("auth_oidc")
    except Exception:                                            # noqa: BLE001
        # No database yet (first start, a CLI run before `db.init()`) is one of
        # these too. Never a crash -- this is asked on the way into every
        # request -- but never remembered either.
        return _ASK_AGAIN
    if raw in ("1", "0"):
        return raw == "1"
    return None


def auth_modes() -> set:
    """The roads in.

    ⚠ Read this and the invariant is not a rule somebody has to remember, it is
      the shape of the function: `local` comes out of ENV_AUTH_MODES and out of
      nothing else. The database is asked about ONE thing, `proxy`.

      It was a mode list at first -- the page stored "local+proxy" or "local",
      and the same code path could therefore write `local` into a site whose
      configuration says `proxy`. On the live site that meant one click turning
      single sign-on "off", finding no password account anywhere (there are
      none -- the people come from the directory), and opening /setup: whoever
      reached the port next would have become the administrator of the family's
      photographs. A rule that can be got round by a caller is not a rule.
    """
    global _auth_cache
    if AUTH_LOCKED:
        return ENV_AUTH_MODES
    # ⚠ Under the lock, and asked again inside it. Without that this is a
    #   check-then-set: two requests both find the cache empty, both read the
    #   database, and an administrator's click can land between one thread's
    #   read and its write -- after which the thread stores the value from
    #   BEFORE the click and the site goes on believing it until the next write
    #   or a restart. The window is one SQLite read, which is small and is not
    #   zero.
    with _auth_lock:
        if _auth_cache is not None:
            return _auth_cache
        modes = set(ENV_AUTH_MODES)
        stored = _db_oidc()
        if stored is _ASK_AGAIN:
            # Answer from the environment for THIS request and remember
            # nothing -- the next request asks again.
            return modes or set(ENV_AUTH_MODES)
        if stored is True:
            modes.add("oidc")
        elif stored is False:
            modes.discard("oidc")
        # ⚠ A stored "off" must never empty the set. Someone runs `local+proxy`,
        #   switches the proxy off on the page (stored: off), and LATER edits
        #   the configuration to `FAMILY_AUTH=proxy` -- and the old click, made
        #   about a different installation, would leave a site with no road in
        #   at all. The configuration is the authority; the database only ever
        #   refines it, and it does not get to refine it to nothing.
        if not modes:
            modes = set(ENV_AUTH_MODES)
        _auth_cache = modes
    return _auth_cache


def forget_auth_modes() -> None:
    """Drop the cache. Called after a write, and by the tests."""
    global _auth_cache
    with _auth_lock:
        _auth_cache = None


def set_auth_oidc(on: bool) -> set:
    """Switch signing in through the identity provider on or off.

    ⚠ `local` is not this function's business. It comes from ENV_AUTH_MODES and
      from nothing else -- see `auth_modes()` -- so no number of clicks here can
      shut the password road. That invariant is why a wrong provider setting is
      a nuisance and not a lockout.

    ⚠ There is no arm-and-confirm, and there used to be. That dance existed to
      prove a shared secret really travelled from the reverse proxy to the app
      before the setting took effect. The app talks to the provider itself now:
      there is no secret to prove by round trip.
    """
    if AUTH_LOCKED:
        raise ValueError("FAMILY_AUTH_LOCK is on -- the configuration decides this")
    if on:
        if not (setting("oidc_issuer") and setting("oidc_client_id")):
            raise ValueError(
                "the provider needs an issuer and a client id before this can be "
                "switched on -- otherwise the button leads nowhere")
    else:
        # ⚠ Off, where the configuration gives no password road either, leaves
        #   nobody able to sign in at all.
        if "local" not in ENV_AUTH_MODES:
            raise ValueError(
                "single sign-on is the only way in on this installation "
                "(FAMILY_AUTH=oidc). Put FAMILY_AUTH=local+oidc in the "
                "configuration and make an account first: "
                "roamlight-user add <name> --admin")
        from . import auth
        if not auth.has_local_admin():
            raise ValueError(
                "there is no administrator with a password on this site -- "
                "everybody here signs in through the provider. Switching it off "
                "would leave nobody able to get in. Make one first: "
                "roamlight-user add <name> --admin")
    from . import db
    db.set_state("auth_oidc", "1" if on else "0")
    forget_auth_modes()
    return auth_modes()


def setup_is_open(token_given: str = "") -> bool:
    """May the first-run page (`/setup`) answer this request?

    The caller has already established that there is no local account yet.

    ⚠ It lives here, next to the other rules about who may in, and not in the
      route -- because it IS one of those rules, and because a rule in a route
      that needs pyvips to import is a rule no test ever reaches. This one is
      the second lock on the worst thing in this file, so it gets a test.

    ⚠ With an identity proxy configured, the answer is always no. There, "no
      local account yet" is not a fresh installation waiting for its owner --
      it is the normal and permanent state of a site whose people all come from
      the directory. Leaving the first-run form reachable in that state means
      everyone the proxy lets through, and anyone who can reach the port, is
      one form away from owning the library. The way in is the terminal:
      `roamlight-user add <name> --admin`.
    """
    if auth_oidc():
        return False
    if not SETUP_TOKEN:
        return True
    import hmac as _hmac
    return _hmac.compare_digest(token_given or "", SETUP_TOKEN)


def auth_local() -> bool:
    return "local" in auth_modes()


def auth_oidc() -> bool:
    """Does the site sign people in through an identity provider itself?

    ⚠ This replaced `proxy` (forward-auth) on 08.09.2026. The difference is
      where the conversation with the provider happens: `proxy` had nginx do it
      and the app believe a header; here the app is an OpenID Connect client and
      nobody has to be believed.
    """
    return "oidc" in auth_modes()


# --- the settings the page may change ---------------------------------------
#
# ⚠ Everything else in this file is read once from the environment and never
#   moves. These do move, because asking somebody to edit a YAML file and
#   restart a container in order to point the site at their identity provider
#   is asking them not to have one.
#
#   The environment is still the DEFAULT for every one of them: an installation
#   that sets them in `family.env` or `compose.yaml` and never opens the page
#   behaves exactly as before.
#
# ⚠ SECRETS DO NOT GO IN THE DATABASE. `deploy/db-backup.sh` makes its copy with
#   `VACUUM INTO`, so anything in there is in fourteen rotating backups in the
#   clear -- and the two secrets here (the shared proxy secret, the directory's
#   API token) are exactly what somebody would want out of a stolen backup: one
#   forges identity headers, the other reads the directory. They are written to
#   files under FAMILY_DATA at 0600 instead. Losing them costs a re-issue;
#   leaking them costs the house.
def _env(name: str, fallback: str = "") -> str:
    """An environment variable, as the DEFAULT for a changeable setting.

    ⚠ Read at the moment it is asked and not at import, so a test can set one
      and the setting follows -- and so that `forget_settings()` really does
      forget everything.
    """
    return (os.environ.get(name, "") or "").strip() or fallback


_SETTINGS = {
    # --- the identity provider (OpenID Connect) ---------------------------
    # ⚠ Two ways in, and the order is the point: what the settings page stored
    #   wins, and underneath it stands the ENVIRONMENT. An installation that is
    #   configured from a file (Docker, a systemd unit, a repository somebody
    #   deploys from) sets FAMILY_OIDC_* and never opens the page; one that is
    #   set up by hand points itself at a Keycloak or an Authentik from the
    #   page and sets nothing. Neither has to know about the other.
    #
    #   These used to be `lambda: ""` -- page or nothing -- which meant a site
    #   deployed from a configuration file had no way to say who its provider
    #   was without somebody clicking.
    "oidc_provider":          lambda: "",      # which preset the page picked
    "oidc_issuer":            lambda: _env("FAMILY_OIDC_ISSUER"),
    "oidc_client_id":         lambda: _env("FAMILY_OIDC_CLIENT_ID"),
    "oidc_redirect_uri":      lambda: _env("FAMILY_OIDC_REDIRECT_URI"),
    "oidc_scopes":            lambda: _env("FAMILY_OIDC_SCOPES", "openid email profile"),
    "oidc_username_claim":    lambda: _env("FAMILY_OIDC_USERNAME_CLAIM",
                                           "preferred_username"),
    "oidc_groups_claim":      lambda: _env("FAMILY_OIDC_GROUPS_CLAIM", "groups"),
    # Only for a provider without discovery. The road is the issuer.
    "oidc_authorization_url": lambda: _env("FAMILY_OIDC_AUTHORIZATION_URL"),
    "oidc_token_url":         lambda: _env("FAMILY_OIDC_TOKEN_URL"),
    "oidc_userinfo_url":      lambda: _env("FAMILY_OIDC_USERINFO_URL"),
    "oidc_jwks_url":          lambda: _env("FAMILY_OIDC_JWKS_URL"),
    "oidc_end_session_url":   lambda: _env("FAMILY_OIDC_END_SESSION_URL"),
    "authentik_url":     lambda: _ENV_AUTHENTIK_URL,
    "sso_start":         lambda: sso_path(SSO_START_DEFAULT),
    "sso_logout":        lambda: sso_path(SSO_LOGOUT_DEFAULT, "/outpost.goauthentik.io/sign_out"),
    "trusted_peers":     lambda: ",".join(sorted(_ENV_TRUSTED_PEERS)),
    "admin_groups":      lambda: ",".join(sorted(_ENV_ADMIN_GROUPS)),
    "viewer_groups":     lambda: ",".join(sorted(_ENV_VIEWER_GROUPS)),
    "contributor_groups": lambda: ",".join(sorted(_ENV_CONTRIBUTOR_GROUPS)),
}


def setting(key: str) -> str:
    """One changeable setting: what the page stored, else what the YAML says."""
    if key not in _SETTINGS:
        raise KeyError(key)
    try:
        from . import db
        stored = db.get_state("cfg." + key)
    except Exception:                                            # noqa: BLE001
        stored = None
    if stored is not None:
        return stored
    return _SETTINGS[key]() or ""


def set_setting(key: str, value: str) -> str:
    if key not in _SETTINGS:
        raise KeyError(key)
    value = (value or "").strip()
    if key in ("sso_start", "sso_logout"):
        value = sso_path(value)
    if key == "oidc_issuer" or (key.startswith("oidc_") and key.endswith("_url")):
        value = provider_url(key, value)
    if key.endswith("_groups") or key == "trusted_peers":
        value = ",".join(v.strip() for v in value.replace(" ", ",").split(",") if v.strip())
    if key.endswith("_groups") and not value:
        raise ValueError("a group name is needed -- an empty list locks everybody out")
    from . import db
    db.set_state("cfg." + key, value)
    forget_settings()
    return value


def provider_url(key: str, value: str) -> str:
    """An address the site may be pointed at, or a refusal.

    ⚠ These are typed by an administrator, and the site then TALKS to them --
      the token endpoint is where the client secret goes. Without a check that
      is a small server-side request forgery with a credential attached: point
      `oidc_token_url` at a machine you own, press sign in, read the secret off
      your own log. Admin-only, so not the end of the world -- but the settings
      page promises the secret is never given out, and this was the one way it
      could be.

    ⚠ `?` and `#` are refused in the ISSUER specifically, because discovery
      appends `/.well-known/openid-configuration` to it: an issuer carrying a
      query turns that into a path of the attacker's choosing.
    """
    value = (value or "").strip()
    if not value:
        return ""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(value)
    except ValueError:
        raise ValueError(f"{key}: that is not an address")
    if u.scheme not in ("https", "http"):
        raise ValueError(f"{key}: must start with https://")
    if u.scheme == "http" and (u.hostname or "") not in ("localhost", "127.0.0.1", "::1"):
        # ⚠ Over http the id_token and the client secret cross the network in
        #   the clear. Allowed only against this machine, for a test rig.
        raise ValueError(
            f"{key}: http is only allowed to localhost -- the client secret and "
            "the token would go over the network in the clear")
    if not u.hostname:
        raise ValueError(f"{key}: no host in that address")
    if key == "oidc_issuer" and (u.query or u.fragment):
        raise ValueError(
            "the issuer may not carry a query or a fragment: discovery appends "
            "/.well-known/openid-configuration to it, and that would let the "
            "path be chosen elsewhere")
    return value


def _names(key: str) -> set:
    return set(n for n in setting(key).split(",") if n)


def admin_groups() -> set:
    return _names("admin_groups")


def viewer_groups() -> set:
    return _names("viewer_groups")


def contributor_groups() -> set:
    return _names("contributor_groups")


def trusted_peers() -> set:
    return _names("trusted_peers")


def authentik_url() -> str:
    return setting("authentik_url")


# --- the two secrets, in files ------------------------------------------------
def _secret_file(name: str, env_path):
    """Where a secret lives: what the configuration says, else ours under DATA.

    ⚠ The configured path wins, so an installation that already keeps the
      secret at /etc/family/proxy-secret (root-owned, 0640) keeps doing that and
      the page only READS it. Ours is used when there is none -- a Docker or LXC
      install where nobody hand-wrote a file -- and there the page can write it.
    """
    return Path(env_path) if env_path else (DATA_DIR / name)


def secret_path(which: str):
    if which == "proxy":
        return _secret_file("proxy-secret", _ENV_PROXY_SECRET_FILE)
    if which == "authentik":
        return _secret_file("authentik-token", _ENV_AUTHENTIK_TOKEN_FILE)
    if which == "share-cookie":
        # ⚠ Its OWN key, not the OIDC one and not the old proxy secret. A key
        #   that signs share cookies has nothing to do with signing in, and
        #   deriving one from the other is how the share cookies quietly lost
        #   their key when forward-auth was removed.
        return _secret_file("share-cookie-key", os.environ.get("FAMILY_SHARE_KEY_FILE"))
    if which == "oidc":
        # ⚠ The OIDC client secret. In a file for the same reason as the other
        #   two: `deploy/db-backup.sh` copies the database with VACUUM INTO, and
        #   a client secret in fourteen backups is a client secret somebody else
        #   has. Never in the database, never sent back to the page.
        return _secret_file("oidc-client-secret", os.environ.get("FAMILY_OIDC_SECRET_FILE"))
    raise KeyError(which)


def read_secret(which: str) -> str:
    """One of the secrets, or "" if there is none. Never logged, never returned
    to a browser."""
    try:
        return secret_path(which).read_text().strip()
    except OSError:
        return ""


def secret_is_writable(which: str) -> bool:
    """May the page set this one, or does it belong to root?"""
    p = secret_path(which)
    try:
        return os.access(p if p.exists() else p.parent, os.W_OK)
    except OSError:
        return False


def write_secret(which: str, value: str) -> None:
    p = secret_path(which)
    if not secret_is_writable(which):
        raise ValueError(
            f"{p} belongs to somebody else -- it is not the site's to write. "
            "Put it there by hand, or point FAMILY_PROXY_SECRET_FILE / "
            "FAMILY_AUTHENTIK_TOKEN_FILE at a place the site owns.")
    value = (value or "").strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    # ⚠ The mode on `os.open` applies ONLY when the file is created. Writing a
    #   new secret into a file that already existed at 0640 left it at 0640 --
    #   found by the test, which is what the test is for. So `fchmod` on the
    #   open descriptor, before a byte is written: it acts on THIS file, not on
    #   a path somebody could have swapped in the meantime, and there is no
    #   moment where the new secret is on disk under the old mode.
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        # Not ours to chmod. `secret_is_writable()` said we could write, so this
        # is a file we may append to but not own -- leave the mode alone rather
        # than fail the write.
        pass
    with os.fdopen(fd, "w") as f:
        f.write(value + "\n")


def secret_is_readable_by_all(which: str = "oidc") -> bool:
    """Can any account on this machine read the shared secret?

    ⚠ `proxmox-lxc.sh` does not create the file -- somebody who wants single
      sign-on there writes it by hand, and a hand-written file gets the shell's
      umask, which is usually 022. That container listens on 0.0.0.0:8080 and
      trusts 127.0.0.1, so a world-readable secret means any local account on
      that box can send `X-authentik-groups: admin` from loopback and be an
      administrator. A secret everybody can read is not a secret.
    """
    try:
        return bool(secret_path(which).stat().st_mode & 0o004)
    except OSError:
        return False


SETUP_TOKEN = os.environ.get("FAMILY_SETUP_TOKEN", "").strip()

SESSION_DAYS = int(os.environ.get("FAMILY_SESSION_DAYS", "30"))
SESSION_COOKIE = "family_session"
# For a TLS proxy that does not set `X-Forwarded-Proto`: put this at 1, and the
# session cookie is marked Secure regardless.
COOKIE_SECURE = _bool("FAMILY_COOKIE_SECURE", False)

REQUIRE_AUTH = _bool("FAMILY_REQUIRE_AUTH", True)
_ENV_PROXY_SECRET_FILE = _path("FAMILY_PROXY_SECRET_FILE", "/etc/roamlight/proxy-secret")
PROXY_HEADER = "x-family-proxy"
# ⚠ Only these peers may send anything at all. The proxy runs on the same host,
#   so that is 127.0.0.1 — anything else means somebody went around it.
_ENV_TRUSTED_PEERS = set(
    p.strip() for p in os.environ.get("FAMILY_TRUSTED_PEERS", "127.0.0.1,::1").split(",") if p.strip()
)


# Where the "sign in with single sign-on" button goes, and where signing out
# ends the provider's session. Both are paths on this same site that the proxy
# intercepts -- Authentik's outpost is the default because it is the one this
# was built against.
#
# ⚠ Stored the same way as the modes above: the environment is the default and
#   the settings page may overwrite it, because a person switching the proxy
#   road on has to be able to say where their provider lives without editing a
#   file. Only a path on this site is accepted (`sso_path()`), never an address
#   somewhere else -- otherwise the settings page is a way to point everybody's
#   sign-in button at a stranger's copy of the login screen.
SSO_START_DEFAULT = os.environ.get("FAMILY_SSO_START", "").strip()
SSO_LOGOUT_DEFAULT = os.environ.get(
    "FAMILY_SSO_LOGOUT", "/outpost.goauthentik.io/sign_out").strip()


_SSO_OK = None


def sso_path(value: str, fallback: str = "") -> str:
    """A path on this site, or the fallback. `//host` and `http://…` are not.

    ⚠ A whitelist, not a list of things to reject. The first version was
      `startswith("/") and not startswith("//")` -- and TWO values walked
      straight through it:

          /\\evil.example      a browser normalises the backslash to a slash
          /<tab>/evil.example  tabs, newlines and CRs are STRIPPED before the
                               URL is parsed, so this is //evil.example too

      Either one turns "Sign in with single sign-on" on this site's own login
      page into a link to somebody else's login page. Autoescaping does not
      help: both are perfectly ordinary characters inside an href.

      So the rule is now what may appear, and everything else is the fallback.
      `urlsplit` on top of it, because a whitelist somebody widens by one
      character in a year's time should still not be able to grow a host.
    """
    import re
    from urllib.parse import urlsplit
    global _SSO_OK
    if _SSO_OK is None:
        # `?` and `&` are in there because a provider's start address carries a
        # query -- /outpost.goauthentik.io/start?rd=/ is the ordinary form. A
        # query cannot grow a host, so it costs nothing to allow.
        _SSO_OK = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*$")
    value = (value or "").strip()
    if not value or not _SSO_OK.match(value):
        return fallback
    try:
        parts = urlsplit(value)
    except ValueError:
        return fallback
    if parts.scheme or parts.netloc:
        return fallback
    return value


def sso_start() -> str:
    """The address of the sign-in button -- empty means no button."""
    if not auth_oidc():
        return ""
    try:
        from . import db
        stored = db.get_state("sso_start")
    except Exception:                                            # noqa: BLE001
        stored = None
    if stored is not None:
        return sso_path(stored)
    return sso_path(SSO_START_DEFAULT)


def sso_logout() -> str:
    try:
        from . import db
        stored = db.get_state("sso_logout")
    except Exception:                                            # noqa: BLE001
        stored = None
    if stored:
        return sso_path(stored, SSO_LOGOUT_DEFAULT)
    return sso_path(SSO_LOGOUT_DEFAULT, "/outpost.goauthentik.io/sign_out")


# ⚠ Who is allowed to tell this site who the client is.
#
#   `X-Forwarded-For` is a header, and a header is whatever the sender typed.
#   Believing it from anybody lets the per-address sign-in throttle be walked
#   straight past: a fresh made-up address on every try, and nothing ever
#   counts to ten. It also decides, behind a proxy, whether a request is
#   treated as coming from the machine itself.
#
#   So the header is only believed when the request arrives FROM one of the
#   addresses below, and the default is EMPTY. That means an installation with
#   nothing in front of it -- which is what `compose.yaml` gives you -- is safe
#   as it stands. The price is one shared throttle for everybody, and a shared
#   throttle is a nuisance; a throttle that can be stepped around is not a
#   throttle at all.
#
#   Behind nginx as in deploy/nginx.conf:      127.0.0.1
#   Behind another reverse proxy on the LAN:   its address, e.g. 192.168.1.5
#   Ranges are allowed: 192.168.1.0/24
# --- Bescheed soen ------------------------------------------------------------
#
# (D'Notiz zu FAMILY_TRUSTED_PROXIES steet ënnendrënner, direkt bei där
#  Astellung -- si gehéiert net hei hin.)
#
# ⚠ Off until it is set up, and that is on purpose: a notification goes THROUGH
#   Apple or Google, and that is a decision to make deliberately, not something
#   that happens because the software was installed. Nothing is sent while
#   these are empty.
#
# ⚠ What goes through them is the album's own title and a count -- "12 new
#   photographs in 2026 Ostende". Never a name, never a place beyond the album
#   title, never a photograph.
NOTIFY_WINDOW = int(os.environ.get("FAMILY_NOTIFY_WINDOW", "90"))

# Apple. The .p8 comes from the developer portal (Keys → new key → APNs).
APNS_KEY_FILE = os.environ.get("FAMILY_APNS_KEY_FILE", "").strip()
APNS_KEY_ID = os.environ.get("FAMILY_APNS_KEY_ID", "").strip()
APNS_TEAM_ID = os.environ.get("FAMILY_APNS_TEAM_ID", "").strip()
# ⚠ The bundle id of the app, EXACTLY -- Apple checks it against the key.
APNS_TOPIC = os.environ.get("FAMILY_APNS_TOPIC", "").strip()
# A build from Xcode gets its token from Apple's test service; TestFlight and
# the App Store use the live one. The wrong one answers `BadDeviceToken`.
APNS_SANDBOX = _bool("FAMILY_APNS_SANDBOX", False)

# Google. The JSON of a service account from the Firebase project.
FCM_CREDENTIALS = os.environ.get("FAMILY_FCM_CREDENTIALS", "").strip()


def apns_ready() -> bool:
    from pathlib import Path as _P
    return bool(APNS_KEY_FILE and APNS_KEY_ID and APNS_TEAM_ID and APNS_TOPIC
                and _P(APNS_KEY_FILE).is_file())


def fcm_ready() -> bool:
    from pathlib import Path as _P
    return bool(FCM_CREDENTIALS and _P(FCM_CREDENTIALS).is_file())


TRUSTED_PROXIES = tuple(
    p.strip() for p in os.environ.get("FAMILY_TRUSTED_PROXIES", "").split(",") if p.strip())

# ⚠ Which header carries the client's address. Behind Cloudflare, set this to
#   `CF-Connecting-IP`: Cloudflare OVERWRITES that one, while it only appends
#   to `X-Forwarded-For` -- so the first entry of X-Forwarded-For is still
#   whatever the visitor sent.
CLIENT_IP_HEADER = os.environ.get("FAMILY_CLIENT_IP_HEADER", "X-Forwarded-For").strip()


def trusts_proxy(peer: str) -> bool:
    """Is this the address of a proxy whose forwarding header we believe?"""
    if not peer or not TRUSTED_PROXIES:
        return False
    import ipaddress
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for entry in TRUSTED_PROXIES:
        try:
            if ip in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


# The headers an identity proxy sets. Whatever it does not set, a client can.
HDR_USER = "x-authentik-username"
HDR_GROUPS = "x-authentik-groups"
HDR_EMAIL = "x-authentik-email"


# Three roles: administrator (uploads, tidies, albums, share links), viewer
# (sees the gallery), contributor (uploads and manages their own photographs).
#
# ⚠ Lists, not single names. An identity provider does not push group changes:
#   they are set once at sign-in and hold for hours. Rename a group and accept
#   only one name, and every signed-in person is locked out until they sign in
#   again — which you find out about far too late.
def _groups(env: str, default: str) -> set:
    return set(g.strip() for g in os.environ.get(env, default).split(",") if g.strip())


_ENV_ADMIN_GROUPS = _groups("FAMILY_ADMIN_GROUPS", "admin")
_ENV_VIEWER_GROUPS = _groups("FAMILY_VIEWER_GROUPS", "family")
# Who may upload and manage their own photographs. By default everyone with an
# account; narrow it when only some of the household should contribute.
_ENV_CONTRIBUTOR_GROUPS = _groups("FAMILY_CONTRIBUTOR_GROUPS", "family")


# Share links: one collection, one address, for someone without an account.
# Off by default — while it is off the path does not exist at all: no code, no
# bug, no exception in the authentication.
SHARES_ENABLED = _bool("FAMILY_SHARES", False)

# What a guest may put back through a share link. The limits are per link; the
# third one is the global brake — ten links at two gigabytes each is twenty
# gigabytes on the same disk the database lives on.
GUEST_MAX_FILES = int(os.environ.get("FAMILY_GUEST_MAX_FILES", "50"))
GUEST_MAX_BYTES = int(os.environ.get("FAMILY_GUEST_MAX_BYTES", str(2 * 1024**3)))
GUEST_TOTAL_BYTES = int(os.environ.get("FAMILY_GUEST_TOTAL_BYTES", str(10 * 1024**3)))

# Virus scanning (app/av.py): every uploaded file is checked before the site
# trusts it. Off by default — the scanner wants about a gigabyte of memory.
CLAMAV_ENABLED = _bool("FAMILY_CLAMAV", False)
CLAMAV_TIMEOUT = int(os.environ.get("FAMILY_CLAMAV_TIMEOUT", "120"))
# ⚠ When the scanner is a neighbouring container, name it here: the file is
#   then streamed to it over TCP. `clamdscan --fdpass` hands over an open file
#   descriptor and only works inside the same container. Empty = local
#   `clamdscan`.
CLAMAV_HOST = os.environ.get("FAMILY_CLAMAV_HOST", "").strip()
CLAMAV_PORT = int(os.environ.get("FAMILY_CLAMAV_PORT", "3310"))

# --- the site -----------------------------------------------------------------
SITE_TITLE = os.environ.get("FAMILY_SITE_TITLE", "Roamlight")
# ⚠ Share links are built from this. Leave it at localhost and you hand people
#   links that only work on your own machine.
SITE_URL = os.environ.get("FAMILY_SITE_URL", "http://localhost:8080").rstrip("/")

# --- the picture pipeline -----------------------------------------------------
# The master: one JPEG, long edge 4000 px, q88, sRGB, metadata kept.
MASTER_LONG_EDGE = int(os.environ.get("FAMILY_MASTER_LONG_EDGE", "4000"))
MASTER_QUALITY = int(os.environ.get("FAMILY_MASTER_QUALITY", "88"))
DERIVATIVE_WIDTHS = [400, 800, 1200, 2000, 2800]
AVIF_QUALITY = int(os.environ.get("FAMILY_AVIF_Q", "58"))
# ⚠ Measured on a small server: effort 2 = 0.31 s and 266 kB · effort 9 =
#   18.46 s and 277 kB. Twenty times the work for a BIGGER file. libheif's own
#   default is 4, and that was the whole reason pages were slow to appear.
AVIF_EFFORT = int(os.environ.get("FAMILY_AVIF_EFFORT", "2"))
WEBP_QUALITY = int(os.environ.get("FAMILY_WEBP_Q", "78"))
# ⚠ exiftool reads the ORIGINAL, which may live on a network share. While the
#   share is busy, a single read takes minutes rather than seconds — a
#   conversion died on exactly that at 120 seconds.
EXIFTOOL_TIMEOUT = int(os.environ.get("FAMILY_EXIFTOOL_TIMEOUT", "300"))
LQIP_WIDTH = 24

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp"}
_RAW_SUFFIXES = {".cr3", ".cr2", ".crw", ".arw", ".sr2", ".nef", ".nrw",
                 ".raf", ".orf", ".rw2", ".pef", ".dng"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mts", ".avi"}
ALLOWED_SUFFIXES = _IMAGE_SUFFIXES | _RAW_SUFFIXES | _VIDEO_SUFFIXES
MAX_UPLOAD_BYTES = int(os.environ.get("FAMILY_MAX_UPLOAD", str(400 * 1024 * 1024)))
CHUNK_SIZE = 5 * 1024 * 1024
# ⚠ libvips has no pixel limit of its own. A 500 MB PNG of 60,000 × 60,000
#   pixels passes every byte limit there is and eats all the memory on the
#   machine. This is the limit that stops it, before anything is decoded.
MAX_MEGAPIXELS = int(os.environ.get("FAMILY_MAX_MEGAPIXELS", "50"))

# --- the map ------------------------------------------------------------------
# The only request this site makes to the outside world: looking up where a
# place is, once per place. It goes out from the SERVER, never from a browser.
GEOCODE = _bool("FAMILY_GEOCODE", True)
USER_AGENT = os.environ.get("FAMILY_USER_AGENT", f"Roamlight/1.0 (+{SITE_URL})")
# A journey by car or bus can follow the actual road. The route is fetched once
# when the journey is saved and stored with it. Empty = no routing, which draws
# a stylised arc instead. Plane and train are always an arc.
OSRM_URL = os.environ.get("FAMILY_OSRM_URL", "https://router.project-osrm.org")

# Home, for the journey animation: an album with no departure of its own starts
# here, so that every album — including a brand new one — opens with something,
# without anybody having to set it. Further than PLANE_KM and it is a plane,
# otherwise a car.
HOME_NAME = os.environ.get("FAMILY_HOME_NAME", "Luxembourg")
HOME_LAT = float(os.environ.get("FAMILY_HOME_LAT", "49.6117"))
HOME_LON = float(os.environ.get("FAMILY_HOME_LON", "6.1319"))
JOURNEY_PLANE_KM = float(os.environ.get("FAMILY_JOURNEY_PLANE_KM", "1100"))
# ⚠ Map tiles are fetched through THIS server and cached here. That way a
#   family member's browser never talks to OpenStreetMap — and the site's own
#   content policy can stay at `img-src 'self'`.
TILE_CACHE = _path("FAMILY_TILE_CACHE", DATA_DIR / "tiles")
TILE_URL = os.environ.get("FAMILY_TILE_URL",
                          "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
TILE_MAX_AGE_DAYS = int(os.environ.get("FAMILY_TILE_MAX_AGE_DAYS", "60"))
# Offline: tiles are served from the local cache only, no request ever leaves.
# The seeding job (app/tileseed.py) has to have run first. A missing tile comes
# back transparent — the map background shows through, rather than a checkerboard.
TILE_OFFLINE = _bool("FAMILY_TILE_OFFLINE", False)
# Seeding loads tiles in advance: z0..BASE covers the whole area of every place
# (the overview), and z(BASE+1)..MAX loads a block of RADIUS_TILES tiles around
# each place — the same number of tiles at every zoom level, so that the view
# around a place stays filled as you zoom in. (A radius in degrees would be
# less than one tile at medium zoom, and you would get a thin strip.) DELAY is
# the pause between downloads, to be polite to the tile server. New places?
# Run the job again — it only fetches what is missing.
TILE_SEED_BASE_ZOOM = int(os.environ.get("FAMILY_TILE_SEED_BASE", "7"))
TILE_SEED_MAX_ZOOM = int(os.environ.get("FAMILY_TILE_SEED_MAX", "15"))
TILE_SEED_RADIUS_TILES = int(os.environ.get("FAMILY_TILE_SEED_RADIUS", "4"))
TILE_SEED_DELAY = float(os.environ.get("FAMILY_TILE_SEED_DELAY", "0.35"))

# --- the worker ---------------------------------------------------------------
WORKER_POLL_SECONDS = float(os.environ.get("FAMILY_WORKER_POLL", "2"))

# How many conversions at the same time.
#
# ⚠ Measured on a four-core machine: one conversion uses 0.73 of a core — so
#   three quarters of the machine sat idle while seven hundred photographs
#   waited in the queue. Two or three take most of it and leave something for
#   everything else on the box.
WORKERS = int(os.environ.get("FAMILY_WORKERS", "2"))
JOB_MAX_ATTEMPTS = int(os.environ.get("FAMILY_JOB_ATTEMPTS", "5"))
JOB_MAX_RETRIES = int(os.environ.get("FAMILY_JOB_MAX_RETRIES", "500"))

# --- keeping the library and the site in step ---------------------------------
TRASH_DAYS = int(os.environ.get("FAMILY_TRASH_DAYS", "30"))

# Optional: read the list of people from Authentik, so accounts do not have to
# be typed twice. Only used with FAMILY_AUTH=proxy.
# ⚠ Use the internal http endpoint, not the public https one: a container
#   usually has no CA certificates, and the request stays on your own network.
_ENV_AUTHENTIK_URL = os.environ.get("FAMILY_AUTHENTIK_URL", "")
_ENV_AUTHENTIK_TOKEN_FILE = os.environ.get("FAMILY_AUTHENTIK_TOKEN_FILE", "")
# ⚠ If more than this is missing in one pass, the sync stops and changes
#   nothing. A share that half-mounted, or a folder renamed by hand, should not
#   be able to empty the site.
SCAN_MAX_MISSING_PCT = float(os.environ.get("FAMILY_SCAN_MAX_MISSING_PCT", "2"))
SCAN_MAX_MISSING_ABS = int(os.environ.get("FAMILY_SCAN_MAX_MISSING_ABS", "200"))


def proxy_secret() -> str:
    try:
        return secret_path("proxy").read_text().strip()
    except OSError:
        return ""


def check_startup() -> None:
    """Stop before the server listens, when the locks are not in place.

    A site that starts with half its authentication working is more dangerous
    than one that does not start at all: the one that does not start gets
    noticed.

    ⚠ Much shorter than it was. It used to have a whole second half about the
      shared proxy secret -- what to do when the configuration asked for
      forward-auth and the secret file had gone, and what to do when the
      settings page had asked instead. None of that exists now: the site talks
      to the provider itself, so there is no secret that has to match in two
      places and nothing that can be half there.

    ⚠ Runs AFTER `db.init()`, because the modes may come from the database.
    """
    if not REQUIRE_AUTH:
        return
    if not auth_modes():
        sys.exit("FAMILY_AUTH is empty -- use 'local', 'oidc' or 'local+oidc'.")
    if not auth_oidc():
        return
    # ⚠ Not a reason to refuse to start. A provider that is misconfigured, or
    #   unreachable this morning, means single sign-on does not work -- and the
    #   password road is still there, because `local` comes from the YAML and
    #   nothing can take it away. So: say it loudly and carry on.
    if not (setting("oidc_issuer") and setting("oidc_client_id")):
        import logging
        logging.getLogger("family").error(
            "⚠ SINGLE SIGN-ON IS ON BUT NOT CONFIGURED: no issuer or no client id. "
            "The button will not work. Settings -> Sign-in, or FAMILY_AUTH without "
            "'oidc'.")


def ensure_marker(root) -> None:
    """Write the marker file, when these folders are not required to be mounts.

    ⚠ The marker exists to catch a share that did not mount: an empty folder
    and an empty library look identical. With `FAMILY_REQUIRE_MOUNT=1` it is
    therefore NOT created here — there it has to come from the mount, or the
    check is worth nothing. With the check off (an ordinary folder, a Docker
    volume) it is created, because otherwise a fresh install sits at "not ok"
    and nothing says why.
    """
    try:
        if REQUIRE_MOUNT or not root.is_dir():
            return
        marker = root / MARKER_NAME
        if not marker.exists():
            marker.write_text("roamlight\n")
    except OSError:
        pass


def ensure_dirs() -> None:
    """Create the folders -- and shut them.

    ⚠ 0750, not 0755. `data/` holds the database, and in it are the members, the
      album keys, the hashes of the sessions and the device tokens, and the
      password hashes of the share links. The default would be 0755 -- readable
      by anybody on the machine. The group keeps its access, because the sync
      and the backup runs are the same user.
    """
    for d in (DATA_DIR, INCOMING_DIR, DERIVATIVE_DIR, TILE_CACHE, WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o750)
        except OSError:
            pass


def sweep_work(older_than_hours: int = 6) -> int:
    """Clear out the scratch folders of conversions that never finished.

    `convert.py` works in a `TemporaryDirectory` and the context manager
    removes it -- unless the process is killed first. A restart during a big
    import therefore leaves one folder per conversion behind, and they add up:
    on the live site 67 of them, 97 MB, before this existed.

    ⚠ Only folders that have not been touched for `older_than_hours` go. A
      conversion running RIGHT NOW has a young folder, and two workers must not
      delete each other's work. Six hours is far past the slowest possible
      conversion (a 400 MB video) and far short of a leak worth keeping.
    """
    import shutil
    import time

    cutoff = time.time() - older_than_hours * 3600
    gone = 0
    try:
        entries = list(WORK_DIR.iterdir())
    except OSError:
        return 0
    for d in entries:
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                gone += 1
        except OSError:
            pass
    return gone


# --- the changeable settings, under their old names --------------------------
#
# ⚠ `ADMIN_GROUPS`, `TRUSTED_PEERS` and the rest are read in some fifty places
#   across the program -- every permission check, the CLI, the tests. Renaming
#   all of those to function calls would have been fifty chances to get one
#   wrong, in exactly the code where wrong means somebody sees an album they
#   should not. So the names stay, and Python hands them over here instead
#   (PEP 562): the module-level values are `_ENV_*`, and the public name is
#   resolved through this function every time it is read.
#
# ⚠ Which is why there is a cache. `ADMIN_GROUPS` is read on the way into every
#   request and for every image on a gallery page; a database round trip each
#   time would be felt. Dropped by `set_setting()`.
#
# ⚠ And it is why a bare `ADMIN_GROUPS` INSIDE this file does not work -- module
#   `__getattr__` is not consulted for names in the module's own scope. Inside
#   here, call the function (`admin_groups()`, `secret_path("proxy")`).
_setting_cache: dict = {}

_DYNAMIC = {
    "ADMIN_GROUPS":         admin_groups,
    "VIEWER_GROUPS":        viewer_groups,
    "CONTRIBUTOR_GROUPS":   contributor_groups,
    "TRUSTED_PEERS":        trusted_peers,
    "AUTHENTIK_URL":        authentik_url,
    "PROXY_SECRET_FILE":    lambda: secret_path("proxy"),
    "AUTHENTIK_TOKEN_FILE": lambda: secret_path("authentik"),
    # The single one, for an error message. Falls back so that a site with no
    # admin group named still says something rather than raising IndexError.
    "ADMIN_GROUP":          lambda: (sorted(admin_groups()) or ["admin"])[0],
}


def forget_settings() -> None:
    _setting_cache.clear()


def __getattr__(name):
    fn = _DYNAMIC.get(name)
    if fn is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name not in _setting_cache:
        _setting_cache[name] = fn()
    return _setting_cache[name]
