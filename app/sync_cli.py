"""Keeping the library and the site in step, from the command line -- this is
what the timer calls.

    FAMILY_SYNC_KIND=quick|deep  python3 -m app.sync_cli

It runs in its **own process**, not inside the site. Whatever it finds is put
on the job queue; the main service does the converting.
"""
import logging
import os
import sys


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stdout)
    from . import db, sync
    db.init()
    kind = os.environ.get("FAMILY_SYNC_KIND", "quick")
    if kind not in ("quick", "deep"):
        print(f"unknown kind: {kind!r}", file=sys.stderr)
        return 2
    res = sync.run(kind)
    for k, v in res.items():
        print(f"  {k}: {v}")
    # ⚠ A halted run exits non-zero. Otherwise `systemctl status` would say
    # "success" while the sync had stopped itself -- and that is exactly the
    # case somebody needs to see.
    return 1 if res.get("halted") else 0


if __name__ == "__main__":
    sys.exit(main())
