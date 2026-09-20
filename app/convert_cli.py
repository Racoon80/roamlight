"""Converting files that came from outside the house -- in a process of its own.

    python3 -m app.convert_cli          # runs until it is stopped

⚠ **Why this exists.** Everything slow used to happen in the site's own
  process: the library scan, the geocoding, and the conversion of whatever
  somebody had just uploaded. That last one is not like the others. Building a
  master out of an uploaded file means handing bytes that a stranger chose to
  libheif, to LibRaw, to ffmpeg -- decoders written in C, and the class of
  program that has a memory-safety advisory most months.

  And the site's own process has `/mnt/my-photos` mounted **read-write**. That
  folder is the family's only copy of their originals; there is no second one.
  So a hole in a decoder was one step away from the one thing in this whole
  system that cannot be rebuilt. Two independent security reviews put that at
  the top of their list, both times.

  Nothing here makes the decoders safer. What it does is take the originals out
  of reach of the process that runs them: this unit does not have that tree
  mounted at all (`deploy/family-convert.service`), so there is nothing there
  to damage even if the decode goes wrong.

⚠ **The same code, not a copy.** It runs `app.convert.convert`, the same
  function as the site, off the same queue table. What differs is which job
  kinds it takes (`convert-upload`, and nothing else) and what the process can
  reach. A second implementation would drift from the first, and the drift
  would be in the half nobody looks at.

⚠ **No `db.init()` here.** The schema belongs to the site, which runs the
  migrations at startup; two processes migrating the same database at the same
  moment is a way to lose it. The unit is ordered `After=family.service` for
  the same reason.
"""
import logging
import signal
import sys
import time


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(message)s", stream=sys.stdout)
    log = logging.getLogger("family")

    from . import config, convert as _convert    # noqa: F401  (registers the handler)
    from . import worker as w

    # ⚠ Only the scratch space and the derivatives -- NOT `ensure_marker` on the
    #   originals, which this process cannot see and must not try to write to.
    config.ensure_dirs()

    kinds = list(w.FOREIGN_KINDS)
    # ⚠ Ours and only ours. The site sweeps the rest; sweeping everything from
    #   here would reset a library conversion the site is in the middle of.
    orphans = w.requeue_orphans(kinds=kinds)
    if orphans:
        log.warning("%d unfinished upload conversion(s) put back on the queue", orphans)

    hand = w.Worker(kinds=kinds, notices=False)
    hand.start()
    log.info("convert: waiting for %s", ", ".join(kinds))

    stop = {"now": False}

    def bye(signum, frame):                                      # noqa: ARG001
        stop["now"] = True

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)

    while not stop["now"]:
        time.sleep(1)
        st = hand.status()
        if not st["running"]:
            log.error("convert: the worker threads are gone -- %s", st["last_error"])
            return 1

    # ⚠ Ask the threads to stop and give a conversion in flight a moment to
    #   finish. One that does not finish is put back by the next start, so the
    #   worst case is that it runs twice, not that it is lost.
    hand.stop()
    for _ in range(100):
        if not hand.status()["busy"]:
            break
        time.sleep(0.1)
    log.info("convert: stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
