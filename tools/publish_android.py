#!/usr/bin/env python3
"""Put a signed Android APK on this site, for the family to download.

    python3 tools/publish_android.py Roamlight-1.1.0.apk --code 110 --name 1.1.0

The APK goes to FAMILY_ANDROID_DIR (default <data>/android) as roamlight.apk,
and next to it roamlight.json says which version it is. The page "Phone &
tablet" then offers the download, and the app compares its own versionCode
with this one and offers the update.

⚠ The version is TOLD, not read out of the APK: that would need the Android
  build tools on the server. So say it right -- `--code` must be the
  versionCode in android/app/build.gradle.kts, and it only ever goes up.
⚠ Written next to the old one first and then swapped in, so a download that
  is running never gets half an APK.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("apk", type=Path)
    ap.add_argument("--code", type=int, required=True, help="versionCode")
    ap.add_argument("--name", required=True, help="versionName, e.g. 1.1.0")
    a = ap.parse_args()

    data = a.apk.read_bytes()
    if data[:2] != b"PK":
        sys.exit("that is not an APK (not a zip file)")
    out = config.ANDROID_DIR
    out.mkdir(parents=True, exist_ok=True)
    old = out / "roamlight.json"
    if old.is_file():
        was = json.loads(old.read_text()).get("version_code", 0)
        if a.code < was:
            sys.exit(f"versionCode {a.code} is lower than the published {was} -- "
                     "phones would never be offered it")
    tmp = out / "roamlight.apk.new"
    tmp.write_bytes(data)
    os.replace(tmp, out / "roamlight.apk")
    meta = {"version_code": a.code, "version_name": a.name,
            "sha256": hashlib.sha256(data).hexdigest()}
    tmpj = out / "roamlight.json.new"
    tmpj.write_text(json.dumps(meta, indent=1))
    os.replace(tmpj, old)
    print(f"published Roamlight {a.name} ({a.code}), {len(data) / 1048576:.1f} MB, "
          f"sha256 {meta['sha256'][:16]}…  -> {out}")


if __name__ == "__main__":
    main()
