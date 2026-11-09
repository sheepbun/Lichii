# CMake Options

To change these options, add `-DOPTION_NAME=NEWVALUE` to the command line.

- On Qt Creator, go to Project -> Current Configuration

Notes:

- To set a boolean variable to on, use `ON` for the value; to turn it off, use `OFF`
- TYPE is always boolean unless otherwise specified
- Format:
  - `OPTION_NAME` (TYPE DEFAULT) DESCRIPTION

## Options

### Dependencies

These options control dependencies.

- `YUZU_DOWNLOAD_TIME_ZONE_DATA` (ON) Always download time zone binaries
  - Currently, build fails without this
- `YUZU_TZDB_PATH` (string) Path to a pre-downloaded timezone database (useful for nixOS and Gentoo)
- `YUZU_USE_BUNDLED_SDL3` (OFF) Download a prebuilt SDL3

### Miscellaneous

- `ENABLE_WEB_SERVICE` (ON) Enable multiplayer service
- `ENABLE_WIFI_SCAN` (OFF) Enable WiFi scanning (requires iw on Linux) - experimental
- `ENABLE_CUBEB` (ON) Enables the cubeb audio backend
  - This option is subject for removal.
- `YUZU_TESTS` (ON) Compile tests - requires Catch2
- `ENABLE_LTO` (OFF) Enable link-time optimization
  - You may be better off appending `-flto=thin` to compiler args
- `USE_FASTER_LINKER` (OFF) Check if a faster linker is available
- `YUZU_INSTALL_UDEV_RULES` (OFF) Install udev rules to enable hidraw access
  - Needed for gyroscopes
- `ENABLE_DEBUG_TOOLS` (OFF) Enables debugging and development tools, see [tools](../tools/README.md).
- `ENABLE_WERROR` (ON) Enables warnings as errors (-Werror).
- `LICHII_FUZZERS` (OFF) Builds the libFuzzer harnesses for the game-file parsers and instruments the whole build with ASan and UBSan, see [Fuzzing](./Fuzzing.md).
  - Clang only

### Flavors

These options control executables and build flavors.

- `YUZU_LEGACY` (OFF): Apply patches to improve compatibility on some older GPUs at the cost of performance
- `NIGHTLY_BUILD` (OFF): This is only used by CI. Do not use this unless you're making your own distribution and know what you're doing.
- `YUZU_STATIC_BUILD` (OFF) Attempt to build using static libraries if possible
  - Not supported on Linux
- `ENABLE_UPDATE_CHECKER` (OFF) Enable update checking functionality
  - `LICHII_BUILD_TAG` (empty) Published `build-N` tag. Builds without it never check for updates.
  - `LICHII_BUILD_PLATFORM` (empty) Release asset platform and variant, `linux-x86_64` or `linux-x86_64-pgo`. CI sets this from its build matrix.
- `YUZU_DISABLE_LLVM` (OFF) Do not attempt to link to the LLVM demangler
  - Really only useful for CI or distribution builds

**Desktop only**:

- `YUZU_CMD` (ON) Compile the SDL-based frontend (eden-cli)
- `YUZU_ROOM` (OFF) Compile dedicated room functionality into the main executable
- `YUZU_ROOM_STANDALONE` (OFF) Compile a separate executable for room functionality

### Desktop

The following options are desktop only.

- `ENABLE_LIBUSB` (ON) Enable the use of the libusb input backend (HIGHLY RECOMMENDED)
- `ENABLE_OPENGL` (ON) Enable the OpenGL graphics backend
  - You probably shouldn't turn this off.

### Qt

Also desktop-only, but apply strictly to Qt

- `ENABLE_QT` (ON) Enable the Qt frontend (recommended)
- `ENABLE_QT_TRANSLATION` (OFF) Enable translations for the Qt frontend. Release builds turn it on; it needs Qt Linguist Tools (`lrelease`)
- `YUZU_USE_BUNDLED_QT` (OFF) Download bundled Qt binaries
  - Not recommended; use the system Qt.
- `YUZU_QT_MIRROR` (string) What mirror to use for downloading the bundled Qt libraries
- `YUZU_USE_QT_MULTIMEDIA` (OFF) Use QtMultimedia for camera support
- `YUZU_USE_QT_WEB_ENGINE` (OFF) Use QtWebEngine for web applet implementation (requires the huge QtWebEngine dependency; not recommended)
- `USE_DISCORD_PRESENCE` (OFF) Enables Discord Rich Presence (Qt frontend only)

### Retired Options

The following options were a part of Eden at one point, but have since been retired.

- `ENABLE_OPENSSL` - MbedTLS was fully replaced with OpenSSL in [#3606](https://git.eden-emu.dev/eden-emu/eden/pulls/3606), because OpenSSL straight-up performs better.
- `ENABLE_SDL2` - While technically possible to *not* use SDL2 on desktop, this is **NOT** a supported configuration under any means, and adding this matrix to our build system was not worth the effort.
- `YUZU_USE_CPM` - This option once had a purpose, but that purpose has long since passed us by. *All* builds use CPMUtil to manage dependencies now.
  - If you want to *force* the usage of system dependencies, use `-DCPMUTIL_FORCE_SYSTEM=ON`.
- `YUZU_USE_EXTERNAL_SDL` - This is now handled automatically. It was included even after CPM for purposes that have not applied for a very long time.

See `src/dynarmic/CMakeLists.txt` for additional options--usually, these don't need changed
