#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write every native shader program in a NUP game's RomFS as an EDEN_NX2_SHADER_DUMP directory.

usage: python3 -I tools/nx2/nup_shader_dump.py <game.nup> <new output dir>
           [--cache <vulkan.fork-vNN.bin> --lower-dump <lower_dump binary>]

Programs are captured like GenericEnvironment::LowerNativeCode (ProgramCaptureState) and named
<NativeProgramKey>-gfx|cs-0.bin, with a .header.bin sidecar for graphics, so the keys match live
dumps and EDEN_NX2_REJECT_HASH. manifest.json lists every program's stage and archive origins.
Disk cache records hold only lowered code, so --cache marks the programs the cache holds by
lowering each one with lower_dump (.claude/skills/nx2-shader-triage/scripts/lower_dump.cpp) and
matching its hash; that only matches records written with the same lowering.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_static_shaders import walk_container  # noqa: E402
from shader_archive import ArchiveError  # noqa: E402
from shader_containers import read_zstd_dictionaries, zstd_module  # noqa: E402
from shader_sweep import CacheError, parse_cache, unpack_native_code  # noqa: E402

NO_DIR = 0xFFFFFFFF
ARCHIVE_EXTENSIONS = (".bfsha", ".sarc", ".bnsh", ".cgsb", ".shn", ".sharcb")
MAX_INSTRUCTIONS = 65536
GRAPHICS_HEADER = 128


class NupError(ValueError):
    pass


class Nup:
    """Read-only NUP reader (src/core/file_sys/nup_container.cpp)."""

    def __init__(self, path):
        self.file = open(path, "rb")
        self.size = self.file.seek(0, 2)
        superblock = self.read(0, 0x80)
        if superblock[:4] != b"NUP\x01":
            raise NupError("not a NUP file")
        toc_offset, toc_size = struct.unpack_from("<QQ", superblock, 8)
        toc_hash = superblock[0x18:0x38]
        if toc_offset < 0x80 or toc_size > self.size - toc_offset:
            raise NupError("TOC out of range")
        toc = self.read(toc_offset, toc_size)
        if hashlib.sha256(toc).digest() != toc_hash:
            raise NupError("TOC SHA-256 mismatch")
        magic, packages, dirs, files, pool, pool_size = struct.unpack_from("<IIQQQQ", toc)
        if magic != 0x01434F54:
            raise NupError("bad TOC magic")
        pos = 0x30
        self.packages = []
        for _ in range(packages):
            title, root, exefs, romfs, version, kind = struct.unpack_from("<QIIIIB", toc, pos)
            self.packages.append({"title_id": title, "romfs": romfs, "version": version, "kind": kind})
            pos += 0x40
        name = lambda offset, length: toc[pool + offset:pool + offset + length].decode("utf-8")
        self.dirs = []
        for _ in range(dirs):
            offset, length, first, count = struct.unpack_from("<IIII", toc, pos)
            self.dirs.append((name(offset, length), first, count))
            pos += 0x18
        self.files = []
        for _ in range(files):
            offset, length, size, extent, extent_size = struct.unpack_from("<IIQQQ", toc, pos)
            self.files.append((name(offset, length), size, extent, extent_size, toc[pos + 0x40]))
            pos += 0x50
        self.children = struct.unpack_from(f"<{(pool - pos) // 4}I", toc, pos)

    def read(self, offset, size):
        self.file.seek(offset)
        data = self.file.read(size)
        if len(data) != size:
            raise NupError("short read")
        return data

    def tree(self, directory, prefix=""):
        """{RomFS path: file index} under a directory node."""
        result = {}
        _, first, count = self.dirs[directory]
        for child in self.children[first:first + count]:
            if child < len(self.dirs):
                result.update(self.tree(child, prefix + self.dirs[child][0] + "/"))
            else:
                result[prefix + self.files[child - len(self.dirs)][0]] = child - len(self.dirs)
        return result

    def romfs(self):
        """The base RomFS with the newest update's files over it."""
        base = [p for p in self.packages if p["kind"] == 0 and p["romfs"] != NO_DIR]
        updates = [p for p in self.packages if p["kind"] == 1 and p["romfs"] != NO_DIR]
        result = self.tree(base[0]["romfs"]) if base else {}
        if updates:
            result.update(self.tree(max(updates, key=lambda p: p["version"])["romfs"]))
        return result

    def open(self, index):
        _, size, extent, extent_size, codec = self.files[index]
        data = self.read(extent, extent_size) if size else b""
        if codec == 1:
            data = zstd_module().decompress(data)
        if len(data) != size:
            raise NupError(f"{self.files[index][0]}: size mismatch")
        return data


def classify(path):
    """ClassifyGameFile (src/video_core/shader_warm/shader_archives.cpp)."""
    if path == "Pack/ZsDic.pack.zs":
        return "dictionaries"
    lower = path.lower().removesuffix(".zs")
    if lower.endswith(ARCHIVE_EXTENSIONS):
        return "archive"
    if path.startswith("Effect/") and "/" not in path[7:] and path.endswith(".vsetb.byml.zs"):
        return "effect"
    return None


def bits(words, offset, count):
    value = words[0] | words[1] << 32 | words[2] << 64 | words[3] << 96
    return value >> offset & ((1 << count) - 1)


def capture(code):
    """Shader::Ampere::ProgramCaptureState over `code`: (instruction count, complete, invalid)."""
    exit_setup = []
    last_call = last_forward = 0
    main_exited = computed = False
    count = min(len(code) // 16, MAX_INSTRUCTIONS)
    for index in range(count):
        words = struct.unpack_from("<4I", code, index * 16)
        opcode = words[0] & 0xfff
        if index < 5:
            exit_setup.append(words)
            s = exit_setup
            if (index == 4 and all(w[3] & 0xc00001ff == 0 for w in s) and
                    s[0][:3] == (0x734e, 0, 0) and s[1][0] == 0x37802 and s[1][2] == 0xf00 and
                    s[2][:3] == (0x7210, 3, 0x07f1e0ff) and s[3][1] == 0xff and
                    ((s[3][0], s[3][2]) in ((0x01017210, 0x007fe4ff), (0xff017224, 0x000e0401))) and
                    words[:3] == (0x7357, 0, 0)):
                target = s[1][1]
                if target < 80 or target % 16 or target >= MAX_INSTRUCTIONS * 16:
                    return index + 1, False, True
                last_forward = max(last_forward, target // 16)
        if opcode in (0x944, 0x947, 0x945):
            low = words[1] & (~3 & 0xffffffff if opcode == 0x947 else 0xffffffff)
            if opcode == 0x945:
                relative = words[1] - (1 << 32) if words[1] >> 31 else words[1]
            else:
                raw = low | (words[2] & 0x3ffff) << 32
                relative = raw - (1 << 50) if raw >> 49 else raw
            target = index * 16 + 16 + relative
            if target < 0 or target % 16 or target >= MAX_INSTRUCTIONS * 16:
                return index + 1, False, True
            if opcode == 0x944:
                last_call = max(last_call, target // 16)
            else:
                last_forward = max(last_forward, target // 16)
            if opcode == 0x947 and computed and bits(words, 12, 4) == 7 and target == index * 16:
                return index + 1, True, False
        if opcode in (0x949, 0x958):
            computed = True
        if computed:
            continue
        if opcode == 0x94d and bits(words, 12, 4) == 7 and index >= last_forward:
            if index >= last_call:
                return index + 1, True, False
            main_exited = True
        if (opcode == 0x950 and bits(words, 12, 4) == 7 and main_exited and
                index >= last_call and index >= last_forward):
            return index + 1, True, False
    return count, False, False


def program_key(native):
    """Shader::Ampere::NativeProgramKey: FNV-1a over the instruction bytes."""
    value = 14695981039346656037
    for byte in native:
        value = (value ^ byte) * 1099511628211 & 0xFFFFFFFFFFFFFFFF
    return value


def fnv_words(data):
    """lower_dump's OK hash over 64-bit words."""
    value = 1469598103934665603
    for (word,) in struct.iter_unpack("<Q", data):
        value = (value ^ word) * 1099511628211 & 0xFFFFFFFFFFFFFFFF
    return value


def mark_cached(output, programs, cache, lower_dump):
    """Keys whose lowering (by `lower_dump`) equals the lowered code of a cache record."""
    cached = Counter()
    for env in parse_cache(cache.read_bytes()):
        lowered = unpack_native_code(env["code"])
        if lowered is not None:
            cached[fnv_words(lowered)] += 1
    names = sorted(p["file"] for p in programs.values())
    hashes = {}
    for begin in range(0, len(names), 512):
        result = subprocess.run([str(lower_dump), *(str(output / n) for n in names[begin:begin + 512])],
                                capture_output=True, text=True, check=False)
        for line in result.stdout.splitlines():
            fields = line.split()
            if fields[0] == "OK":
                hashes[fields[1]] = int(fields[3], 16)
    matched = set()
    for program in programs.values():
        lowered = hashes.get(program["file"])
        program["cached"] = lowered in cached
        if program["cached"]:
            matched.add(lowered)
    return len(cached), len(matched)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("game", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cache", type=Path, help="Vulkan disk cache (vulkan.fork-vNN.bin)")
    parser.add_argument("--lower-dump", type=Path, help="lower_dump binary, required with --cache")
    args = parser.parse_args()
    if args.cache and not (args.lower_dump and args.lower_dump.is_file()):
        parser.error("--cache needs --lower-dump")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("output must be a new or empty directory")
    if zstd_module() is None:
        parser.error("needs Python 3.14 compression.zstd or backports.zstd")

    nup = Nup(args.game)
    romfs = nup.romfs()
    dictionaries = None
    if "Pack/ZsDic.pack.zs" in romfs:
        dictionaries = read_zstd_dictionaries(nup.open(romfs["Pack/ZsDic.pack.zs"]))
    # Effects are read after every shader archive, like the warm builder.
    order = sorted((p for p in romfs if classify(p) in ("archive", "effect")),
                   key=lambda p: (classify(p) == "effect", p))
    output.mkdir(parents=True, exist_ok=True)
    programs = {}
    errors = []
    stats = Counter()
    for path in order:
        stats["archives"] += 1
        try:
            records = list(walk_container(nup.open(romfs[path]), path, dictionaries=dictionaries))
        except (ArchiveError, NupError, ValueError) as exc:
            errors.append({"path": path, "error": str(exc)})
            continue
        for record in records:
            if "error" in record:
                errors.append({"path": record["member"], "error": record["error"]})
                continue
            stats["records"] += 1
            compute = record["stage"] == "compute"
            header = b"" if compute else record["code"][:GRAPHICS_HEADER]
            code = record["code"][len(header):]
            length, complete, invalid = capture(code)
            if invalid:
                stats["invalid branch target"] += 1
                continue
            if not complete:
                stats["incomplete capture"] += 1
            native = code[:length * 16]
            key = program_key(native)
            kind = "cs" if compute else "gfx"
            identity = f"{key:016x}-{kind}"
            program = programs.get(identity)
            if program is None:
                program = programs[identity] = {
                    "key": f"{key:016x}", "file": f"{identity}-0.bin", "stage": record["stage"],
                    "instructions": length, "complete": complete, "headers": [], "origins": []}
                (output / program["file"]).write_bytes(native)
                if header:
                    (output / f"{identity}-0.header.bin").write_bytes(header)
            if header and hashlib.sha256(header).hexdigest() not in program["headers"]:
                program["headers"].append(hashlib.sha256(header).hexdigest())
            if len(program["origins"]) < 8:
                program["origins"].append(record["member"])
    summary = {"archives": stats["archives"], "records": stats["records"], "programs": len(programs),
               "stages": dict(Counter(p["stage"] for p in programs.values())),
               "incomplete_captures": stats["incomplete capture"],
               "invalid_branch_targets": stats["invalid branch target"], "errors": len(errors)}
    if args.cache:
        try:
            summary["cache_programs"], summary["cache_programs_matched"] = mark_cached(
                output, programs, args.cache, args.lower_dump)
        except CacheError as exc:
            parser.error(f"{args.cache}: {exc}")
        summary["archive_programs_in_cache"] = sum(p["cached"] for p in programs.values())
    (output / "manifest.json").write_text(json.dumps(
        {"summary": summary, "programs": sorted(programs.values(), key=lambda p: p["file"]),
         "errors": errors}, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
