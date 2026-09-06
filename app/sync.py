"""The sync -- the site as a mirror of the originals tree.

Whatever goes **through the site** is there immediately anyway. This module is
for everything done **outside** it: a file manager, Lightroom, a card reader.

| In the originals tree | On the site |
|---|---|
| removed | disappears -- but the row stays, and the master goes to the bin |
| changed | is converted again, the web sizes drop out of the cache |
| renamed / moved | stays THE SAME photograph: title, rating, tags all stay |
| new | arrives on the site by itself |

No `inotify`: over a network share there are no events. So **look, do not listen**.

Two runs, because one check is cheap and the other is expensive:

* **quick run** -- `stat` only (size + mtime). Every night.
* **deep run** -- recompute the sha256 of everything. Every Sunday. It finds a
  file that was written back with an *old* mtime, and bit rot on the disk.

⚠ The two brakes further down are the most important thing in this module.
Without them, a share that is not mounted turns "the share is gone" into
"delete everything".
"""
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from . import av, config, convert, db, library, scan
from .worker import enqueue

log = logging.getLogger("family")

TRASH_DIR = ".trash"


# ---------------------------------------------------------------------------
#  Listing the disk
# ---------------------------------------------------------------------------
def _walk(root: Path) -> dict:
    """`{relative path: (size, mtime)}` -- `stat` only, no data read."""
    out = {}
    for path in root.rglob("*"):
        name = path.name
        if name.startswith(".") or TRASH_DIR in path.parts:
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in config.ALLOWED_SUFFIXES:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        out[str(path.relative_to(root))] = (st.st_size, st.st_mtime)
    return out


def _folder(rel: str) -> str:
    return str(Path(rel).parent)


# ---------------------------------------------------------------------------
#  D'Poubelle
# ---------------------------------------------------------------------------
def _trash(web_name: str) -> str:
    """Move the master into the bin. Returns the path there, or ''.

    NOTHING is deleted here -- `empty_trash()` does that, after
    `FAMILY_TRASH_DAYS` days. Until then the photograph comes back with one
    move, if the original turns up again.
    """
    if not web_name:
        return ""
    src = config.WEB_DIR / web_name
    if not src.is_file():
        return ""
    dst = config.WEB_DIR / TRASH_DIR / datetime.now().strftime("%Y-%m-%d") / web_name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst = dst.with_name(f"{dst.stem}~{int(datetime.now().timestamp())}{dst.suffix}")
    shutil.move(str(src), str(dst))
    return str(dst.relative_to(config.WEB_DIR))


def _untrash(web_name: str) -> bool:
    """Take the master back out of the bin, if it is still lying there."""
    if not web_name:
        return False
    goal_ = config.WEB_DIR / web_name
    if goal_.is_file():
        return True
    trash = config.WEB_DIR / TRASH_DIR
    if not trash.is_dir():
        return False
    for day_dir in sorted(trash.iterdir(), reverse=True):
        cand = day_dir / web_name
        if cand.is_file():
            goal_.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(cand), str(goal_))
            return True
    return False


def empty_trash(days: int = None) -> dict:
    """Whatever has been in the bin longer than `FAMILY_TRASH_DAYS` goes."""
    days = config.TRASH_DAYS if days is None else days
    trash = config.WEB_DIR / TRASH_DIR
    if not trash.is_dir():
        return {"deleted": 0, "kept": 0}
    # Local time, because the folder name is written in local time too --
    # otherwise a run around midnight would be one day off.
    cutoff = (datetime.now() - timedelta(days=days)).date()
    gone_ = left_over = 0
    for day_dir in sorted(trash.iterdir()):
        if not day_dir.is_dir():
            continue
        try:
            when_ = datetime.strptime(day_dir.name, "%Y-%m-%d").date()
        except ValueError:
            left_over += 1
            continue
        if when_ < cutoff:
            n = sum(1 for _ in day_dir.rglob("*") if _.is_file())
            shutil.rmtree(day_dir, ignore_errors=True)
            gone_ += n
            log.info("bin: %s emptied (%d files, older than %d days)",
                     day_dir.name, n, days)
        else:
            left_over += sum(1 for _ in day_dir.rglob("*") if _.is_file())
    return {"deleted": gone_, "kept": left_over}


# ---------------------------------------------------------------------------
#  De Laf
# ---------------------------------------------------------------------------
def run(kind: str = "quick", confirm_missing: bool = False,
        root: Path = None) -> dict:
    """One run. `kind` is `quick` (stat only) or `deep` (hash everything).

    `confirm_missing=True` is the operator's "yes, that was me" -- it lets a
    run through that the mass brake stopped the first time.
    """
    if kind not in ("quick", "deep"):
        raise ValueError("kind must be quick or deep")
    root = root or config.ORIGIN_DIR

    with db.tx() as c:
        scan_id = c.execute("INSERT INTO scans (kind) VALUES (?)", (kind,)).lastrowid

    def result_(halted=None, **numbers):
        with db.tx() as c:
            c.execute("UPDATE scans SET finished_at=datetime('now'), n_new=?, "
                      "n_changed=?, n_moved=?, n_missing=?, halted_reason=? WHERE id=?",
                      (numbers.get("new", 0), numbers.get("changed", 0),
                       numbers.get("moved", 0), numbers.get("missing", 0),
                       halted, scan_id))
        return {"scan_id": scan_id, "kind": kind, "halted": halted, **numbers}

    # -- Brake 1: is the share there at all? -------------------------------
    # A share that is not mounted is an empty folder, and an empty folder
    # suddenly means "every photograph removed". This is that brake.
    try:
        library.check_tree(root)
    except Exception as exc:                                    # noqa: BLE001
        log.error("sync stopped: %s", exc)
        return result_(halted=f"the share is not mounted: {exc}")

    on_disk = _walk(root)
    conn = db.connect()
    known = {r["origin_path"]: dict(r) for r in conn.execute(
        "SELECT id, origin_path, origin_sha256, origin_bytes, origin_mtime, "
        "       state, web_name FROM photos WHERE origin_root='my_photos'")}
    blocked_ = {r["origin_path"]: r["sha256"] for r in conn.execute(
        "SELECT origin_path, sha256 FROM removed WHERE origin_root='my_photos'")}

    new_paths = [r for r in on_disk if r not in known and r not in blocked_]
    changed_, mtime_only, new_infected = [], [], []

    # -- What the site knows: has it changed? ------------------------------
    for rel, row in known.items():
        if rel not in on_disk:
            continue
        size, mtime = on_disk[rel]
        same_ = (row["origin_bytes"] == size
                  and abs((row["origin_mtime"] or 0) - mtime) < 1)
        if same_ and kind == "quick":
            continue                     # ⚠ NO hash is computed here
        sha = library.sha256_of(root / rel)
        if sha == row["origin_sha256"]:
            if not same_:
                mtime_only.append((size, mtime, row["id"]))
            # ⚠ On a deep run an unchanged file is scanned again too: virus
            # signatures are added every day, and a file that was clean
            # yesterday can be recognised tomorrow. If it is no longer clean
            # it is taken off the site and marked as infected.
            if kind == "deep" and row["state"] == "ok":
                verdict, detail = av.scan(root / rel)
                if verdict == av.INFECTED:
                    new_infected.append((row, detail))
            continue
        changed_.append((row, sha, size, mtime))

    # -- What is in the database and no longer on the disk -----------------
    suspect = [row for rel, row in known.items()
                  if rel not in on_disk and row["state"] != "missing"]

    # -- Moved: the same hash on a new path ---------------------------------
    # This has to come BEFORE "missing", otherwise a photograph that was just
    # dragged into another folder counts as a loss -- and the mass brake would
    # fire on a simple tidy-up.
    new_hash = {}
    if suspect and new_paths:
        for rel in new_paths:
            try:
                new_hash.setdefault(library.sha256_of(root / rel), rel)
            except OSError:
                continue
    moved_ = []
    for_huge = []
    for row in suspect:
        rel_new = new_hash.get(row["origin_sha256"] or "")
        if rel_new:
            moved_.append((row, rel_new))
        else:
            for_huge.append(row)
    moved_to = {rel for _, rel in moved_}
    new_paths = [r for r in new_paths if r not in moved_to]

    # -- Bremse 2: d'Massen-Bremse -----------------------------------------
    total_ = len(known) or 1
    pct = 100.0 * len(for_huge) / total_
    too_many = (len(for_huge) > config.SCAN_MAX_MISSING_ABS
               or pct > config.SCAN_MAX_MISSING_PCT)
    if for_huge and too_many and not confirm_missing:
        # Per folder, not per photograph: if one folder with 400 photographs
        # disappears, that is ONE case and not 400.
        dirs = sorted({_folder(r["origin_path"]) for r in for_huge})
        grounds = (f"{len(for_huge)} photographs ({pct:.1f} %) are missing, "
                 f"across {len(dirs)} folder{'' if len(dirs) == 1 else 's'} — "
                 f"the sync stopped and changed nothing. Is the share alright?")
        log.error("sync stopped: %s", grounds)
        return result_(halted=grounds, missing=len(for_huge),
                       folders=dirs[:20], new=0, changed=0, moved=0)

    # ======================================================================
    #  From here on it writes
    # ======================================================================
    n_new = n_changed = n_moved = n_missing = n_restored = 0

    if mtime_only:
        with db.tx() as c:
            c.executemany("UPDATE photos SET origin_bytes=?, origin_mtime=? WHERE id=?",
                          mtime_only)

    # 1. Moved -- only follow the paths, NOTHING else.
    #    The photograph keeps its id, and with it title, rating, tags, people.
    for row, rel_new in moved_:
        _move_photo(row, rel_new)
        n_moved += 1

    # 2. Changed -- convert again, empty the cache, `rev` up.
    for row, sha, size, mtime in changed_:
        with db.tx() as c:
            c.execute("UPDATE photos SET origin_sha256=?, origin_bytes=?, "
                      "origin_mtime=?, state='changed', rev=rev+1 WHERE id=?",
                      (sha, size, mtime, row["id"]))
        # ⚠ `rev` MUST go up: the image addresses carry it as ?v=, and they
        # are cached `immutable`. Without it a browser would keep showing the
        # old photograph for a year.
        shutil.rmtree(convert.derivative_dir(row["id"]), ignore_errors=True)
        enqueue("convert", str(row["id"]))
        n_changed += 1

    # Newly detected viruses: take it off the site, master away, mark it
    # infected. The original is NOT deleted -- that belongs to its owner.
    for row, detail in new_infected:
        shutil.rmtree(convert.derivative_dir(row["id"]), ignore_errors=True)
        if row["web_name"]:
            (config.WEB_DIR / row["web_name"]).unlink(missing_ok=True)
        with db.tx() as c:
            c.execute("UPDATE photos SET state='infected', note=? WHERE id=?",
                      (f"virus scan (deep): {detail}"[:200], row["id"]))
        log.warning("deep run: %s now detected as a virus -- taken off the site",
                    row["origin_path"])
    n_infected = len(new_infected)

    # 3. Nei
    for rel in sorted(new_paths):
        if scan.adopt(root / rel, rel) is not None:
            n_new += 1

    # 4. Missing -- off the site, but the row stays
    for row in for_huge:
        where_ = _trash(row["web_name"] or "")
        shutil.rmtree(convert.derivative_dir(row["id"]), ignore_errors=True)
        with db.tx() as c:
            c.execute("UPDATE photos SET state='missing', "
                      "missing_since=datetime('now') WHERE id=?", (row["id"],))
        log.info("feelt: %s (Master -> %s)", row["origin_path"], where_ or "no master")
        n_missing += 1

    # 5. Back -- a photograph that stood as "missing" and whose path is there again
    for rel, row in known.items():
        if row["state"] == "missing" and rel in on_disk:
            if _untrash(row["web_name"] or ""):
                with db.tx() as c:
                    c.execute("UPDATE photos SET state='ok', missing_since=NULL, "
                              "rev=rev+1 WHERE id=?", (row["id"],))
            else:
                with db.tx() as c:
                    c.execute("UPDATE photos SET state='new', missing_since=NULL, "
                              "rev=rev+1 WHERE id=?", (row["id"],))
                enqueue("convert", str(row["id"]))
            n_restored += 1

    # New places onto the map. As a job -- the sync should not wait a second
    # per place.
    enqueue("geocode", "")

    binned = empty_trash()
    # While we are here: orphaned upload folders and expired quarantines.
    # Both grow quietly otherwise.
    from . import shares, upload as _up
    orphaned = _up.purge_orphans()
    expired_ = shares.purge_expired()
    log.info("sync %s: %d new, %d changed, %d moved, %d missing, %d back",
             kind, n_new, n_changed, n_moved, n_missing, n_restored)
    return result_(new=n_new, changed=n_changed, moved=n_moved,
                   missing=n_missing, restored=n_restored, infected=n_infected,
                   seen=len(on_disk),
                   bin=binned, orphans=orphaned, expired_shares=expired_)


def _move_photo(row: dict, rel_new: str) -> None:
    """A photograph got a new path -- and the site follows.

    The master is moved to the mirrored path in the web tree, and the album
    fields come out of the new path. The id stays -- and with it everything
    that hangs off it.
    """
    alt_web = row["web_name"] or ""
    new_web = str(convert.web_path_for(rel_new).relative_to(config.WEB_DIR))
    if alt_web and alt_web != new_web:
        src = config.WEB_DIR / alt_web
        dst = config.WEB_DIR / new_web
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.move(str(src), str(dst))
    jar, country_, event = scan.parts_of(Path(rel_new))
    with db.tx() as c:
        c.execute(
            "UPDATE photos SET origin_path=?, web_name=?, "
            "  master_source_path=?, album_year=?, country=?, event=? WHERE id=?",
            (rel_new, new_web, str(config.ORIGIN_DIR / rel_new),
             (jar if (jar or "").isdigit() and len(jar or "") == 4 else None),
             country_ or None, event or None, row["id"]))
    # The block list has to come along, or a photograph taken off the site returns.
    with db.tx() as c:
        c.execute("UPDATE removed SET origin_path=? WHERE origin_root='my_photos' "
                  "AND origin_path=?", (rel_new, row["origin_path"]))
    log.info("moved: %s -> %s", row["origin_path"], rel_new)


def last_runs(n: int = 10) -> list:
    return [dict(r) for r in db.connect().execute(
        "SELECT * FROM scans ORDER BY id DESC LIMIT ?", (n,))]


def pending_halt() -> dict:
    """The last run, if it was stopped -- for the button on the admin page."""
    row = db.connect().execute(
        "SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    if row and row["halted_reason"]:
        return dict(row)
    return {}


# ---------------------------------------------------------------------------
#  As a job, not inside the request
# ---------------------------------------------------------------------------
from .worker import handler                                     # noqa: E402


@handler("sync")
def _job(job) -> None:
    """⚠ The worker hands the handler the WHOLE job row, not the payload -- see
    `convert(job)`, which does `int(job["payload"])`. Expecting a string here
    gave a clean AttributeError: `'sqlite3.Row' object has no attribute
    'rstrip'`.

    The payload is `quick`, `deep`, `quick!` or `deep!` -- the "!" is the
    operator's "yes, that was me".

    A run through 30,000 photographs takes time; that is why it hangs off the
    worker and not off the HTTP request, where the browser would time out
    anyway.
    """
    payload = job["payload"] or "quick"
    run(payload.rstrip("!"), confirm_missing=payload.endswith("!"))
