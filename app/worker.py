"""The background queue.

Everything slow happens here and not in a request: converting a photograph,
walking the library, looking up places on the map. A handful of threads take
jobs off a table in SQLite; a job that fails comes back a few minutes later,
five times, and then stops."""
import logging
import threading
import time
import traceback

from . import config, db

log = logging.getLogger("family")

HANDLERS = {}


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def enqueue(kind: str, payload: str = "") -> int:
    """Add a job -- but never the same one twice.

    ⚠ The same job arrives from several directions: the sync says "changed", a
    scan says "new", somebody accepts a guest photograph. Without this check
    two threads work on the same photograph, get in each other's way, and the
    loser tries again five minutes later. If the same kind with the same
    payload is already `pending` or `running`, no second one is created.
    """
    with db.tx() as conn:
        do = conn.execute(
            "SELECT id FROM jobs WHERE kind=? AND payload=? "
            "AND status IN ('pending','running')", (kind, payload)).fetchone()
        if do:
            return do["id"]
        return conn.execute(
            "INSERT INTO jobs (kind, payload) VALUES (?, ?)", (kind, payload)
        ).lastrowid


def requeue_orphans(kinds=None, exclude=None) -> int:
    """Put jobs that were left on `running` back to `pending`.

    ⚠ A job that was running when the service stopped stays on `running` -- and
    the worker only ever looks for `pending`. So it is never picked up again.
    Found the hard way: one conversion hung like that, and the photograph sat
    at "new" and never appeared on the site.

    ⚠ ONLY this process's own kinds, and that is new. The old version swept
      every `running` row and said so in a comment: "there is only ever one
      instance, so this cannot interrupt another process's work". That stopped
      being true the day the uploads moved into `family-convert.service`. A
      restart of the site would have reset a conversion that the OTHER process
      was in the middle of -- and then both would have been working on the same
      photograph, one of them writing a master that the other had already
      replaced.
    """
    where, args = "", []
    if kinds:
        where = " AND kind IN (%s)" % ",".join("?" for _ in kinds)
        args = list(kinds)
    elif exclude:
        where = " AND kind NOT IN (%s)" % ",".join("?" for _ in exclude)
        args = list(exclude)
    with db.tx() as conn:
        n = conn.execute(
            "UPDATE jobs SET status='pending', updated_at=datetime('now'), "
            "last_error='the service was restarted' WHERE status='running'" + where,
            args).rowcount
    return n or 0


# ⚠ The kinds the SITE's own worker leaves alone. A photograph somebody
#   uploaded is a file from outside the house, and decoding it means handing a
#   stranger's bytes to libheif, LibRaw or ffmpeg. That must not happen in the
#   process that has the family's only copy of the originals mounted `rw`.
#
#   So it happens in `family-convert.service` instead -- the same code, the
#   same queue, a different process with `/mnt/my-photos` not mounted at all.
#   See `app/convert_cli.py`.
FOREIGN_KINDS = ("convert-upload",)


class Worker:
    def __init__(self, kinds=None, notices: bool = True) -> None:
        """`kinds` = the job kinds this worker may take. `None` = everything
        except what belongs to another process (`FOREIGN_KINDS`).

        ⚠ The filter has to be in the SQL that takes the job, not in a check
          afterwards. `_one()` marks a row `running` the moment it picks it up;
          a worker that took a job it cannot handle would have to put it back,
          and the put-back path defers it by an hour. With two processes on one
          queue that would mean every upload waits an hour for the wrong
          worker to hand it over.
        """
        self._kinds = tuple(kinds) if kinds else None
        self._do_notices = notices
        self._stop = threading.Event()
        self._threads = []
        self._lock = threading.Lock()
        self._last_error = None
        self._done = 0
        self._busy = 0

    def _where(self):
        """The kind condition for the taking query, and its parameters."""
        if self._kinds:
            marks = ",".join("?" for _ in self._kinds)
            return f"AND kind IN ({marks})", list(self._kinds)
        if FOREIGN_KINDS:
            marks = ",".join("?" for _ in FOREIGN_KINDS)
            return f"AND kind NOT IN ({marks})", list(FOREIGN_KINDS)
        return "", []

    def start(self) -> None:
        if any(t.is_alive() for t in self._threads):
            return
        self._stop.clear()
        self._threads = []
        for i in range(max(1, config.WORKERS)):
            th = threading.Thread(target=self._loop, name=f"family-worker-{i}",
                                  daemon=True)
            th.start()
            self._threads.append(th)
        # ⚠ Its own thread, and a slow one. The notices are not work to get
        #   through -- they are work to hold back: the whole point is that a row
        #   sits for a while and collects. Putting it in the job queue would
        #   mean a job per photograph, which is exactly what it exists to avoid.
        #
        # ⚠ And only in the SITE's worker. Two processes flushing the same
        #   window would send the same notice twice.
        if self._do_notices:
            post = threading.Thread(target=self._notices, name="family-notices",
                                    daemon=True)
            post.start()
            self._threads.append(post)

    def _notices(self) -> None:
        from . import notify
        while not self._stop.is_set():
            # Every fifteen seconds: nothing to do most times, and never later
            # than that once a window has passed.
            if self._stop.wait(15):
                return
            try:
                tally = notify.flush()
                if tally["sent"] or tally["failed"]:
                    log.info("Bescheed: %s", tally)
            except Exception:                                    # noqa: BLE001
                log.warning("notify: flush failed", exc_info=True)
        log.info("Worker: %d Threads", len(self._threads))

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        alive = [t for t in self._threads if t.is_alive()]
        return {
            "running": bool(alive),
            "threads": len(alive),
            "busy": self._busy,
            "done": self._done,
            "last_error": self._last_error,
        }

    def _loop(self) -> None:
        while not self._stop.wait(config.WORKER_POLL_SECONDS):
            try:
                self._one()
            except Exception:
                self._last_error = traceback.format_exc(limit=3)

    def _one(self) -> None:
        conn = db.connect()
        # ⚠ A job may be taken exactly once. With more than one thread,
        # "find one, then mark it" is a race: two threads read the same row and
        # both rotate the same photograph. So it is a single statement that
        # marks AND returns -- SQLite can do that with `RETURNING` (3.35 and
        # later). The row that comes back belongs to this thread and to nobody
        # else.
        kind_sql, kind_args = self._where()
        with db.tx() as c:
            row = c.execute(
                "UPDATE jobs SET status='running', attempts=attempts+1, "
                "  updated_at=datetime('now') "
                "WHERE id = (SELECT id FROM jobs WHERE status='pending' "
                "            AND (run_after IS NULL OR run_after <= datetime('now')) "
                f"           {kind_sql} "
                "            ORDER BY id LIMIT 1) "
                "RETURNING *", kind_args).fetchone()
        if row is None:
            return
        fn = HANDLERS.get(row["kind"])
        if fn is None:
            # No handler for this kind: that is a feature that is not built
            # yet, not an error. The job waits and starts running by itself as
            # soon as the handler exists -- an hour apart keeps the queue
            # quiet in the meantime.
            conn.execute(
                "UPDATE jobs SET status='pending', attempts=attempts-1, last_error=?, "
                "run_after=datetime('now','+1 hour'), updated_at=datetime('now') "
                "WHERE id=?", (f"job type {row['kind']!r} is not built yet", row["id"]),
            )
            return
        with self._lock:
            self._busy += 1
        try:
            fn(row)
        except Exception:
            err = traceback.format_exc(limit=5)
            # ⚠ `attempts` was already raised by the UPDATE that took the job
            # -- so do NOT count it again here. It used to turn five attempts
            # into four.
            status = "error" if row["attempts"] >= config.JOB_MAX_ATTEMPTS else "pending"
            conn.execute(
                "UPDATE jobs SET status=?, last_error=?, "
                "run_after=datetime('now','+5 minutes'), updated_at=datetime('now') "
                "WHERE id=?", (status, err, row["id"]),
            )
            self._last_error = err
        else:
            conn.execute(
                "UPDATE jobs SET status='done', last_error=NULL, "
                "updated_at=datetime('now') WHERE id=?", (row["id"],)
            )
            with self._lock:
                self._done += 1
        finally:
            with self._lock:
                self._busy -= 1
