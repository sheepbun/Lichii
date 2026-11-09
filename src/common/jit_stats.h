// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <atomic>
#include <cstdint>

// CPU JIT counters for the GPU profile report (EDEN_NX2_GPU_PROFILE): blocks compiled and cache
// invalidations, summed over every core.
namespace Common::JitStats {
inline std::atomic<std::uint64_t> blocks_compiled{};
inline std::atomic<std::uint64_t> range_invalidations{};
inline std::atomic<std::uint64_t> full_clears{};
} // namespace Common::JitStats
