# Tools

Tools for Eden and other subprojects. When adding new scripts please use `#!/bin/sh -e` or `#!/usr/bin/env <interpreter>` (for `.py`, `.rb`, or `.perl`). Keep scripts POSIX compliant (i.e not require hard `bash` to run, just plain old `sh`).

## Third-Party

- [CPMUtil Scripts](./cpm)

## Binaries

- `maxwell-spirv`: Converts Maxwell shaders (dumped from `.ash` files) into SPIR-V code (emitted into STDOUT).
- `maxwell-disas`: Dumb raw Maxwell disassembler.
- `maxwell-ir`: Dump generated IR of Maxwell shaders.

## Scripts

- `generate_converters.py`: Generates converters for given formats of textures (C++ helper).
- `svc_generator.py`: Generates the files `src/core/hle/kernel/svc.cpp` and `src/core/hle/kernel/svc.h` based off prototypes.
- `shellcheck.sh`: Ensure POSIX compliance (and syntax sanity) for all tools in this directory and subdirectories.
- `llvmpipe-run.sh`: Sets environment variables needed to run any command (or Eden) with llvmpipe.
- `optimize-assets.sh`: Optimizes PNG assets with OptiPng.
- `update-cpm.sh`: Updates CPM.cmake to the latest version.
- `generate_bunx2_icons.py`: Renders the Bunx2 application icons (`dist/branding/png/`, `dist/bunx2.ico`, `dist/bunx2.icns`, `dist/bunx2.bmp`) from `dist/branding/bunx2.svg` and `dist/branding/bunx2-small.svg`. `--check` reports committed icons that differ from a fresh render.
    * Requires: `rsvg-convert` (librsvg), ImageMagick
- `update-icons.sh`: Eden's script. Rebuilds Eden's icons (macOS, Windows, bitmaps, Android) from `dist/icon_variations`; Bunx2 does not use those icons for its application identity.
    * Also optimizes the master SVG
    * Requires: `png2icns` (libicns), ImageMagick, [`svgo`](https://github.com/svg/svgo)
- `dtrace-tool.sh`
- `lanczos-gen.pl`: Generates constants for the Lanczos filter.
- `clang-format.sh`: Runs `clang-format` on the entire codebase.
    * Requires: clang
- `find-unused-strings.sh`: Find any unused strings in the Android app (XML -> Kotlin).
- `cpp-lint.sh`: Homemade dumb C++ linter.
- `fuzzsettings.cpp`: Fuzz settings files.

## Android

It's recommended to run these scripts after almost any Android change, as they are relatively fast and important both for APK bloat and CI.

- `unused-strings.sh`: Finds unused strings in `strings.xml` files.
- `stale-translations.sh`: Finds translated strings that aren't present in the source `strings.xml` file.

## Translations

- [Translation Scripts](./translations)
