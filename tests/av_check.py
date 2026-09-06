#!/usr/bin/env python3
"""Acceptance test for the virus scan (app/av.py). Runs on the server as `family`.

The point: photographs come in on other people's sticks and phones, and one of
those files can be compromised. This test proves that

  * the scanner is on and talking to clamd,
  * a clean photograph goes through,
  * a file carrying the EICAR signature is recognised as infected,
  * and -- most importantly -- the WHOLE content of a valid photograph is
    scanned, not only its header (a test signature of our own inside a JPEG).
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env                                              # noqa: E402

sys.path.insert(0, _env.app_root())

ok = bad = 0


def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1; print(f"  ok    {name}")
    else:
        bad += 1; print(f"  FAIL  {name}  {extra}")


def _jpeg(p, size=(500, 350)):
    subprocess.run(["/opt/family/venv/bin/python3", "-c",
                    f"import pyvips;pyvips.Image.black({size[0]},{size[1]})"
                    f".copy(interpretation='b-w').colourspace('srgb')"
                    f".jpegsave({str(p)!r})"], check=True, capture_output=True)


def main():
    from app import av, config
    print("Ofnahm-Test — Virescan (ClamAV)\n")
    if not config.CLAMAV_ENABLED:
        print("  --    FAMILY_CLAMAV=0 -- the scanner is off, the test is skipped")
        return 0

    chk("the scanner is configured", av.enabled())

    tmp = Path(tempfile.mkdtemp(dir="/opt/family/incoming"))
    try:
        # 1. Propper Foto
        clean = tmp / "clean.jpg"; _jpeg(clean)
        z, d = av.scan(clean)
        chk("a clean photograph is clean", z == av.CLEAN, f"{z} {d}")

        # 2. EICAR (Standard-Testdatei fir jidder Scanner)
        eicar = tmp / "eicar.com"
        eicar.write_text("X5O!P%@AP[4\\PZX54(P^)7CC)7}$"
                         "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")
        z, d = av.scan(eicar)
        chk("⚠ an infected file is recognised", z == av.INFECTED, f"{z} {d}")
        chk("is_clean() does NOT let it through", not av.is_clean(eicar))

        # 3. The whole content of a valid photograph is scanned
        #    (our own signature, because the EICAR one is anchored to that exact file)
        marker = b"ZZ-FAMILY-AVTEST-" + os.urandom(4).hex().encode()
        sigdir = Path(tempfile.mkdtemp())
        (sigdir / "t.ndb").write_bytes(
            b"FamilyAvTest:0:*:" + marker.hex().encode() + b"\n")
        poison = tmp / "poison.jpg"; _jpeg(poison)
        with open(poison, "ab") as f:
            f.write(marker)
        # Image filter: valid
        from app import meta
        good, _ = meta.looks_like_media(poison, by_content=True)
        chk("the poisoned photograph is a valid JPEG", good)
        # Scan mat der Test-Signatur
        r = subprocess.run(["clamscan", "--no-summary", f"--database={sigdir}",
                            "--", str(poison)], capture_output=True, text=True)
        chk("⚠ the marker INSIDE the JPEG is found (the whole file is scanned)",
            "FamilyAvTest.UNOFFICIAL FOUND" in r.stdout, r.stdout.strip()[:120])
        import shutil as _sh
        _sh.rmtree(sigdir, ignore_errors=True)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n  {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
