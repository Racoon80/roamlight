"""Where the photographs and the database are -- for the tests.

⚠ NOTHING IS GUESSED HERE. A default path that happens to fit somebody else's
  installation is dangerous: the check that keeps a test from touching real
  photographs would then be looking in the wrong tree and let everything
  through. So: from the environment, else from the file the service reads, and
  otherwise the test does not run at all.

  That is exactly what happened once: the fallback was changed from one
  installation's mount point to a generic one, and afterwards the tests worked
  on a tree that does not exist -- while the guard noticed nothing.
"""
import os
import sys

_ENV_FILES = ("/etc/family/env", "/opt/roamlight/roamlight.env")


def _from_file(var):
    for path in _ENV_FILES:
        try:
            for line in open(path):
                line = line.strip()
                if line.startswith(var + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return None


def need(var):
    """The value -- or the test does not run at all."""
    v = os.environ.get(var) or _from_file(var)
    if not v:
        sys.exit(f"ABORTED: {var} is not set, and none of {_ENV_FILES} names it.\n"
                 f"  Run the test with the service's environment, for example:\n"
                 f"    set -a; . /etc/family/env; set +a; python3 <test>")
    return v


def connect(path=None):
    """A connection to the database -- ALWAYS with foreign keys ON.

    ⚠ `sqlite3.connect()` turns them OFF. A test that deletes photographs over
      such a connection leaves the rows in `album_photos`, `upload_files` and
      `share_hits` dangling -- and then a perfectly ordinary action on the site
      dies with a 500 (`FOREIGN KEY constraint failed`). That is what happened
      to the live database: ten orphaned rows, and deleting a collection blew
      up. The guard meant to protect the data was damaging it.

    ⚠ NOTHING ELSE is changed here -- above all no `row_factory`. A test that
      compares `rows == [("Porto",)]` suddenly gets objects instead of tuples
      with `sqlite3.Row` and goes red while nothing is wrong with the site.
      Whoever wants `Row` sets it themselves, as before.
    """
    import sqlite3
    con = sqlite3.connect(path or need("FAMILY_DB"), timeout=30)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con
