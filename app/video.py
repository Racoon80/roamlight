"""Video: a poster frame and a web-ready MP4.

Why convert instead of serving the original: a phone or cinema-camera video is
often HEVC/MOV in 4K -- too big to start quickly, and HEVC does not play in
every browser. So an **H.264/AAC MP4** is made, with `faststart` (the moov atom
first, so it begins playing immediately), scaled down to 1080p. The original is
never touched.

The poster is a single frame. It carries the video through the whole photo
machinery -- gallery, grid, album cover -- and the video itself plays when
somebody opens it.
"""
import json
import logging
import subprocess
from pathlib import Path

from . import config

log = logging.getLogger("family")

MAX_HEIGHT = int(getattr(config, "VIDEO_MAX_HEIGHT", 1080))
CRF = str(getattr(config, "VIDEO_CRF", 23))
PRESET = getattr(config, "VIDEO_PRESET", "veryfast")


def probe(src: Path) -> dict:
    """Width, height and duration (seconds) of a video."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration",
         "-of", "json", "--", str(src)],
        capture_output=True, text=True, timeout=120)
    d = json.loads(out.stdout or "{}")
    st = (d.get("streams") or [{}])[0]
    dur = 0.0
    try:
        dur = float((d.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    return {"width": int(st.get("width") or 0),
            "height": int(st.get("height") or 0),
            "duration": dur}


def poster(src: Path, dst: Path, at: float = 1.0) -> None:
    """Grab one frame around `at` seconds. ffmpeg applies the display matrix
    itself, so a phone video shot sideways comes out the right way up."""
    def grab(t):
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-ss", str(max(0.0, t)), "-i", str(src),
             "-frames:v", "1", "-q:v", "3", "-f", "image2", str(dst)],
            check=True, capture_output=True, timeout=180)
    try:
        grab(at)
        if dst.is_file() and dst.stat().st_size > 0:
            return
    except subprocess.SubprocessError:
        pass
    # Short clip, or something wrong at `at` -- try from the very start.
    grab(0.0)


def web_master(src: Path, dst: Path) -> None:
    """H.264/AAC MP4, faststart, scaled down to MAX_HEIGHT.

    The scale filter keeps both dimensions even, because H.264 requires it."""
    vf = f"scale='trunc(iw/2)*2':'min({MAX_HEIGHT},trunc(ih/2)*2)'"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(src),
         "-vf", vf,
         "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
         "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", "-pix_fmt", "yuv420p",
         "-map_metadata", "-1",          # no GPS or serial in the web master
         str(dst)],
        check=True, capture_output=True, timeout=3600)


def build(src: Path, tmp_poster: Path, tmp_mp4: Path) -> dict:
    """Make the poster and the web copy. Returns {width, height, duration}."""
    pr = probe(src)
    poster(src, tmp_poster, at=min(1.0, (pr["duration"] or 3) / 3))
    web_master(src, tmp_mp4)
    return pr
