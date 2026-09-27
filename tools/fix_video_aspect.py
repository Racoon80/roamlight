#!/usr/bin/env python3
"""Repair the videos the old scale filter squashed.

    python3 tools/fix_video_aspect.py          # only says what it would do
    python3 tools/fix_video_aspect.py --apply

The old filter in `app/video.py` capped the height and left the width alone,
so 4K came out 3840x1080 and a portrait clip 1080x1080. ⚠ Do not look for a
non-square SAR to find them: on the live server the copies carry NO SAR at all
(ffprobe: N/A) -- they are simply squashed, in every player. The right shape
is in the database: `width`/`height` come from the poster, which ffmpeg
rotated correctly. A web copy is broken when its proportions differ from the
poster's; only those are touched.

⚠ Two roads, because an upload has no original any more:
  - library video -> a new `convert` job, built again from the original.
  - uploaded video (origin_root='user') -> the source was thrown away after
    conversion, so the web MP4 IS the only copy. It is re-encoded from itself:
    the old filter only ever squeezed the HEIGHT, so the height is stretched
    back to the poster's proportions and then the normal filter applied. One
    more generation of H.264, but the right shape. Written next to it and swapped in only when
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


def dims(p: Path) -> tuple:
    """Width and height as a player sees them (after any rotation tag)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:stream_side_data=rotation",
         "-of", "json", "--", str(p)],
        capture_output=True, text=True, timeout=120)
    st = (json.loads(out.stdout or "{}").get("streams") or [{}])[0]
    w, h = int(st.get("width") or 0), int(st.get("height") or 0)
    rot = next((abs(int(d.get("rotation", 0))) for d in st.get("side_data_list") or []
                if "rotation" in d), 0)
    return (h, w) if rot in (90, 270) else (w, h)


def off(a: tuple, b: tuple) -> bool:
    return abs(a[0] / a[1] - b[0] / b[1]) > 0.02 * (b[0] / b[1])


def reencode(p: Path, want: tuple) -> None:
    tmp = p.with_name(p.stem + ".fix.mp4")
    m = video.MAX_HEIGHT
    ratio = f"{want[0]}/{want[1]}"
    vf = (f"scale=iw:'trunc(iw/({ratio})/2)*2',setsar=1,"
          f"scale='if(gte(iw,ih),-2,min({m},trunc(iw/2)*2))'"
          f":'if(gte(iw,ih),min({m},trunc(ih/2)*2),-2)',setsar=1")
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-i", str(p), "-vf", vf,
             "-c:v", "libx264", "-preset", video.PRESET, "-crf", video.CRF,
             "-c:a", "copy", "-movflags", "+faststart", "-pix_fmt", "yuv420p",
             "-map_metadata", "-1", str(tmp)],
            check=True, capture_output=True, timeout=3600)
        if off(dims(tmp), want):
            raise RuntimeError(f"still the wrong shape: {dims(tmp)}")
        os.chmod(tmp, 0o640)
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> None:
    apply = "--apply" in sys.argv
    rows = db.connect().execute(
        "SELECT id, origin_root, web_name, width, height FROM photos "
        "WHERE kind='video' "
        "AND web_name IS NOT NULL").fetchall()
    for r in rows:
        mp4 = (config.WEB_DIR / r["web_name"]).with_suffix(".mp4")
        if not mp4.is_file():
            print(f"{r['id']:>7}  no MP4 on disk: {mp4}")
            continue
        want = (r["width"], r["height"])
        if not all(want):
            continue
        have = dims(mp4)
        if not all(have) or not off(have, want):
            continue
        road = "re-encode" if r["origin_root"] == "user" else "convert job"
        print(f"{r['id']:>7}  {have[0]}x{have[1]} -> {want[0]}x{want[1]}  "
              f"{road:<11} {mp4}")
        if not apply:
            continue
        if r["origin_root"] == "user":
            try:
                reencode(mp4, want)
            except Exception as e:  # one bad file must not stop the rest
                print(f"         failed: {e}")
        else:
            enqueue("convert", str(r["id"]))
    if not apply:
        print("\n(dry run -- --apply to do it)")


if __name__ == "__main__":
    main()
