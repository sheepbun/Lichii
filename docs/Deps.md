# Dependencies

Lichii is built only on Arch Linux x86_64 (see the [build guide](Build.md)). It needs:

* [GCC](https://gcc.gnu.org/) 11 or later (Arch ships GCC 16)
* **[CMake](https://www.cmake.org/)** 3.31+
* **[Git](https://git-scm.com/)**
* **[Python3](https://www.python.org/downloads/)** 3.10+, to download external repositories and run `tools/nx2`
* Qt 6 for the Qt frontend, and optionally Qt Creator (`qtcreator`)

## Externals

The following are handled by Eden's externals:

* [FFmpeg](https://ffmpeg.org/) (should use `-DYUZU_USE_EXTERNAL_FFMPEG=ON`)
* [SDL3](https://www.libsdl.org/download-2.0.php) 3.2.10+ (Use `-DYUZU_USE_BUNDLED_SDL2=ON` to reduce compile time)

All other dependencies will be downloaded and built by [CPM](https://github.com/cpm-cmake/CPM.cmake/) if `YUZU_USE_CPM` is on, but will always use system dependencies if available:

* [Boost](https://www.boost.org/users/download/) 1.57.0+
* [Catch2](https://github.com/catchorg/Catch2) 3.0.1 if `YUZU_TESTS` or `DYNARMIC_TESTS` are on
* [fmt](https://fmt.dev/) 8.0.1+
* [lz4](http://www.lz4.org)
* [nlohmann\_json](https://github.com/nlohmann/json) 3.8+
* [OpenSSL](https://www.openssl.org/source/) 3+
* [ZLIB](https://www.zlib.net/) 1.2+
* [zstd](https://facebook.github.io/zstd/) 1.5+
* [enet](http://enet.bespin.org/) 1.3+
* [Opus](https://opus-codec.org/) 1.3+

Vulkan 1.3.274+ is also needed:

* [VulkanUtilityLibraries](https://github.com/KhronosGroup/Vulkan-Utility-Libraries)
* [VulkanHeaders](https://github.com/KhronosGroup/Vulkan-Headers)
* [SPIRV-Tools](https://github.com/KhronosGroup/SPIRV-Tools)
* [SPIRV-Headers](https://github.com/KhronosGroup/SPIRV-Headers)

Certain other dependencies will be fetched by CPM regardless. System packages *can* be used for these libraries, but many are either not packaged by most distributions OR have issues when used by the system:

* [SimpleIni](https://github.com/brofield/simpleini)
* [DiscordRPC](https://github.com/eden-emulator/discord-rpc)
* [cubeb](https://github.com/mozilla/cubeb)
* [libusb](https://github.com/libusb/libusb)
* [VulkanMemoryAllocator](https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator)
* [sirit](https://github.com/eden-emulator/sirit)
* [httplib](https://github.com/yhirose/cpp-httplib) - if `ENABLE_UPDATE_CHECKER` or `ENABLE_WEB_SERVICE` are on
  * This package is known to be broken on the AUR.
* [cpp-jwt](https://github.com/arun11299/cpp-jwt) 1.4+ - if `ENABLE_WEB_SERVICE` is on

On x86_64:

* [xbyak](https://github.com/herumi/xbyak) - 7.22 or earlier is recommended

If `DYNARMIC_TESTS` is on:

* [oaknut](https://github.com/merryhime/oaknut) 2.0.1+

## Command

Always review what you're running before you hit Enter:

```sh
sudo pacman -Syu --needed base-devel boost catch2 cmake enet ffmpeg fmt git glslang libzip lz4 ninja nlohmann-json openssl opus qt6-base qt6-multimedia qt6-charts sdl3 zlib zstd zip unzip vulkan-headers vulkan-utility-libraries libusb spirv-tools spirv-headers
```

* Building with QT Web Engine requires `qt6-webengine` as well.
* Proper Wayland support requires `qt6-wayland`.
* Graphics debugging needs `vulkan-validation-layers`; frame captures need `renderdoc`.
* CPM bundles any library from the externals list that is not installed.

## All Done

You may now return to the **[root build guide](Build.md)**.
