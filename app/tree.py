"""Reading the tree, and proposing where something belongs.

The structure is  <year>/<country>/<album>/  and the rule for reading an
existing library is:

    a folder under a year that contains PHOTOGRAPHS is an album
    a folder under a year that contains ONLY FOLDERS is a country

`classify()` implements exactly that.
"""
import re
import unicodedata
from pathlib import Path

from . import config

_YEAR = re.compile(r"^(19|20)\d{2}$")
# ⚠ What a folder name may contain. Everything else is dropped -- a path is
# built out of this, and neither a slash nor a dot-dot has any business in it.
_SAFE = re.compile(r"[^0-9A-Za-zÀ-ÿ '\-_.()]")


_YEAR_TAIL = re.compile(r"[\s._-]+(19|20)\d{2}$")


def strip_year(name: str) -> str:
    """Strip a trailing year from an album name -- always, in both trees.

    People type "Anna's wedding 2016", because that is how they name folders.
    But the year is already the folder above, and the site builds the title
    itself (gallery.album_title). Left in the name, it showed up twice:
    "2016 Anna's wedding 2016".
    """
    name = (name or "").strip()
    out = _YEAR_TAIL.sub("", name).strip()
    # "2016" on its own stays -- otherwise no name would be left at all.
    return out or name


def clean_name(name: str, fallback: str = "Untitled") -> str:
    name = unicodedata.normalize("NFC", (name or "").strip())
    name = _SAFE.sub("", name).strip(" .-")
    name = re.sub(r"\s+", " ", name)
    if not name or name in (".", ".."):
        return fallback
    return name[:80]


def _dirs(path: Path):
    try:
        return sorted((p for p in path.iterdir() if p.is_dir()), key=lambda p: p.name)
    except OSError:
        return []


def _has_media(path: Path) -> bool:
    try:
        for p in path.iterdir():
            if p.is_file() and p.suffix.lower() in config.ALLOWED_SUFFIXES:
                return True
    except OSError:
        pass
    return False


def classify(folder: Path) -> str:
    """`event` when there are photographs inside, otherwise `country`."""
    return "event" if _has_media(folder) else "country"


def years(root: Path = None) -> list:
    root = root or config.ORIGIN_DIR
    return [d.name for d in _dirs(root) if _YEAR.match(d.name)]


def countries(year: str, root: Path = None) -> list:
    root = root or config.ORIGIN_DIR
    y = root / year
    if not y.is_dir():
        return []
    return [d.name for d in _dirs(y) if classify(d) == "country"]


def events(year: str, country: str = None, root: Path = None) -> list:
    """The albums in a year. Without a country: the ones that sit directly
    under the year (the older shape of a library)."""
    root = root or config.ORIGIN_DIR
    base = root / year / country if country else root / year
    if not base.is_dir():
        return []
    if country:
        return [d.name for d in _dirs(base)]
    return [d.name for d in _dirs(base) if classify(d) == "event"]


def overview(root: Path = None) -> dict:
    """Everything the upload form needs for its lists and suggestions."""
    root = root or config.ORIGIN_DIR
    out = {"years": {}, "countries": []}
    seen = set()
    for y in years(root):
        cs = countries(y, root)
        out["years"][y] = {c: events(y, c, root) for c in cs}
        # albums sitting directly under the year (the older shape)
        loose = events(y, None, root)
        if loose:
            out["years"][y]["—"] = loose
        seen.update(cs)
    out["countries"] = sorted(seen)
    return out


def propose(taken_at: str, place: str = "", country: str = "") -> dict:
    """The suggestion. Only ever a suggestion -- it appears on screen and the
    person uploading changes it or does not."""
    year = (taken_at or "")[:4]
    if not _YEAR.match(year):
        year = ""
    place = clean_name(place, "") if place else ""
    country = clean_name(country, "") if country else ""
    # The name without the year: the year is already a folder level and the
    # site builds the title from it (see gallery.album_title). Left in the
    # name as well, it shows up twice.
    return {"year": year, "country": country, "event": place, "place": place}


def target_dir(year: str, country: str, event: str, root: Path = None) -> Path:
    """The path, without creating it. Every part is cleaned."""
    root = root or config.ORIGIN_DIR
    year = clean_name(year, "")
    if not _YEAR.match(year or ""):
        raise ValueError(f"not a valid year: {year!r}")
    country = clean_name(country, "")
    event = clean_name(strip_year(event), "")
    if not country:
        raise ValueError("the country is missing")
    if not event:
        raise ValueError("the name is missing")
    return root / year / country / event


def web_dir(year: str, country: str, event: str) -> Path:
    """The same path in the served tree -- the two mirror each other."""
    return target_dir(year, country, event, config.WEB_DIR)
