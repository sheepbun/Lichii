// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include "common/common_types.h"
#include "video_core/host1x/gpu_device_memory_manager.h"

namespace Tegra::Host1x {
class SyncpointManager;
}

// EDEN_NX2_LATE_READ_CHECK=1: finds guest memory the GPU thread reads for a frame after the
// game, running a frame ahead, already rewrote it for the next one. When a present returns before
// its frame's fence, every page the GPU thread read late in the last 2 s is copied; until the GPU
// thread reaches that fence, the bytes each of its reads covers are compared with the copy (its
// own writes update the copy). Every 2 s the log lists the read sites (file:line, or the caller's
// offset in the binary for addr2line) and pages that saw changed bytes.
namespace VideoCommon::Nx2LateRead {

[[nodiscard]] bool Enabled();

/// Counts presents that returned to the game; command lists are stamped with it when submitted.
[[nodiscard]] u64 SubmitEpoch();

/// GPU thread: the stamp of the command list it runs. Reads for lists the game submitted after the
/// present belong to the next frame and are not checked.
void SetListEpoch(u64 epoch);

/// Present thread, after the present's wait. `ran_ahead`: the frame's fence is not reached yet.
void OnPresent(Tegra::MaxwellDeviceMemoryManager& memory,
               const Tegra::Host1x::SyncpointManager& syncpoints, bool ran_ahead, u32 fence_id,
               u32 fence_value);

} // namespace VideoCommon::Nx2LateRead
