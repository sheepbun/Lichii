<!--
# SPDX-FileCopyrightText: Copyright 2025 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# SPDX-FileCopyrightText: 2018 yuzu Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
-->

<p align="center">
  <img src="dist/branding/bunx2.svg" alt="Bunx2 logo: a white rabbit outlined in hot pink" width="160">
</p>

# Bunx2

**Bunx2** is a personal, experimental, performance-oriented fork of
[Lichii](https://github.com/zortos293/Lichii), an open-source Nintendo Switch and Switch 2 emulator
written in C++. It exists to develop, measure and refine targeted emulator improvements for the new
*The Legend of Zelda: Ocarina of Time* remake on Nintendo Switch 2.

Bunx2 is not a new emulator and does not replace Lichii or the projects before it. Almost all of
its code comes from the projects listed under [Built on and credits](#built-on-and-credits). It is not affiliated with or endorsed by those projects, or by Nintendo.

## Purpose and philosophy

- **A personal engineering fork.** Bunx2 is where one developer tries changes, measures them, and keeps or drops them. It makes no claim to be better than Lichii.
- **Evidence before changes.** Fixes start from logs, frame dumps, RenderDoc captures and live A/B switches. A successful build or a passing test is not proof that a game renders or runs correctly.
- **Built to be shared.** Each improvement is kept small and documented so that upstream projects can reuse it. See [Upstream collaboration](#upstream-collaboration).

## Primary development target

*The Legend of Zelda: Ocarina of Time* for Nintendo Switch 2 (program id `0400486024DEC000`).
Work aims at correct rendering, steady frame pacing and lower CPU and GPU overhead in that game.

This README makes no claim about the game's compatibility, performance or completion status. They depend on the build, settings, driver and hardware, and are checked case by case. Other games may run.

## Supported environment

| Part | Supported |
|---|---|
| Operating system | Arch Linux, x86_64 |
| GPU | NVIDIA, through Vulkan |

The reference development and test system is an Intel Core i7-11700 with 32 GB of RAM and an
NVIDIA GeForce RTX 3080 (10 GB) on the `nvidia-open` driver, built with GCC 16, CMake 4 and Qt 6 from Arch. That is the configuration changes are tested on, not a minimum requirement.

Other operating systems, CPU architectures and GPU vendors are not supported. Code inherited for
Windows, macOS, Android, AMD and other platforms remains in the tree, but Bunx2 does not build or
test it.

## Current development focus

1. Game-specific correctness and compatibility.
2. Rendering fidelity and Vulkan shader correctness, including NX2 native shader lowering.
3. CPU and GPU scheduling, synchronization and latency.
4. Shader compilation, caching and frame pacing.
5. NVIDIA-specific performance investigation and optimization.
6. Reproducible builds and diagnostics on Arch Linux x86_64.
7. Maintainable improvements that can be contributed back upstream.

The investigation notes are in [NX2_NOTES.md](NX2_NOTES.md),[NX2_GPU_THREAD_SPLIT.md](NX2_GPU_THREAD_SPLIT.md) and [docs/nx2](docs/nx2), for example [Ocarina of Time frame rates](docs/nx2/ocarina-dynamic-fps.md) and
[performance experiments](docs/nx2/oot-performance-experiments.md). The diagnostic switches and tools are listed under "Debugging tools" in [AGENTS.md](AGENTS.md) and in [tools/nx2](tools/nx2).

## Building and running

Install the [dependencies](docs/Deps.md), then:

```sh
git clone https://github.com/sheepbun/Bunx2.git
cd Bunx2
cmake -S . -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DYUZU_TESTS=OFF -DYUZU_ROOM=OFF -DYUZU_ROOM_STANDALONE=OFF -DUSE_DISCORD_PRESENCE=OFF \
  -DVulkanHeaders_FORCE_BUNDLED=ON -DVulkanUtilityLibraries_FORCE_BUNDLED=ON
cmake --build build --parallel 16 --target yuzu yuzu-cmd
./build/bin/bunx2
```

This builds two programs:

- `build/bin/bunx2`, the Qt frontend (CMake target `yuzu`).
- `build/bin/eden-cli`, the SDL frontend (CMake target `yuzu-cmd`; it keeps its inherited name).

`sudo cmake --install build` installs both, with the `io.github.sheepbun.bunx2` desktop entry,
icons, AppStream metadata and MIME types. [dist/packaging/arch/bunx2-git](dist/packaging/arch/bunx2-git) is a draft pacman package of the same install. The [build guide](docs/Build.md) covers tests and build types, and [CONTRIBUTING.md](CONTRIBUTING.md) the remotes and push guard for development.

Bunx2 publishes no prebuilt releases, and its builds do not check for updates.

### Games, keys and firmware

Use only games, keys and firmware that you dumped from hardware you own, as the law where you live
allows. Bunx2 includes none of them and does not help you obtain them. Do not attach game files,
keys, firmware or private account data to issues, logs or pull requests.

### Existing data

Bunx2 uses the same directories as Lichii and Eglantine: `~/.config/eglantine`,
`~/.local/share/eglantine` and `~/.cache/eglantine`. Settings, saves, keys and shader caches from a Lichii installation keep working without migration, and Bunx2 does not move or delete them. Back up your saves before testing experimental builds.

## Upstream collaboration

Lichii's and Eglantine's maintainers and contributors are welcome to reuse, cherry-pick, adapt or merge any distinct improvement made here, under the project's license. That is why changes are kept focused and documented with the evidence behind them. Whether a change is accepted upstream is up to that project's review and licensing requirements. Bunx2 never submits changes upstream automatically and claims no endorsement from upstream projects.

Before fixing a problem, Bunx2 first checks Lichii and Eglantine for an existing fix and ports it with credit to its source. See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).

## Built on and credits

Bunx2 exists because of the projects below and the people who built them. Their copyright notices stay in the source files, and Git history keeps their commits and authors.

| Project | How Bunx2 relates to it | Source |
|---|---|---|
| **Lichii** | Bunx2's direct upstream: Bunx2 is a fork of it. Lichii, maintained by Zortos (zortos293) and its contributors, continued Eglantine's Switch 2 work with renderer fixes, diagnostics and game support that Bunx2 builds on. | [zortos293/Lichii](https://github.com/zortos293/Lichii) |
| **Eglantine** | Lichii's upstream: a Switch 2 emulator derived from Eden, yuzu and Sudachi, published by Djihads (Djihads80) and the Eglantine contributors. Its README credits developer Camille LaVey with starting the project. | [Eglantine-Community/Eglantine](https://github.com/Eglantine-Community/Eglantine), formerly Djihads80/Eglantine |
| **Eden** | Eglantine's base: a Nintendo Switch emulator started by developer Camille LaVey and derived from yuzu and Sudachi. | [eden-emu/eden](https://git.eden-emu.dev/eden-emu/eden) |

Third-party libraries are listed under **Help > Bunx2
Dependencies** in the application and in [externals](externals), with their licenses in
[LICENSES](LICENSES) and [.reuse/dep5](.reuse/dep5).

## License and disclaimer

Bunx2 is distributed under the GNU General Public License, version 3 or later
([LICENSE.txt](LICENSE.txt)). Files keep the copyright and license notices in their SPDX headers,
including GPL-2.0-or-later code from yuzu and Citra, and third-party code keeps its own licenses.
All of those notices remain in effect.

Bunx2 is provided as is, without warranty. It is intended only for software you own and have
legally obtained.

"Nintendo Switch" and "The Legend of Zelda" are trademarks of Nintendo. Bunx2 is not affiliated
with, endorsed by or sponsored by Nintendo. Names of other projects are used only to credit them.
