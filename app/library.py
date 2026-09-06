"""The ONLY file that touches files in your library.

    store_original()  in the originals tree: create NEW files only. No unlink,
                      no rename, no writing to a path that already exists.
    move_web()        in the served tree: move and rename freely -- the site
                      owns that one.

The rule "never overwrite" lives here and nowhere else. Whoever changes it
changes it in one place, and that is the whole point of the arrangement.
"""
import hashlib
import os
import re
import unicodedata
from pathlib import Path

from . import config

CHUNK = 4 * 1024 * 1024
_EXT = re.compile(r"^[A-Za-z0-9]{1,8}$")


class LibraryError(RuntimeError):
    pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def check_tree(root: Path) -> None:
    """A share that failed to mount looks exactly like an empty folder. The
    site would happily write to the local disk, and the next sync would decide
    every photograph had been deleted. So: a real mount point AND a marker
    file."""
    if not root.is_dir():
        raise LibraryError(f"{root} does not exist")
    if config.REQUIRE_MOUNT and not os.path.ismount(root):
        raise LibraryError(f"{root} is not a mount point")
    if not (root / config.MARKER_NAME).is_file():
        raise LibraryError(f"{root}/{config.MARKER_NAME} is missing -- not mounted?")


def safe_ext(original_name: str, fallback: str = "bin") -> str:
    ext = Path(unicodedata.normalize("NFC", original_name or "")).suffix.lstrip(".")
    return ext.lower() if _EXT.match(ext or "") else fallback


def build_name(taken_at: str, seq: int, ext: str) -> str:
    """The site invents the stored file name ITSELF.

    ⚠ A name from a client never reaches the file system: no `..`, no NUL
    bytes, no right-to-left mark that makes a `.php` look like a `.jpg`, no
    name starting with `-` that would become an option to a command line tool.
    The original name is kept in the database and shown on the site.
    """
    stamp = (taken_at or "").replace("-", "").replace(":", "").replace(" ", "-")[:15]
    if len(stamp) != 15:
        stamp = "00000000-000000"
    return f"{stamp}_{seq:04d}.{ext}"


def _exists_ci(folder: Path, name: str) -> bool:
    """Collision check that ignores upper and lower case.

    ⚠ SMB is case-insensitive: `IMG_1.CR3` and `img_1.cr3` are ONE file on
    disk but two rows in the database. That is exactly the collision a plain
    `exists()` walks straight past."""
    target = name.casefold()
    try:
        return any(p.name.casefold() == target for p in folder.iterdir())
    except OSError:
        return False


def store_original(src: Path, folder: Path, name: str, expect_sha: str = None) -> dict:
    """Put a NEW file into the originals tree. Returns {path, sha256, bytes}.

    The order matters:
      1. check the tree (mount point + marker)
      2. create the target folder if it is missing
      3. check for a collision -- case-insensitively
      4. open with O_CREAT|O_EXCL: the only check that cannot be lost in the
         gap between `exists()` and `open()`
      5. write, then read the file BACK off the share and compare sha256
      6. if it does not match: remove our copy and fail -- the upload stays
         where it was
    """
    check_tree(config.ORIGIN_DIR)
    try:
        folder.relative_to(config.ORIGIN_DIR)
    except ValueError:
        raise LibraryError(f"{folder} is not inside {config.ORIGIN_DIR}")

    folder.mkdir(parents=True, exist_ok=True)
    if _exists_ci(folder, name):
        raise LibraryError(f"{name} is already there (ignoring case)")

    dst = folder / name
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    written = 0
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            while True:
                b = inp.read(CHUNK)
                if not b:
                    break
                out.write(b)
                written += len(b)
            out.flush()
            os.fsync(out.fileno())
    except Exception:
        dst.unlink(missing_ok=True)   # only our own, just-written copy
        raise

    got = sha256_of(dst)
    if expect_sha and got != expect_sha:
        dst.unlink(missing_ok=True)
        raise LibraryError(
            f"sha256 does not match after writing ({got[:12]}... instead of "
            f"{expect_sha[:12]}...) -- the copy was removed, the upload is untouched"
        )
    return {"path": str(dst), "sha256": got, "bytes": written}
