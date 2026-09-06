"""SQLite in WAL mode, with migrations and the job queue.

The schema carries `origin_root` from the start even though there is only one
value today -- when a second library is added one day, that is a migration of
one row and not a rewrite.
"""
import logging
import sqlite3
import threading
from contextlib import contextmanager

from . import config

log = logging.getLogger("family")

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id          INTEGER PRIMARY KEY,
    tree        TEXT NOT NULL,                    -- origin | web
    path        TEXT NOT NULL,                    -- relativ zur Wuerzel vum Bam
    kind        TEXT NOT NULL,                    -- year | country | event
    year        INTEGER,
    country     TEXT,
    event       TEXT,
    UNIQUE (tree, path)
);

CREATE TABLE IF NOT EXISTS photos (
    id                  INTEGER PRIMARY KEY,
    web_folder_id       INTEGER REFERENCES folders(id),
    web_name            TEXT,
    origin_root         TEXT NOT NULL DEFAULT 'my_photos',   -- my_photos | photos | user
    -- ⚠ Who uploaded the photograph. NULL = an administrator, or the library
    -- itself. A contributor's photograph (origin_root='user') has NO original
    -- in the library -- the upload was thrown away after the conversion and it
    -- lives only as the master. The owner may delete, turn and share their own
    -- photographs; nobody else (except an administrator) may.
    owner               TEXT,
    origin_path         TEXT NOT NULL,
    origin_sha256       TEXT,
    origin_bytes        INTEGER,
    origin_mtime        REAL,
    origin_kind         TEXT,
    master_source_path  TEXT,
    master_source_sha256 TEXT,
    master_built_at     TEXT,
    country             TEXT,
    place               TEXT,
    taken_at            TEXT,
    taken_source        TEXT,                     -- exif | file | manual
    width               INTEGER,
    height              INTEGER,
    camera              TEXT,
    lens                TEXT,
    iso                 INTEGER,
    aperture            TEXT,
    shutter             TEXT,
    gps_lat             REAL,
    gps_lon             REAL,
    kind                TEXT NOT NULL DEFAULT 'photo',       -- photo | video
    duration_s          REAL,
    rating              INTEGER NOT NULL DEFAULT 0,
    hidden              INTEGER NOT NULL DEFAULT 0,
    rev                 INTEGER NOT NULL DEFAULT 0,   -- raised when it is turned
    -- ⚠ The album year and the album name are FIELDS OF THEIR OWN, not derived.
    -- They used to come from `substr(taken_at,1,4)` and from cutting up
    -- `origin_path`. That made them impossible to change: a photograph taken on
    -- 31 December sat in the wrong album, and renaming a folder went unnoticed
    -- by the site entirely. They are set from the path when the row is created
    -- and kept in step when it is edited (album.edit).
    album_year          TEXT,
    event               TEXT,
    title               TEXT,
    note                TEXT,
    state               TEXT NOT NULL DEFAULT 'new',
        -- new | ok | changed | moved | missing | gone | infected
    missing_since       TEXT,
    xmp_changed_at      TEXT,
    last_seen_scan_id   INTEGER,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (origin_root, origin_path)
);

CREATE TABLE IF NOT EXISTS variants (
    id          INTEGER PRIMARY KEY,
    photo_id    INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    ext         TEXT,
    bytes       INTEGER,
    mtime       REAL,
    sha256      TEXT,
    is_master_source INTEGER NOT NULL DEFAULT 0,
    UNIQUE (photo_id, path)
);

CREATE TABLE IF NOT EXISTS upload_batches (
    id           INTEGER PRIMARY KEY,
    token        TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    committed_at TEXT,
    year         TEXT,
    country      TEXT,
    event        TEXT,
    place        TEXT,
    share_id     INTEGER REFERENCES shares(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS upload_files (
    id            INTEGER PRIMARY KEY,
    batch_id      INTEGER NOT NULL REFERENCES upload_batches(id) ON DELETE CASCADE,
    original_name TEXT,          -- what the client said. NEVER reaches the file system.
    ext           TEXT,
    size          INTEGER,
    sha256        TEXT,
    taken_at      TEXT,
    taken_source  TEXT,
    camera        TEXT,
    lens          TEXT,
    gps_lat       REAL,
    gps_lon       REAL,
    kind          TEXT,
    dup_of        INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    photo_id      INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    state         TEXT NOT NULL DEFAULT 'uploading',
        -- uploading | ready | rejected | stored
    note          TEXT
);

CREATE TABLE IF NOT EXISTS removed (
    origin_root TEXT NOT NULL,
    origin_path TEXT NOT NULL,
    sha256      TEXT,
    at          TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (origin_root, origin_path)
);

CREATE TABLE IF NOT EXISTS places (
    place        TEXT NOT NULL,
    country      TEXT NOT NULL DEFAULT '',
    lat          REAL,
    lon          REAL,
    display_name TEXT,
    region       TEXT,
    source       TEXT,
    looked_up_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (place, country)
);

CREATE TABLE IF NOT EXISTS members (
    username          TEXT PRIMARY KEY,
    display_name      TEXT NOT NULL DEFAULT '',
    email             TEXT NOT NULL DEFAULT '',
    active            INTEGER NOT NULL DEFAULT 1,
    groups_json       TEXT NOT NULL DEFAULT '[]',
    seen_in_authentik INTEGER NOT NULL DEFAULT 0,
    last_seen_at      TEXT,
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Who sees an album.
--
-- ⚠ The rule: **no row = THE ADMINISTRATOR ONLY**. A photograph has to BE on
-- a list before anybody sees it. Closed until somebody opens it.
--
-- ⚠⚠ This comment once said the opposite ("no row = everybody"), and at that
-- time it was true. The rule was then turned around. A comment that no longer
-- matches the code is more dangerous than none at all: sooner or later
-- somebody fixes the code to match the comment -- and then the whole library
-- stands open.
--
-- `principal` is `user:<name>` or `group:<name>`.
CREATE TABLE IF NOT EXISTS album_acl (
    album_key TEXT NOT NULL,              -- <Joer>/<Land>/<Numm>
    principal TEXT NOT NULL,
    added_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (album_key, principal)
);
CREATE INDEX IF NOT EXISTS idx_acl_key ON album_acl (album_key);

-- Which albums have been gone through.
--
-- A folder picked up by a scan is not finished yet: year, name, place and
-- country come out of the path, and that path does not have to be right. Only
-- once somebody has saved the row is the album done. Hence this table --
-- otherwise new albums would slide in among the finished ones and nobody
-- would see what was still to do.
CREATE TABLE IF NOT EXISTS album_published (
    album_key TEXT PRIMARY KEY,           -- <Joer>/<Land>/<Numm>
    at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The journey: a departure and a means of travel per album. The destination
-- comes from the album itself (GPS or the looked-up place), so NOT here.
CREATE TABLE IF NOT EXISTS album_journey (
    album_key TEXT PRIMARY KEY,           -- <year>/<country>/<name>
    departure TEXT NOT NULL,              -- the name as typed (e.g. "Rumelange")
    dep_lat   REAL,
    dep_lon   REAL,
    transport TEXT NOT NULL DEFAULT 'car', -- car | bus | train | plane
    route     TEXT,                       -- JSON [[lat,lon],...] for car/bus, else NULL
    legs      TEXT                        -- JSON [{name,lat,lon,transport,route}], optional multi-hop
);

-- ---------------------------------------------------------------------------
--  Devices (the iPhone and iPad app). See app/devices.py.
-- ---------------------------------------------------------------------------
-- A pairing code is what stands in the QR code on the /app page: short-lived
-- and good for one use. It is what the app trades for its real token.
CREATE TABLE IF NOT EXISTS app_pairings (
    code       TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    used_at    TEXT
);

-- One connected device. ⚠ Only the HASH of the token is stored; the value
-- itself is shown exactly once (when it is scanned) and is then gone.
CREATE TABLE IF NOT EXISTS app_devices (
    id          INTEGER PRIMARY KEY,
    ref         TEXT NOT NULL UNIQUE,   -- the public half: what is searched on
    username    TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    secret_hash TEXT NOT NULL,          -- sha256 of the secret (see devices.py)
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT,
    last_ip     TEXT,
    revoked_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_devices_user ON app_devices(username);

-- A sign-in with a password. ⚠ As with devices: only the hash of the secret
-- is stored, and the lookup key sits next to it.
CREATE TABLE IF NOT EXISTS sessions (
    ref         TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    secret_hash TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT NOT NULL,
    last_seen   TEXT,
    agent       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(username);

CREATE TABLE IF NOT EXISTS scans (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,                  -- quick | deep
    started_at    TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at   TEXT,
    n_new         INTEGER NOT NULL DEFAULT 0,
    n_changed     INTEGER NOT NULL DEFAULT 0,
    n_moved       INTEGER NOT NULL DEFAULT 0,
    n_missing     INTEGER NOT NULL DEFAULT 0,
    halted_reason TEXT
);

CREATE TABLE IF NOT EXISTS albums (
    id             INTEGER PRIMARY KEY,
    slug           TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    cover_photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS album_photos (
    album_id   INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    photo_id   INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    sort_index INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (album_id, photo_id)
);

CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS photo_tags (
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (photo_id, tag_id)
);

CREATE TABLE IF NOT EXISTS people (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS photo_people (
    photo_id  INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    PRIMARY KEY (photo_id, person_id)
);

CREATE TABLE IF NOT EXISTS shares (
    id              INTEGER PRIMARY KEY,
    album_id        INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    token           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,                -- argon2id
    expires_at      TEXT NOT NULL,                -- Flicht, kee Link ouni Enn
    allow_download  INTEGER NOT NULL DEFAULT 1,
    allow_upload    INTEGER NOT NULL DEFAULT 0,
    keep_gps        INTEGER NOT NULL DEFAULT 0,
    guest_max_files INTEGER NOT NULL DEFAULT 50,
    guest_max_bytes INTEGER NOT NULL DEFAULT 2147483648,
    fail_count      INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at      TEXT
);

CREATE TABLE IF NOT EXISTS share_hits (
    id       INTEGER PRIMARY KEY,
    share_id INTEGER NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
    at       TEXT NOT NULL DEFAULT (datetime('now')),
    ip_hash  TEXT,
    ua       TEXT
);

CREATE TABLE IF NOT EXISTS share_uploads (
    id         INTEGER PRIMARY KEY,
    share_id   INTEGER NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
    at         TEXT NOT NULL DEFAULT (datetime('now')),
    guest_name TEXT,
    filename   TEXT,
    bytes      INTEGER,
    sha256     TEXT,
    state      TEXT NOT NULL DEFAULT 'guest',     -- guest | accepted | rejected
    photo_id   INTEGER REFERENCES photos(id) ON DELETE SET NULL
);

-- A signed-in family member adds a photograph to ONE album. Same rule as for
-- guest uploads (guests.py): nothing lands on the site by itself, everything
-- waits for an administrator's click. The difference: the target is ONE
-- existing album (year/country/name), not a "guests" folder.
CREATE TABLE IF NOT EXISTS album_uploads (
    id         INTEGER PRIMARY KEY,
    at         TEXT NOT NULL DEFAULT (datetime('now')),
    year       TEXT,
    country    TEXT,
    event      TEXT,
    uploader   TEXT,                              -- member name (metadata, never a file name)
    filename   TEXT,
    bytes      INTEGER,
    sha256     TEXT,
    state      TEXT NOT NULL DEFAULT 'guest',     -- guest | accepted | rejected
    photo_id   INTEGER REFERENCES photos(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS moves (
    id        INTEGER PRIMARY KEY,
    batch_id  TEXT NOT NULL,
    at        TEXT NOT NULL DEFAULT (datetime('now')),
    src       TEXT NOT NULL,
    dst       TEXT NOT NULL,
    sha256    TEXT,
    undone_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id        INTEGER PRIMARY KEY,
    kind      TEXT NOT NULL,
    payload   TEXT,
    status    TEXT NOT NULL DEFAULT 'pending',    -- pending | running | done | error
    attempts  INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    run_after TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_photos_folder  ON photos (web_folder_id);
CREATE INDEX IF NOT EXISTS idx_photos_taken   ON photos (taken_at);
CREATE INDEX IF NOT EXISTS idx_photos_state   ON photos (state);
CREATE INDEX IF NOT EXISTS idx_photos_sha     ON photos (origin_sha256);
CREATE INDEX IF NOT EXISTS idx_photos_country ON photos (country);
CREATE INDEX IF NOT EXISTS idx_jobs_pending   ON jobs (status, id);
CREATE INDEX IF NOT EXISTS idx_variants_photo ON variants (photo_id);
CREATE INDEX IF NOT EXISTS idx_share_hits     ON share_hits (share_id, at);
CREATE INDEX IF NOT EXISTS idx_upload_files   ON upload_files (batch_id, state);
"""

_local = threading.local()


def _shut(path) -> None:
    """Shut the database: 0640 instead of 0644.

    ⚠ SQLite creates the file with the umask -- so, normally, readable by
      anybody. In it are the members, the hashes of the sessions and the
      device tokens and the password hashes of the share links. The group keeps
      its read access, because the backup and the sync runs are there. The -wal
      and the -shm too, or the same content lies open right next to it.
    """
    import os as _os
    from pathlib import Path as _P
    for suffix in ("", "-wal", "-shm"):
        f = _P(str(path) + suffix)
        try:
            if f.exists() and (f.stat().st_mode & 0o777) != 0o640:
                _os.chmod(f, 0o640)
        except OSError:
            pass


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.ensure_dirs()
        conn = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        _shut(config.DB_PATH)
        _local.conn = conn
    return conn


@contextmanager
def tx():
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# New columns on existing tables. SQLite has no "ADD COLUMN IF NOT EXISTS",
# so what is already there is looked up. Only columns that can be ADDED --
# anything else belongs in a real migration step.
MIGRATIONS = [
    ("photos", "rev", "INTEGER NOT NULL DEFAULT 0"),
    ("photos", "album_year", "TEXT"),
    ("photos", "event", "TEXT"),
    ("photos", "owner", "TEXT"),
    ("albums", "owner", "TEXT"),
    ("shares", "owner", "TEXT"),
    # Undo of an album rebuild: the old and new state, as JSON.
    ("moves", "meta", "TEXT"),
    # The view limit of a share link: how often it may be opened. NULL =
    # unlimited. A user's link stands at 1, the admin sets it themselves.
    ("shares", "max_views", "INTEGER"),
    # A cover set by hand ("make cover"): that photograph carries
    # is_cover=1. Otherwise the newest one is used automatically.
    ("photos", "is_cover", "INTEGER NOT NULL DEFAULT 0"),
    # The journey: the road route for car/bus (JSON), computed once.
    ("album_journey", "route", "TEXT"),
    ("album_journey", "legs", "TEXT"),
    # Multi-hop on/off. Default 1, so albums that already have stops keep
    # working unchanged.
    ("album_journey", "legs_on", "INTEGER NOT NULL DEFAULT 1"),
    # Local sign-in (for installations without SSO).
    # `password_hash` is argon2id; `is_local` tells an account of this site
    # apart from one that came out of a directory.
    ("members", "password_hash", "TEXT"),
    ("members", "is_local", "INTEGER NOT NULL DEFAULT 0"),
]


def _migrate(conn) -> None:
    for table, column, decl in MIGRATIONS:
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    _backfill_album_columns(conn)
    _seed_published(conn)


def _seed_published(conn) -> int:
    """Anything with a viewing list has already been gone through.

    When the table is introduced: an album where somebody ticked the names is
    one that was worked on. Anything else would be rude -- it would mean going
    through every finished album a second time.
    """
    if conn.execute("SELECT COUNT(*) FROM album_published").fetchone()[0]:
        return 0
    n = conn.execute(
        "INSERT OR IGNORE INTO album_published (album_key) "
        "SELECT DISTINCT album_key FROM album_acl").rowcount
    return n or 0


def _backfill_album_columns(conn) -> int:
    """Fill in `album_year` and `event` from the path where they are empty.

    The path is <year>/<country>/<name>/<file>. Older rows have three parts
    (<year>/<name>/<file>) -- no country there, and the name is the second
    part. Done once; after that it is always zero rows.
    """
    rows = conn.execute(
        "SELECT id, origin_path, taken_at FROM photos "
        "WHERE album_year IS NULL OR event IS NULL").fetchall()
    n = 0
    for r in rows:
        parts = (r["origin_path"] or "").split("/")
        if len(parts) >= 4:
            year, event = parts[0], parts[2]
        elif len(parts) == 3:
            year, event = parts[0], parts[1]
        else:
            year, event = (r["taken_at"] or "")[:4], (parts[-2] if len(parts) >= 2 else "")
        if not (year or "").isdigit() or len(year or "") != 4:
            year = (r["taken_at"] or "")[:4] or None
        conn.execute("UPDATE photos SET album_year=?, event=? WHERE id=?",
                     (year or None, event or None, r["id"]))
        n += 1
    return n


# Foreign keys that would block an ordinary delete. Every one of them means
# "remember this while it is still there", not "this must not go".
_FK_SET_NULL = (
    ("albums", "cover_photo_id"),
    ("upload_files", "photo_id"),
    ("upload_files", "dup_of"),
    ("share_uploads", "photo_id"),
    ("album_uploads", "photo_id"),
    ("upload_batches", "share_id"),
)


def _fix_fks(conn) -> list:
    """`ON DELETE SET NULL` on foreign keys that were created as NO ACTION.

    ⚠ Why this has to be: taking a photograph off the site (`register.remove`)
      does a `DELETE FROM photos`. If that same photograph is the cover of a
      collection, `albums.cover_photo_id` blocks it -- and the click dies with
      `FOREIGN KEY constraint failed`, a 500 for no reason. The same for a
      photograph that still stands in an old upload record.

    ⚠ SQLite cannot change a foreign key. So the table is rebuilt -- from ITS
      OWN definition out of `sqlite_master`, with only that one line patched.
      That way no column, no default and no constraint is lost. The indexes are
      recreated, and then the row count is compared -- if it differs, it rolls
      back.
    """
    import re
    todo = []
    for table, col in _FK_SET_NULL:
        try:
            for fk in conn.execute(f"PRAGMA foreign_key_list({table})"):
                if fk[3] == col and (fk[6] or "NO ACTION").upper() != "SET NULL":
                    todo.append((table, col))
        except sqlite3.Error:
            continue
    if not todo:
        return []

    done = []
    for table in dict.fromkeys(t for t, _ in todo):
        cols = [c for tb, c in todo if tb == table]
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                           (table,)).fetchone()
        if not row or not row[0]:
            continue
        sql = row[0]
        for col in cols:
            # only THAT one column line, and only where no ON DELETE stands yet
            sql = re.sub(
                rf"(^\s*{re.escape(col)}\s+[^,\n]*?REFERENCES\s+\w+\s*\([^)]*\))"
                rf"(?![^,\n]*ON DELETE)",
                r"\1 ON DELETE SET NULL", sql, flags=re.M)
        sql = sql.replace(f"TABLE {table}", f"TABLE {table}__new", 1)
        sql = sql.replace(f'TABLE "{table}"', f'TABLE "{table}__new"', 1)
        if "__new" not in sql:
            log.warning("fk migration: %s could not be renamed -- skipped", table)
            continue
        idx = [r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table,))]
        before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.execute("PRAGMA foreign_keys=OFF")
        # ⚠ Only begin one if none is running: `connect()` is in autocommit,
        #   but a caller with an open transaction would otherwise fail here with
        #   "cannot start a transaction within a transaction".
        own = not conn.in_transaction
        try:
            if own:
                conn.execute("BEGIN IMMEDIATE")
            conn.execute(sql)
            conn.execute(f"INSERT INTO {table}__new SELECT * FROM {table}")
            after = conn.execute(f"SELECT COUNT(*) FROM {table}__new").fetchone()[0]
            if after != before:
                raise RuntimeError(f"{table}: {before} -> {after} Zeilen")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}__new RENAME TO {table}")
            for s in idx:
                conn.execute(s)
            if own:
                conn.execute("COMMIT")
            done.append(f"{table}({', '.join(cols)})")
            log.warning("foreign key on %s set to ON DELETE SET NULL (%d rows kept)",
                        table, before)
        except Exception as exc:                                 # noqa: BLE001
            if own and conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.execute(f"DROP TABLE IF EXISTS {table}__new")
            log.error("fk migration for %s aborted: %s", table, exc)
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
    return done


def _repair(conn) -> int:
    """Remove orphaned rows -- the ones pointing at something that is not there.

    ⚠ Why this is needed: `sqlite3.connect()` turns foreign keys OFF. If any
      path reaches the file around `connect()` (an acceptance test, a repair by
      hand, an old version of the program), a photograph can be deleted and the
      rows in `album_photos`, `upload_files` and `share_hits` stay behind.

      Those rows are not merely rubbish: as soon as an ordinary action touches
      one -- taking a photograph out of a collection, which sets a new cover --
      the request dies with `FOREIGN KEY constraint failed`, and the user gets a
      500 for no reason. That is exactly what happened on the live site.

    ONLY what points at something that does not exist is removed -- never a
    photograph, never an album, never a collection.
    """
    gone = 0
    for row in list(conn.execute("PRAGMA foreign_key_check")):
        table, rowid, parent = row[0], row[1], row[2]
        if rowid is None:                     # WITHOUT ROWID -- net eendeiteg
            continue
        conn.execute(f"DELETE FROM {table} WHERE rowid=?", (rowid,))
        gone += 1
        log.warning("orphaned row removed: %s.rowid=%s -> %s", table, rowid, parent)
    if gone:
        log.warning("%d orphaned row(s) removed -- the database is consistent again", gone)
    return gone


def init() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    _migrate(conn)
    _fix_fks(conn)
    _repair(conn)
    set_state("schema_version", "6")
    # ⚠ Once more: the -wal and the -shm only appear on the first write, that
    #   is AFTER the connection -- and then they carry the umask again.
    _shut(config.DB_PATH)


def get_state(key: str, default=None):
    row = connect().execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(key: str, value) -> None:
    connect().execute(
        "INSERT INTO state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def counts() -> dict:
    c = connect()
    def one(sql, *a):
        return c.execute(sql, a).fetchone()[0]
    return {
        "photos": one("SELECT COUNT(*) FROM photos"),
        "on_site": one("SELECT COUNT(*) FROM photos WHERE state='ok' AND hidden=0"),
        "missing": one("SELECT COUNT(*) FROM photos WHERE state='missing'"),
        "infected": one("SELECT COUNT(*) FROM photos WHERE state='infected'"),
        "jobs_pending": one("SELECT COUNT(*) FROM jobs WHERE status='pending'"),
        "jobs_error": one("SELECT COUNT(*) FROM jobs WHERE status='error'"),
    }
