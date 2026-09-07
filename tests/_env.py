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

_ENV_FILES = tuple(x for x in (os.environ.get("FAMILY_ENV"),
                               "/etc/family/env",
                               "/opt/roamlight/roamlight.env") if x)


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


def load():
    """Put the service's environment into ours -- once, at import.

    ⚠ Without this, `_env` and `app.config` can disagree: `_env` reads the file,
      `config` reads `os.environ`. A test started without the environment then
      talks to one database while checking another -- or, worse, `config` falls
      back to its defaults and quietly makes an empty one somewhere else. That
      is not a failure you read off the screen; it is a test that passes against
      nothing.

    Values already in the environment win: a caller can still override.
    """
    import os
    for path in _ENV_FILES:
        try:
            for line in open(path):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except OSError:
            continue


def only_on_a_test_instance():
    """Refuse to run anywhere that has not said, in writing, that it is a test.

    ⚠ THIS EXISTS BECAUSE IT WENT WRONG. The suite has been pointed at the
      family's real instance three times. Once it deleted thirteen photographs
      that had just been uploaded (they were still on the file server, but the
      site showed nothing). Another time it left seven invented people —
      Ada, Ben, Cleo, Dev, Eve, Finn — sitting in the real list of family
      members, where they looked like accounts somebody had made.

      Every one of those was a careless environment away. The guards that
      existed were inside the tests: a year of "1999", a country of "Testland",
      a clean-up that only touches those. They are good guards and they are not
      enough, because they only work AFTER the suite has decided to run.

      So the instance has to opt in. `FAMILY_TEST=1` goes in the environment
      file of a test instance and nowhere else. A real installation cannot fail
      this check by accident — it can only fail it by somebody writing the line.
    """
    import os
    if str(os.environ.get("FAMILY_TEST", "")).strip().lower() in ("1", "true", "yes", "on"):
        return
    sys.exit(
        "\nREFUSED: this does not look like a test instance.\n\n"
        "  The acceptance tests write into the database and into the photo\n"
        "  trees. They must never run against the instance a family actually\n"
        "  uses.\n\n"
        "  If this one IS for testing, put this line in its environment file\n"
        "  and start the service again:\n\n"
        "      FAMILY_TEST=1\n\n"
        f"  (read from: {', '.join(_ENV_FILES)})\n")


load()
only_on_a_test_instance()


def need(var):
    """The value -- or the test does not run at all."""
    v = os.environ.get(var) or _from_file(var)
    if not v:
        sys.exit(f"ABORTED: {var} is not set, and no environment file names it.\n"
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


def app_root():
    """Where the program lives -- so that a test runs on ANY instance.

    ⚠ `/opt/family/app` used to be hard-coded. With that the suite could only
      run on the one machine -- which means every test ran against the live
      library, including the one that re-hashes every photograph. `FAMILY_BASE`
      says where it is; without it the old path stands.
    """
    import os
    return os.environ.get("FAMILY_BASE") or _from_file("FAMILY_BASE") or "/opt/family/app"


def python():
    """The interpreter the site itself uses -- it has pyvips and Pillow."""
    import os
    import shutil
    for c in ("/opt/family/venv/bin/python", "/opt/roamlight/venv/bin/python3"):
        if os.access(c, os.X_OK):
            return c
    return shutil.which("python3") or "python3"


_TOKENS = {}


def headers(user=None, groups=None):
    """Headers that make a request THIS person -- on ANY instance.

    ⚠ There are two ways in, and a test must not care which one an installation
      uses. Behind an identity proxy it is the three headers plus the shared
      secret. With local accounts there is no secret and no proxy -- but there
      IS a device token, the road the phone app takes, and it carries exactly
      the same identity. So a test asks for a person and gets whichever road
      exists here.

      Before this, every test read /etc/family/proxy-secret and died on any
      installation without one -- which is every Docker and every plain LXC.
      That is why the whole suite could only ever run against the live library.

    `user=None` means "nobody": no headers at all. That is a guest.
    """
    import sys
    sys.path.insert(0, app_root())
    from app import config
    if user:
        _invented.add(str(user))
    if config.AUTH_PROXY:
        secret = open(config.PROXY_SECRET_FILE).read().strip()
        h = {"X-Family-Proxy": secret}
        if user:
            h["X-authentik-username"] = user
            h["X-authentik-groups"] = groups or sorted(config.ADMIN_GROUPS)[0]
        return h
    if not user:
        return {}
    # ⚠ Local accounts are kept in lower case (`auth.create_user` folds them),
    #   but a device pairing is looked up as it is written. Asking for "Eve" and
    #   getting a token for a member row called "eve" means the token names
    #   nobody -- and every request comes back 403 with no hint why.
    user = user.strip().lower()
    if user not in _TOKENS:
        from app import auth, devices
        con = connect()
        row = con.execute("SELECT username FROM members WHERE username=? AND active=1",
                          (user,)).fetchone()
        con.close()
        want = [groups or sorted(config.ADMIN_GROUPS)[0]]
        if row is None:
            # ⚠ The account is made with the groups the test asked for -- and
            #   `zz-` names are what the tests clean up afterwards.
            import secrets as _s
            try:
                auth.create_user(user, _s.token_urlsafe(24), display_name=user,
                                 groups=want)
            except ValueError:
                pass
        else:
            # ⚠ With local accounts the groups come from the MEMBER, not from
            #   the request -- so asking for a different role means the record
            #   has to say so. Behind a proxy the header decides and this does
            #   not happen; the test must not have to know the difference.
            import json as _j
            con2 = connect()
            con2.execute("UPDATE members SET groups_json=? WHERE username=?",
                         (_j.dumps(want), user))
            con2.commit(); con2.close()
        code = devices.new_pairing(user)["code"]
        got = devices.redeem(code, "acceptance test")
        if not got:
            sys.exit(f"ABORTED: no way to act as {user!r} on this installation")
        _TOKENS[user] = got["token"]
    return {"Authorization": "Bearer " + _TOKENS[user]}


# --- Wat d'Tester erfonnt hunn, raumen se och op -----------------------------
#
# ⚠ Every request the suite makes leaves a row in `members`: the gate writes
#   down whoever it just saw, so that an administrator has a list of people even
#   when the directory is unreachable. That is right for the site and wrong for
#   a test -- the invented people (Ada, Ben, Cleo…) then sit in the list looking
#   like accounts somebody made. They were found there once, on the family's own
#   instance, and had to be picked out by hand.
#
#   So: whoever `headers()` invented is taken out again when the test ends. Only
#   rows that were NEVER seen in the directory and have no password -- a real
#   person is never touched, even if a test happened to borrow their name.
_invented = set()


def _forget_invented():
    if not _invented:
        return
    try:
        conn = connect(need("FAMILY_DB"))
        conn.executemany(
            "DELETE FROM members WHERE username = ? AND seen_in_authentik = 0 "
            "AND is_local = 0 AND (password_hash IS NULL OR password_hash = '')",
            [(u,) for u in sorted(_invented)])
        conn.commit()
        conn.close()
    except Exception:                                            # noqa: BLE001
        # A clean-up must never turn a passing test into a failing one.
        pass


import atexit                                                    # noqa: E402
atexit.register(_forget_invented)
