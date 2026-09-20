"""The administrator's account, from a terminal.

    python -m app.cli user add guy --admin
    python -m app.cli user passwd guy
    python -m app.cli user list

⚠ Why this exists. Without it the first account is made on `/setup`, and until
  somebody makes it that page is OPEN -- whoever reaches the address first
  becomes the administrator of a family's photographs. On a machine you own
  that window is short and harmless; on a network with other people on it it is
  a door standing open, and the only lock was a token you had to think of
  setting BEFORE the first start. Making the account from the terminal shuts
  the door instead of guarding it: the moment there is an account, `/setup` is
  gone (`main.py`).

⚠ The password is never a command-line argument. `ps` shows the arguments of
  every process on the machine to every user on it, and the shell writes them
  into a history file. So: asked for, twice, not echoed -- or fed in on stdin
  for a script, where it is on a pipe and not in the process table.

⚠ Same functions as the web form, on purpose (`auth.create_user`,
  `auth.set_password`): argon2id, the same minimum length, the same group
  names. A second way in must not be a weaker way in.
"""
import argparse
import getpass
import sys

from . import auth, config, db


def _read_password(stdin: bool) -> str:
    if stdin:
        # One line, and the newline goes. A password with a trailing space is
        # a password; a trailing newline from `echo` is not part of it.
        pw = sys.stdin.readline().rstrip("\n")
        if not pw:
            sys.exit("nothing came in on stdin")
        return pw
    if not sys.stdin.isatty():
        sys.exit("no terminal to ask on -- use --password-stdin")
    pw = getpass.getpass("password: ")
    if pw != getpass.getpass("and again: ") :
        sys.exit("the two passwords are not the same")
    return pw


def _groups(admin: bool) -> list:
    """The group names from the configuration, not words typed here.

    ⚠ `sorted(ADMIN_GROUPS)[0]` is exactly what `/setup` uses. Anything else
      and an account made here would carry a group this installation does not
      know, and the person would sign in to a site that shows them nothing.
    """
    if admin:
        return [sorted(config.ADMIN_GROUPS)[0] if config.ADMIN_GROUPS else "admin"]
    return [sorted(config.VIEWER_GROUPS)[0] if config.VIEWER_GROUPS else "family"]


def _existing(name: str):
    row = db.connect().execute(
        "SELECT username, is_local, password_hash IS NOT NULL AS has_pw, groups_json "
        "FROM members WHERE username=?", ((name or "").strip().lower(),)).fetchone()
    return row


def cmd_add(a) -> int:
    # ⚠ `auth.create_user` is an upsert -- it overwrites the groups and sets
    #   `is_local=1`. Run by hand on a name that already exists that is not a
    #   convenience, it is a quiet demotion: `roamlight-user add guy` (no
    #   --admin) takes an administrator's groups away, and on a site whose
    #   people come from the directory it turns a directory account into a
    #   password account. Neither says a word about what it did. So: refused,
    #   with the command that was actually meant.
    row = _existing(a.name)
    if row is not None and not a.force:
        how = "a password" if row["has_pw"] else "the directory"
        sys.exit(
            f"'{row['username']}' already exists here and signs in with {how}.\n"
            f"  To change the password:  roamlight-user passwd {row['username']}\n"
            f"  To overwrite the account and its groups anyway: add --force")
    first = not auth.has_local_users()
    pw = _read_password(a.password_stdin)
    try:
        auth.create_user(a.name, pw, a.display_name or "", groups=_groups(a.admin))
    except ValueError as exc:
        sys.exit(str(exc))
    role = "administrator" if a.admin else "viewer"
    print(f"{a.name.strip().lower()}: {role} ({', '.join(_groups(a.admin))})")
    if first:
        print("/setup is shut now -- there is an account.")
    elif a.admin:
        print("⚠ a second administrator. They see and may change everything.")
    return 0


def cmd_passwd(a) -> int:
    pw = _read_password(a.password_stdin)
    try:
        auth.set_password(a.name, pw)
    except ValueError as exc:
        sys.exit(str(exc))
    # ⚠ Every session of that person ends. A password is changed either because
    #   it was forgotten or because somebody else might have it -- and in the
    #   second case leaving the old sessions alive changes nothing at all.
    n = auth.end_all(a.name.strip().lower())
    print(f"{a.name.strip().lower()}: password changed"
          + (f", {n} session(s) ended" if n else ""))
    return 0


def cmd_list(a) -> int:
    rows = db.connect().execute(
        "SELECT username, display_name, groups_json, is_local, active, "
        "       password_hash IS NOT NULL AS has_pw "
        "FROM members ORDER BY username").fetchall()
    if not rows:
        print("nobody yet")
        return 0
    import json
    for r in rows:
        try:
            groups = ", ".join(json.loads(r["groups_json"] or "[]")) or "—"
        except (ValueError, TypeError):
            groups = "?"
        how = "password" if r["has_pw"] else "directory"
        state = "" if r["active"] else "  (switched off)"
        print(f"{r['username']:<20} {how:<10} {groups}{state}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli",
                                 description="accounts for this site")
    sub = ap.add_subparsers(dest="what", required=True)
    user = sub.add_parser("user", help="accounts").add_subparsers(
        dest="do", required=True)

    add = user.add_parser("add", help="make an account, or set the password of one")
    add.add_argument("name")
    add.add_argument("--admin", action="store_true",
                     help="sees and manages everything; without it, may look")
    add.add_argument("--display-name", default="", help="the name shown on the site")
    add.add_argument("--password-stdin", action="store_true",
                     help="read the password from stdin instead of asking")
    add.add_argument("--force", action="store_true",
                     help="overwrite an account that already exists, and its groups")
    add.set_defaults(fn=cmd_add)

    pw = user.add_parser("passwd", help="change a password and end that person's sessions")
    pw.add_argument("name")
    pw.add_argument("--password-stdin", action="store_true")
    pw.set_defaults(fn=cmd_passwd)

    ls = user.add_parser("list", help="who is known here")
    ls.set_defaults(fn=cmd_list)

    a = ap.parse_args(argv)
    # ⚠ The schema, before anything is written. This runs on a machine where
    #   the site may never have started -- installing and making the
    #   administrator in one go is the whole point.
    config.ensure_dirs()
    db.init()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
