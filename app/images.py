"""From the original to the master, and from the master to the web sizes.

The master: a JPEG, long edge 4000 px, q88, sRGB, metadata kept.

⚠ Two things are the way they are on purpose:

1. **`autorot()` BEFORE measuring.** On a sister site 24 of 518 photographs
   were cut off top and bottom, because the dimensions were taken before the
   EXIF rotation: the file on disk was portrait, the database had stored
   landscape, and the CSS forced the picture into a 3:2 box.

2. **The embedded preview before demosaicing.** A CR3 from a modern Canon
   carries a full-resolution JPEG (8192 px) inside it. If that is at least as
   wide as our 4000 px we take it: it saves the entire 45 MP demosaic **and**
   keeps the camera's picture style and white balance -- that is, what you saw
   through the viewfinder.
"""
import base64
import subprocess
import tempfile
from pathlib import Path

import pyvips

from . import config

RAW_SUFFIXES = config._RAW_SUFFIXES


def _raw_orientation(src: Path) -> int:
    """The EXIF orientation of a RAW file (1-8), or 1 when there is none."""
    try:
        proc = subprocess.run(
            ["exiftool", "-n", "-s3", "-Orientation", "--", str(src)],
            capture_output=True, text=True, timeout=30, check=False)
        return int((proc.stdout or "1").strip() or "1")
    except (OSError, subprocess.SubprocessError, ValueError):
        return 1


def _apply_orientation(img, n: int):
    """Apply an orientation (1-8) to the pixels -- like `autorot`, but from an
    explicit value. All eight cases (rotation and mirroring) are handled."""
    if n and n != 1:
        img = img.copy()
        img.set_type(pyvips.GValue.gint_type, "orientation", n)
        img = img.autorot()
    return img


def _extract_raw_preview(src: Path, out: Path):
    """Pull out the largest embedded preview, turned the right way up.

    ⚠ The JPEG embedded in a CR3 carries NO orientation of its own -- that sits
    in the RAW header, not in the preview. So `autorot()` on the preview alone
    does NOTHING, and a photograph taken in portrait came out sideways (255 of
    them, before this was found). The orientation from the RAW is therefore
    applied explicitly here. Returns the image, or None when no preview is big
    enough."""
    n = _raw_orientation(src)
    for tag in ("-JpgFromRaw", "-PreviewImage", "-OtherImage"):
        try:
            proc = subprocess.run(["exiftool", "-b", tag, "--", str(src)],
                                  capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if len(proc.stdout) < 50_000:
            continue
        out.write_bytes(proc.stdout)
        try:
            img = _apply_orientation(pyvips.Image.new_from_file(str(out)), n)
        except pyvips.Error:
            continue
        if max(img.width, img.height) >= config.MASTER_LONG_EDGE:
            return _guard(img)
    return None


def _load(src: Path, tmpdir: Path):
    """Returns (pyvips.Image, note). The image is already rotated."""
    if src.suffix.lower() in RAW_SUFFIXES:
        prev = tmpdir / "preview.jpg"
        img = _extract_raw_preview(src, prev)
        if img is not None:
            return img, "raw-preview"
        # The preview is not good enough -- so develop the RAW properly.
        import numpy as np
        import rawpy
        with rawpy.imread(str(src)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
        img = pyvips.Image.new_from_memory(
            np.ascontiguousarray(rgb).data, rgb.shape[1], rgb.shape[0], 3, "uchar")
        return _guard(img), "raw-demosaic"
    # ⚠ No access="sequential" here. `autorot()` turns the image, and a turned
    # image is no longer read top to bottom -- libvips then stops with
    # "out of order read".
    return _guard(pyvips.Image.new_from_file(str(src)).autorot()), "direkt"


def _guard(img):
    """Check the real size before a single pixel is pulled.

    ⚠ This is the AUTHORITATIVE limit, not the filter in `meta`. libvips opens
    a file lazily: `img.width` / `img.height` read only the header and decode
    nothing. A HEIC whose header claims "100x100" but which unfolds through its
    tile grid into gigapixels shows its REAL size here -- and it is the resize
    and the save below that would eat the memory. So it is stopped here,
    BEFORE the decode.
    """
    mp = (img.width * img.height) / 1_000_000
    if mp > config.MAX_MEGAPIXELS:
        raise ValueError(
            f"{mp:.0f} MP iwwer der Grenz vu {config.MAX_MEGAPIXELS} MP")
    return img


def _to_srgb(img):
    if img.interpretation not in ("srgb", "rgb"):
        img = img.colourspace("srgb")
    if img.hasalpha():
        img = img.flatten(background=[255, 255, 255])
    return img


def build_master(src: Path, dst: Path) -> dict:
    """Write the master. Returns {width, height, source}."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(dst.parent)) as td:
        tmpdir = Path(td)
        img, how = _load(src, tmpdir)
        img = _to_srgb(img)
        long_edge = max(img.width, img.height)
        if long_edge > config.MASTER_LONG_EDGE:
            scale = config.MASTER_LONG_EDGE / long_edge
            img = img.resize(scale, kernel="lanczos3")
        img.jpegsave(str(dst), Q=config.MASTER_QUALITY, strip=True,
                     optimize_coding=True, subsample_mode="on")
        w, h = img.width, img.height

    _copy_exif(src, dst)
    return {"width": w, "height": h, "source": how}


def _copy_exif(src: Path, dst: Path) -> None:
    """Copy the metadata from the ORIGINAL -- after a RAW demosaic there is
    none otherwise.

    ⚠ `-fast`: without it exiftool reads a JPEG TO THE VERY END, looking for a
    trailer. The image library only reads the picture data. So the master can
    be finished in seconds while exiftool then hangs on the share for minutes
    -- which is exactly how one conversion died with "TimeoutExpired after 120
    seconds". The trailer is no use to us: the embedded preview does not go
    into the master anyway.

    ⚠ Orientation has to be 1: the picture is already rotated, and a copied-in
    orientation would turn it a second time.

    ⚠ A timeout is NOT swallowed here. A master without metadata looks
    finished, and the date, the camera and the GPS would be quietly gone.
    Better that the job fails and tries again in five minutes -- by then the
    share is usually quiet.
    """
    try:
        subprocess.run(
            ["exiftool", "-fast", "-overwrite_original", "-q", "-m",
             "-TagsFromFile", str(src), "-all:all", "-Orientation#=1",
             "-ExifImageWidth=", "-ExifImageHeight=", "--", str(dst)],
            capture_output=True, timeout=config.EXIFTOOL_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"exiftool gave up after {config.EXIFTOOL_TIMEOUT}s on the EXIF of "
            f"{src} -- the share is too slow, or not there") from None


def build_derivatives(master: Path, out_dir: Path, widths=None) -> dict:
    """The web sizes, out of the master -- no RAW involved, so milliseconds."""
    out_dir.mkdir(parents=True, exist_ok=True)
    widths = widths or [config.DERIVATIVE_WIDTHS[0]]
    img = pyvips.Image.new_from_file(str(master))
    made = {}
    # ⚠ A photograph narrower than the smallest step would otherwise get NO
    # derivative at all -- and would be invisible on the site. So the smallest
    # step is built anyway, at the photograph's own size.
    #
    # The file keeps the name of the STEP (`400.webp`), not the real width:
    # `serve.derivative()` looks for the steps from `DERIVATIVE_WIDTHS`, and a
    # file called `317.webp` would never be found.
    matching = [w for w in widths if w <= img.width] or ([min(widths)] if widths else [])
    for w in matching:
        # ⚠ `.copy_memory()` is required: `thumbnail()` returns a sequential
        # pipeline, and one of those cannot be saved twice -- the second write
        # stops with "out of order read".
        t = pyvips.Image.thumbnail(str(master), min(w, img.width)).copy_memory()
        t.heifsave(str(out_dir / f"{w}.avif"), Q=config.AVIF_QUALITY,
                   compression="av1", effort=config.AVIF_EFFORT)
        t.webpsave(str(out_dir / f"{w}.webp"), Q=config.WEBP_QUALITY)
        made[w] = ["avif", "webp"]
    # The placeholder: a tiny JPEG that goes into the page as a data: URI.
    lq = pyvips.Image.thumbnail(str(master), config.LQIP_WIDTH)
    buf = lq.jpegsave_buffer(Q=30, strip=True)
    (out_dir / "lqip.txt").write_text(
        "data:image/jpeg;base64," + base64.b64encode(buf).decode())
    return made
