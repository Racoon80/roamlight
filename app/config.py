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
AUTH_MODES = set(m.strip().lower() for m in
                 os.environ.get("FAMILY_AUTH", "local").replace(",", "+").split("+")
                 if m.strip())
AUTH_LOCAL = "local" in AUTH_MODES
AUTH_PROXY = "proxy" in AUTH_MODES

# How long a sign-in lasts, in days.
# ⚠ Den éischte Start ass eng oppen Dier: soulaang et kee Kont gëtt, kann
#   JIDDEREN, deen d'Adress erreecht, sech als Administrateur uleeën. Dat ass
#   bewosst esou (soss kënnt keen eran), mä op engem Netz, wou nach een anere
#   sëtzt, gehéiert e Rigel dovir. Ass dat hei gesat, muss d'Setup-Säit dat
#   Wuert kréien -- als `?t=…` oder am Formulaire.
SETUP_TOKEN = os.environ.get("FAMILY_SETUP_TOKEN", "").strip()

SESSION_DAYS = int(os.environ.get("FAMILY_SESSION_DAYS", "30"))
SESSION_COOKIE = "family_session"
# For a TLS proxy that does not set `X-Forwarded-Proto`: put this at 1, and the
# session cookie is marked Secure regardless.
COOKIE_SECURE = _bool("FAMILY_COOKIE_SECURE", False)

REQUIRE_AUTH = _bool("FAMILY_REQUIRE_AUTH", True)
PROXY_SECRET_FILE = _path("FAMILY_PROXY_SECRET_FILE", "/etc/roamlight/proxy-secret")
PROXY_HEADER = "x-family-proxy"
# ⚠ Only these peers may send anything at all. The proxy runs on the same host,
#   so that is 127.0.0.1 — anything else means somebody went around it.
TRUSTED_PEERS = set(
    p.strip() for p in os.environ.get("FAMILY_TRUSTED_PEERS", "127.0.0.1,::1").split(",") if p.strip()
)
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


ADMIN_GROUPS = _groups("FAMILY_ADMIN_GROUPS", "admin")
VIEWER_GROUPS = _groups("FAMILY_VIEWER_GROUPS", "family")
# Who may upload and manage their own photographs. By default everyone with an
# account; narrow it when only some of the household should contribute.
CONTRIBUTOR_GROUPS = _groups("FAMILY_CONTRIBUTOR_GROUPS", "family")
ADMIN_GROUP = sorted(ADMIN_GROUPS)[0]        # only used in an error message

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
AUTHENTIK_URL = os.environ.get("FAMILY_AUTHENTIK_URL", "")
AUTHENTIK_TOKEN_FILE = os.environ.get("FAMILY_AUTHENTIK_TOKEN_FILE", "")
# ⚠ If more than this is missing in one pass, the sync stops and changes
#   nothing. A share that half-mounted, or a folder renamed by hand, should not
#   be able to empty the site.
SCAN_MAX_MISSING_PCT = float(os.environ.get("FAMILY_SCAN_MAX_MISSING_PCT", "2"))
SCAN_MAX_MISSING_ABS = int(os.environ.get("FAMILY_SCAN_MAX_MISSING_ABS", "200"))


def proxy_secret() -> str:
    try:
        return PROXY_SECRET_FILE.read_text().strip()
    except OSError:
        return ""


def check_startup() -> None:
    """Stop before the server listens, when the locks are not in place.

    A site that starts with half its authentication working is more dangerous
    than one that does not start at all: the one that does not start gets
    noticed.

    ⚠ The shared secret belongs to the proxy path. A local sign-in has none —
    there the proof is the password and the cookie. Without that distinction a
    container would refuse to start at all.
    """
    if not REQUIRE_AUTH:
        return
    if not AUTH_MODES:
        sys.exit("FAMILY_AUTH is empty -- use 'local', 'proxy' or 'local+proxy'.")
    if not AUTH_PROXY:
        return
    secret = proxy_secret()
    if len(secret) < 32:
        sys.exit(
            f"FAMILY_AUTH contains 'proxy', but {PROXY_SECRET_FILE} is missing or "
            "too short (32 characters at least). Without it, anyone could send "
            "the headers your proxy sends."
        )


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
    for d in (DATA_DIR, INCOMING_DIR, DERIVATIVE_DIR, TILE_CACHE):
        d.mkdir(parents=True, exist_ok=True)
