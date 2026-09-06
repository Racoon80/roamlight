"""Reading the metadata out of a photograph.

`exiftool` and not a Python library, and the reason is RAW: a CR3 is an
ISO-BMFF container with its metadata in `CMT` boxes, and most libraries find
neither the capture date nor the lens nor the GPS in there. exiftool knows that
format -- and HEIC, MTS, MOV and the video containers as well.

⚠ Never through a shell. `subprocess` gets a list, and `--` goes before the
path, or a file whose name starts with `-` becomes an option.
"""
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from . import config

_TAGS = [
    "-DateTimeOriginal", "-CreateDate", "-MediaCreateDate", "-FileModifyDate",
    "-Make", "-Model", "-LensModel", "-LensID",
    "-ISO", "-FNumber", "-ExposureTime",
    "-GPSLatitude#", "-GPSLongitude#",
    "-ImageWidth", "-ImageHeight", "-Orientation#",
    "-Duration#", "-MIMEType",
]


def _parse_dt(value):
    if not value:
        return None
    s = str(value).strip()
    # exiftool gives "2026:07:14 11:03:22", sometimes with a zone after it
    m = re.match(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", s)
    if not m:
        return None
    try:
        return datetime(*(int(x) for x in m.groups()))
    except ValueError:
        return None


def read(path: Path) -> dict:
    """Returns a dictionary. Anything missing is `None` -- never an exception.

    `taken_source` says where the date came from:
      exif  from the camera (DateTimeOriginal / CreateDate)
      file  from the file date, because the camera left nothing behind
    """
    out = {
        "taken_at": None, "taken_source": None, "camera": None, "lens": None,
        "iso": None, "aperture": None, "shutter": None,
        "gps_lat": None, "gps_lon": None, "width": None, "height": None,
        "kind": "photo", "duration_s": None, "mime": None,
    }
    try:
        proc = subprocess.run(
            ["exiftool", "-j", "-n", "-charset", "filename=utf8", *_TAGS, "--", str(path)],
            capture_output=True, timeout=60, check=False,
        )
        data = json.loads(proc.stdout or b"[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return out
    if not data:
        return out
    d = data[0]

    # ⚠ The order matters. `FileModifyDate` comes AFTER the file name: a file
    # copied onto a share has the day of the copy as its file date -- so a
    # photograph from 2019 would suddenly be from today, and the whole album
    # would jump to the top of the overview. That happened: three photographs
    # with no EXIF but the date in the name
    # (`PHOTO-2019-08-12-01-05-11.jpg`).
    for key, src in (("DateTimeOriginal", "exif"), ("CreateDate", "exif"),
                     ("MediaCreateDate", "exif")):
        dt = _parse_dt(d.get(key))
        if dt:
            out["taken_at"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            out["taken_source"] = src
            break
    if not out["taken_at"]:
        dt = date_from_name(path.name)
        if dt:
            out["taken_at"] = dt
            out["taken_source"] = "name"
    if not out["taken_at"]:
        dt = _parse_dt(d.get("FileModifyDate"))
        if dt:
            out["taken_at"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            out["taken_source"] = "file"

    make = (d.get("Make") or "").strip()
    model = (d.get("Model") or "").strip()
    if model:
        out["camera"] = model if model.lower().startswith(make.lower()) else f"{make} {model}".strip()
    out["lens"] = (d.get("LensModel") or d.get("LensID") or None)
    out["iso"] = d.get("ISO") if isinstance(d.get("ISO"), int) else None
    if isinstance(d.get("FNumber"), (int, float)):
        out["aperture"] = f"f/{d['FNumber']:g}"
    if isinstance(d.get("ExposureTime"), (int, float)) and d["ExposureTime"]:
        t = d["ExposureTime"]
        out["shutter"] = f"1/{round(1/t)}s" if t < 1 else f"{t:g}s"
    for src, dst in (("GPSLatitude", "gps_lat"), ("GPSLongitude", "gps_lon")):
        if isinstance(d.get(src), (int, float)):
            out[dst] = float(d[src])
    for src, dst in (("ImageWidth", "width"), ("ImageHeight", "height")):
        if isinstance(d.get(src), int):
            out[dst] = d[src]
    out["mime"] = d.get("MIMEType")
    if (out["mime"] or "").startswith("video/") or path.suffix.lower() in config._VIDEO_SUFFIXES:
        out["kind"] = "video"
        if isinstance(d.get("Duration"), (int, float)):
            out["duration_s"] = float(d["Duration"])
    return out


# MIME type to extension. ⚠ This is the ONLY source for the name of a file a
# guest sent -- their own file name never reaches the file system.
_MIME_EXT = {
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
    "image/heic": "heic", "image/heif": "heif", "image/avif": "avif",
    "image/tiff": "tif", "image/gif": "gif",
    "image/x-canon-cr2": "cr2", "image/x-canon-cr3": "cr3",
    "image/x-nikon-nef": "nef", "image/x-sony-arw": "arw",
    "image/x-adobe-dng": "dng", "image/x-fuji-raf": "raf",
    "image/x-olympus-orf": "orf", "image/x-panasonic-rw2": "rw2",
    "video/mp4": "mp4", "video/quicktime": "mov", "video/x-msvideo": "avi",
    "video/x-m4v": "m4v", "video/mpeg": "mpg",
}


def kind_of(path: Path) -> str:
    """The extension from the **content**. Empty when the type is not known.

    ⚠ Only types the site will accept later. Otherwise a guest's file is taken,
    written into the originals tree -- and then refused by the next scan,
    because its extension is not in `ALLOWED_SUFFIXES`. That leaves a corpse in
    the one tree that must not have any.
    """
    e = _MIME_EXT.get((read(path).get("mime") or "").lower(), "")
    return e if ("." + e) in config.ALLOWED_SUFFIXES else ""


# Dates as they appear in file names. Deliberately conservative: only patterns
# with a year between 1900 and 2099 and a plausible month and day.
# `IMG_20240803_143851.jpg`, `PHOTO-2019-08-12-01-05-11.jpg`,
# `Foto 03.08.19, 10 37 27.jpg` -- the shapes phones produce.
_NAME_DATES = [
    re.compile(r"(?<!\d)(19|20)(\d{2})[-_.]?(\d{2})[-_.]?(\d{2})"
               r"(?:[-_.\s]?(\d{2})[-_.:\s]?(\d{2})[-_.:\s]?(\d{2}))?(?!\d)"),
]


def date_from_name(name: str):
    """A date out of the file name, or `None`.

    ⚠ Only when it is a **plausible** date. `DSC_20240230.jpg` gets nothing --
    there is no 30th of February, and a wrong number is worse than none.
    """
    from datetime import datetime as _dt
    for pattern in _NAME_DATES:
        m = pattern.search(name or "")
        if not m:
            continue
        jh, jj, mo, dg = m.group(1), m.group(2), m.group(3), m.group(4)
        st, mi, se = m.group(5) or "12", m.group(6) or "00", m.group(7) or "00"
        try:
            d = _dt(int(jh + jj), int(mo), int(dg), int(st), int(mi), int(se))
        except ValueError:
            continue
        if 1900 <= d.year <= _dt.now().year:
            return d.strftime("%Y-%m-%d %H:%M:%S")
    return None


def looks_like_media(path: Path, by_content: bool = False) -> tuple:
    """(ok, reason). Looks at the CONTENT, not at the extension.

    ⚠ An extension says nothing: a `.jpg` that is not an image would otherwise
    pass and then be handed to the image decoder. So exiftool reports the MIME
    type from the magic bytes, and the dimensions are read WITHOUT decoding.

    `by_content=True` skips the extension check -- for a guest file that has no
    name of ours yet. The format filter then rests on the detected type alone,
    which is the stricter of the two checks.
    """
    if not by_content and path.suffix.lower() not in config.ALLOWED_SUFFIXES:
        return False, f"the extension {path.suffix!r} is not allowed"
    if by_content and not kind_of(path):
        return False, "unknown type"
    info = read(path)
    mime = info.get("mime") or ""
    if not (mime.startswith("image/") or mime.startswith("video/")):
        return False, f"not an image and not a video (MIME {mime!r})"
    w, h = info.get("width"), info.get("height")
    if w and h:
        mp = (w * h) / 1_000_000
        if mp > config.MAX_MEGAPIXELS:
            # ⚠ The image library has no pixel limit of its own. A
            # 60,000 x 60,000 PNG fits inside any byte limit and eats all the
            # memory on the machine.
            return False, f"{mp:.0f} MP -- over the limit of {config.MAX_MEGAPIXELS} MP"
    elif by_content:
        # ⚠ Fails closed. For a guest file, "exiftool found no dimensions" is
        # not a green light -- it means we do not know what is in there, and
        # then it does not get decoded. This branch used to be missing: the
        # `if w and h:` above simply fell through, and a file with no readable
        # dimensions reached the decoder unchecked.
        return False, "no readable dimensions -- not accepted"
    return True, ""
