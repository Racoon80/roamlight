"""Virus scanning with ClamAV.

Photographs arrive on other people's sticks and other people's phones. So
**every** file is scanned before the site trusts it -- one from a guest, and
one you put in yourself.

The scan normally runs through **`clamdscan --fdpass`**: the resident daemon
keeps its signatures in memory (fast), and `--fdpass` hands it the already-open
file descriptor -- so the scanner's own user needs no permissions at all on the
upload folder or the library.

⚠ **It fails closed.** If the scanner gives no clear answer (daemon gone,
timeout, error), the file counts as **not clean** and goes no further. Letting
a file through because the scanner happened to be down is exactly the case this
module exists to prevent.

⚠ **`--fdpass` does not work in Docker.** Handing over a file descriptor only
works when both sides are in the same container. When the scanner is a
neighbouring container, `FAMILY_CLAMAV_HOST` is set and the file goes over TCP
by **INSTREAM** instead. That is implemented here directly (thirty lines)
rather than through `clamdscan`: otherwise the whole ClamAV suite would have to
go into the image for a program that does not even run there.
"""
import logging
import shutil
import socket
import struct
import subprocess

from . import config

log = logging.getLogger("family")

CLEAN, INFECTED, ERROR = "clean", "infected", "error"


def enabled() -> bool:
    if not config.CLAMAV_ENABLED:
        return False
    return bool(config.CLAMAV_HOST) or shutil.which("clamdscan") is not None


def _stream(path) -> tuple:
    """INSTREAM against a clamd over TCP (the neighbouring container).

    The protocol is simple: `zINSTREAM\0`, then chunks as
    `<length, 4 bytes big-endian><data>`, then a zero length. Back comes one
    line: `stream: OK` or `stream: <name> FOUND`.
    """
    host, port = config.CLAMAV_HOST, config.CLAMAV_PORT
    try:
        with socket.create_connection((host, port), timeout=config.CLAMAV_TIMEOUT) as s:
            s.settimeout(config.CLAMAV_TIMEOUT)
            s.sendall(b"zINSTREAM\0")
            with open(path, "rb") as f:
                while True:
                    block = f.read(64 * 1024)
                    if not block:
                        break
                    s.sendall(struct.pack("!L", len(block)) + block)
            s.sendall(struct.pack("!L", 0))
            answer = b""
            while b"\0" not in answer and len(answer) < 4096:
                part = s.recv(4096)
                if not part:
                    break
                answer += part
    except (OSError, socket.timeout) as exc:
        log.error("ClamAV (%s:%s): %s", host, port, exc)
        return ERROR, str(exc)
    text = answer.decode("utf-8", "replace").strip("\0\n ")
    if text.endswith("OK"):
        return CLEAN, ""
    if text.endswith("FOUND"):
        log.warning("ClamAV: %s", text)
        return INFECTED, text
    log.error("ClamAV: unexpected answer %r", text[:200])
    return ERROR, text[:200]


def scan(path) -> tuple:
    """`(state, detail)` -- `clean`, `infected` or `error`.

    With the scanner switched off this returns `(clean, "av off")`: running
    without scanning is then a decision somebody made, not a silent failure of
    ours.
    """
    if not config.CLAMAV_ENABLED:
        return CLEAN, "av off"
    if config.CLAMAV_HOST:
        return _stream(path)
    if shutil.which("clamdscan") is None:
        log.error("ClamAV: clamdscan is missing although FAMILY_CLAMAV=1 -- refusing the file")
        return ERROR, "clamdscan missing"
    try:
        proc = subprocess.run(
            ["clamdscan", "--fdpass", "--no-summary", "--", str(path)],
            capture_output=True, timeout=config.CLAMAV_TIMEOUT, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("ClamAV: %s", exc)
        return ERROR, str(exc)
    # clamdscan: 0 = clean, 1 = found something, 2 = error
    if proc.returncode == 0:
        return CLEAN, ""
    if proc.returncode == 1:
        detail = (proc.stdout or "").strip().split("\n")[0]
        log.warning("ClamAV: %s", detail)
        return INFECTED, detail
    log.error("ClamAV rc=%s: %s", proc.returncode,
              (proc.stderr or proc.stdout or "").strip()[:200])
    return ERROR, f"clamdscan rc={proc.returncode}"


def is_clean(path) -> bool:
    """In short: **only** a clear "clean" gets through. Nothing else does."""
    verdict, _ = scan(path)
    return verdict == CLEAN
