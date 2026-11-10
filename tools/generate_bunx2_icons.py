#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Bunx2 contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Render the Bunx2 icons from the SVG masters in dist/branding.

Sizes up to 32 px come from bunx2-small.svg, larger ones from bunx2.svg. Writes:

  dist/branding/png/bunx2-<size>.png   16, 24, 32, 48, 64, 128, 256 and 512 px
  dist/bunx2.ico                       16 to 256 px, PNG frames
  dist/bunx2.icns                      16 to 1024 px, PNG frames
  dist/bunx2.bmp                       256 px, 32-bit, for the SDL frontend's window icon

Needs rsvg-convert (librsvg) and magick (ImageMagick). With --check, renders into a temporary
directory and reports which committed files differ instead of writing them. Output is only
byte-identical across machines with the same librsvg and ImageMagick versions.
"""

import argparse
import filecmp
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BRANDING = DIST / "branding"
MASTER = BRANDING / "bunx2.svg"
SMALL = BRANDING / "bunx2-small.svg"
SMALL_MAX = 32

PNG_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# Type codes and pixel sizes of the PNG-based ICNS entries.
ICNS_ENTRIES = (
    ("icp4", 16), ("icp5", 32), ("icp6", 64), ("ic07", 128), ("ic08", 256), ("ic09", 512),
    ("ic10", 1024), ("ic11", 32), ("ic12", 64), ("ic13", 256), ("ic14", 512),
)


def render(size, out):
    source = SMALL if size <= SMALL_MAX else MASTER
    subprocess.run(["rsvg-convert", "--width", str(size), "--height", str(size),
                    "--keep-aspect-ratio", "--output", str(out), str(source)], check=True)
    return out.read_bytes()


def write_ico(frames, out):
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    entries = b""
    for size, data in frames:
        dimension = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    out.write_bytes(header + entries + b"".join(data for _, data in frames))


def write_icns(frames, out):
    body = b"".join(kind.encode("ascii") + struct.pack(">I", 8 + len(data)) + data
                    for kind, data in frames)
    out.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)


def generate(dist):
    branding_png = dist / "branding" / "png"
    branding_png.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        rendered = {size: render(size, tmp / f"{size}.png")
                    for size in sorted(set(PNG_SIZES + ICO_SIZES + (1024,)))}
        for size in PNG_SIZES:
            (branding_png / f"bunx2-{size}.png").write_bytes(rendered[size])
        write_ico([(size, rendered[size]) for size in ICO_SIZES], dist / "bunx2.ico")
        write_icns([(kind, rendered[size]) for kind, size in ICNS_ENTRIES], dist / "bunx2.icns")
        # BMP3 keeps the 54-byte header SDL_LoadBMP reads, with alpha in the fourth byte.
        subprocess.run(["magick", str(tmp / "256.png"), "-define", "bmp3:alpha=true",
                        f"BMP3:{dist / 'bunx2.bmp'}"], check=True)


def outputs(dist):
    return ([dist / "branding" / "png" / f"bunx2-{size}.png" for size in PNG_SIZES] +
            [dist / name for name in ("bunx2.ico", "bunx2.icns", "bunx2.bmp")])


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="compare freshly rendered icons with the committed ones")
    args = parser.parse_args()

    for tool in ("rsvg-convert", "magick"):
        if shutil.which(tool) is None:
            sys.exit(f"{tool} is required (pacman -S librsvg imagemagick)")

    if not args.check:
        generate(DIST)
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        fresh = pathlib.Path(tmp) / "dist"
        generate(fresh)
        stale = [path.relative_to(DIST) for path, new in zip(outputs(DIST), outputs(fresh))
                 if not path.exists() or not filecmp.cmp(path, new, shallow=False)]
    for path in stale:
        print(f"differs: dist/{path}")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
