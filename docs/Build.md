# Building Lichii

> [!WARNING]
> This guide is intended for developers ONLY. If you are not a developer or packager, you are unlikely to receive support.

Lichii is built, run and tested on one machine:

| Part | This machine |
|---|---|
| OS | Arch Linux, x86_64 |
| CPU | Intel Core i7-11700, 8 cores, 16 threads |
| RAM | 32 GB |
| GPU | NVIDIA GeForce RTX 3080, 10 GB, `nvidia-open` driver |
| Toolchain | GCC 16, CMake 4, system Qt 6 |

This guide covers only that machine.

## Dependencies

Install the [dependencies](Deps.md) first.

## Clone

```sh
git clone https://github.com/sheepbun/Lichii.git
cd Lichii
```

Then add the read-only remotes and the push guard from [CONTRIBUTING.md](../CONTRIBUTING.md).

## Configure

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DYUZU_TESTS=OFF -DYUZU_ROOM=OFF -DYUZU_ROOM_STANDALONE=OFF -DUSE_DISCORD_PRESENCE=OFF \
  -DVulkanHeaders_FORCE_BUNDLED=ON -DVulkanUtilityLibraries_FORCE_BUNDLED=ON
```

With Ninja installed, add `-G Ninja`; otherwise CMake uses Unix Makefiles.

Available `CMAKE_BUILD_TYPE` values:

- `Release`
- `RelWithDebInfo` (debug symbols, needed for readable crash stacks; the executable is large)
- `Debug` (for stepping through code that would otherwise be optimized out)

See [CMake Options](Options.md) and the root `CMakeLists.txt` for other options. The defaults are usually best.

To use Qt Creator instead, open `CMakeLists.txt` with File -> Open File or Project (Ctrl+O), choose `RelWithDebInfo` or `Release`, and hit "Configure Project".

## Build

```sh
cmake --build build --parallel 16 --target yuzu yuzu-cmd
```

The programs are:

- `build/bin/lichii`, the Qt frontend
- `build/bin/eden-cli`, the command-line frontend

Both read their config from `~/.config/eglantine` and their data from `~/.local/share/eglantine`. The log is `~/.local/share/eglantine/log/eden_log.txt`.

## Tests

```sh
cmake -S . -B build -DYUZU_TESTS=ON -DBUILD_TESTING=ON
cmake --build build --parallel 16
ctest --test-dir build --output-on-failure --parallel 16
```

## Troubleshooting

If configure fails:

- *Carefully* re-read the [dependencies guide](Deps.md).
- Clear the CPM cache (`.cache/cpm`) and the CMake cache (`build/CMakeCache.txt`).
- Evaluate the error and find any related settings.
- See the [CPM docs](CPMUtil.md) to see if you need to bundle a package.
