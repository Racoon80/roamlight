#!/usr/bin/env python3
"""Repair the videos the old scale filter squashed.

    python3 tools/fix_video_aspect.py          # only says what it would do
    python3 tools/fix_video_aspect.py --apply

The old filter in `app/video.py` capped the height and left the width alone,
so 4K came out 3840x1080 and a portrait clip 1080x1080, with a non-square
pixel aspect ratio (SAR) to make up for it -- which many players ignore. A web
copy is broken exactly when its SAR is not 1:1; only those are touched.

⚠ Two roads, because an upload has no original any more:
  - library video -> a new `convert` job, built again from the original.
  - uploaded video (origin_root='user') -> the source was thrown away after
    conversion, so the web MP4 IS the only copy. It is re-encoded from itself:
    the SAR still holds the right proportions, so stretching by it and then
    applying the normal filter gives the correct picture. One more generation
    of H.264, but the right shape. Written next to it and swapped in only when
    it probes clean.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config, db, video  # noqa: E402
from app.worker import enqueue  # noqa: E402


def sar(p: Path) -> str:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=sample_aspect_ratio", "-of", "json", "--", str(p)],
        capture_output=True, text=True, timeout=120)
    st = (json.loads(out.stdout or "{}").get("streams") or [{}])[0]
    return st.get("sample_aspect_ratio") or "1:1"


def reencode(p: Path) -> None:
    tmp = p.with_name(p.stem + ".fix.mp4")
    m = video.MAX_HEIGHT
    vf = (f"scale='trunc(iw*sar/2)*2':ih,setsar=1,"
          f"scale='if(gte(iw,ih),-2,min({m},trunc(iw/2)*2))'"
          f":'if(gte(iw,ih),min({m},trunc(ih/2)*2),-2)',setsar=1")
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-i", str(p), "-vf", vf,
             "-c:v", "libx264", "-preset", video.PRESET, "-crf", video.CRF,
             "-c:a", "copy", "-movflags", "+faststart", "-pix_fmt", "yuv420p",
             "-map_metadata", "-1", str(tmp)],
            check=True, capture_output=True, timeout=3600)
        if sar(tmp) not in ("1:1", "0:1"):
            raise RuntimeError("still not square pixels")
        os.chmod(tmp, 0o640)
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> None:
    apply = "--apply" in sys.argv
    rows = db.connect().execute(
        "SELECT id, origin_root, web_name FROM photos WHERE kind='video' "
        "AND web_name IS NOT NULL").fetchall()
    for r in rows:
        mp4 = (config.WEB_DIR / r["web_name"]).with_suffix(".mp4")
        if not mp4.is_file():
            continue
        s = sar(mp4)
        if s in ("1:1", "0:1", "N/A"):
            continue
        road = "re-encode" if r["origin_root"] == "user" else "convert job"
        print(f"{r['id']:>7}  SAR {s:<8} {road:<11} {mp4}")
        if not apply:
            continue
        if r["origin_root"] == "user":
            try:
                reencode(mp4)
            except Exception as e:  # one bad file must not stop the rest
                print(f"         failed: {e}")
        else:
            enqueue("convert", str(r["id"]))
    if not apply:
        print("\n(dry run -- --apply to do it)")


if __name__ == "__main__":
    main()
