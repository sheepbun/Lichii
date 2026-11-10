// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>
#include <tuple>
#include <unordered_map>
#include <vector>

#ifdef __linux__
#include <dlfcn.h>
#endif

#include "common/logging.h"
#include "video_core/gpu_split.h"
#include "video_core/host1x/syncpoint_manager.h"
#include "video_core/nx2_late_read.h"

namespace VideoCommon::Nx2LateRead {
namespace {

constexpr size_t MaxPages = 8192;
constexpr u64 MaxPagesPerAccess = 256;
/// Device-level pointer lookups carry no size: compare this many bytes.
constexpr size_t UnknownSize = 4;

using PageBytes = std::array<u8, Core::DEVICE_PAGESIZE>;

struct PageState {
    std::unique_ptr<PageBytes> snapshot;
    u64 snap_epoch{};
    u64 late_epoch{};
    u64 changed_epoch{};
    u32 late_frames{};
    u32 changed_frames{};
    const char* file{};
    u32 line{};
    uintptr_t caller{};
};

using SiteKey = std::tuple<const char*, u32, uintptr_t>;

struct SiteState {
    u64 changed_reads{};
    u64 changed_bytes{};
};

struct State {
    std::mutex mutex;
    std::unordered_map<u64, PageState> pages;
    std::map<SiteKey, SiteState> sites;
    std::atomic<bool> window_open{};
    u32 fence_id{};
    u32 fence_value{};
    u64 epoch{};
    u64 window_returned{};
    u64 next_frame_reads{};
    u64 presents_ahead{};
    u64 presents_waited{};
    u64 late_reads{};
    Tegra::MaxwellDeviceMemoryManager* memory{};
    const Tegra::Host1x::SyncpointManager* syncpoints{};
    std::chrono::steady_clock::time_point last_report{std::chrono::steady_clock::now()};
};

State& Get() {
    static State state;
    return state;
}

thread_local bool in_hook = false;
thread_local u64 list_epoch = 0;
std::atomic<u64> returned_presents{};

uintptr_t CallerOffset(void* return_address) {
    if (return_address == nullptr) {
        return 0;
    }
#ifdef __linux__
    Dl_info info{};
    if (dladdr(return_address, &info) != 0 && info.dli_fbase != nullptr) {
        return reinterpret_cast<uintptr_t>(return_address) -
               reinterpret_cast<uintptr_t>(info.dli_fbase);
    }
#endif
    return reinterpret_cast<uintptr_t>(return_address);
}

const u8* PagePointer(State& state, u64 page) {
    return state.memory->GetPointer<u8>(page << Core::DEVICE_PAGEBITS);
}

void Hook(u64 address, size_t size, const char* file, u32 line, void* return_address,
          bool write) {
    State& state = Get();
    if (in_hook || !state.window_open.load(std::memory_order_relaxed) ||
        !VideoCommon::GpuSplit::OwnsScheduler()) {
        return;
    }
    if (!write && list_epoch >= state.window_returned) {
        ++state.next_frame_reads;
        return;
    }
    in_hook = true;
    std::scoped_lock lk{state.mutex};
    if (state.syncpoints->IsReadyGuest(state.fence_id, state.fence_value)) {
        state.window_open.store(false, std::memory_order_relaxed);
        in_hook = false;
        return;
    }
    if (size == 1 && file == nullptr) {
        size = UnknownSize;
    }
    const u64 end = address + size;
    const u64 first = address >> Core::DEVICE_PAGEBITS;
    const u64 last = std::min((end - 1) >> Core::DEVICE_PAGEBITS, first + MaxPagesPerAccess - 1);
    if (!write) {
        ++state.late_reads;
    }
    for (u64 page = first; page <= last; ++page) {
        const u64 page_start = page << Core::DEVICE_PAGEBITS;
        const u64 begin = std::max(address, page_start) - page_start;
        const u64 stop = std::min(end, page_start + Core::DEVICE_PAGESIZE) - page_start;
        auto it = state.pages.find(page);
        if (it == state.pages.end()) {
            if (write || state.pages.size() >= MaxPages) {
                continue;
            }
            it = state.pages.emplace(page, PageState{}).first;
        }
        PageState& st = it->second;
        const bool snapped = st.snapshot && st.snap_epoch == state.epoch;
        const u8* const memory = PagePointer(state, page);
        if (write) {
            // The GPU's own write: what this frame's later reads should see.
            if (snapped && memory != nullptr) {
                std::memcpy(st.snapshot->data() + begin, memory + begin, stop - begin);
            }
            continue;
        }
        if (st.late_epoch != state.epoch) {
            st.late_epoch = state.epoch;
            ++st.late_frames;
        }
        if (!snapped || memory == nullptr) {
            continue;
        }
        if (std::memcmp(st.snapshot->data() + begin, memory + begin, stop - begin) == 0) {
            continue;
        }
        u64 differing = 0;
        for (u64 i = begin; i < stop; ++i) {
            differing += (*st.snapshot)[i] != memory[i];
        }
        if (st.changed_epoch != state.epoch) {
            st.changed_epoch = state.epoch;
            ++st.changed_frames;
        }
        st.file = file;
        st.line = line;
        st.caller = CallerOffset(return_address);
        SiteState& site = state.sites[SiteKey{file, line, st.caller}];
        ++site.changed_reads;
        site.changed_bytes += differing;
    }
    in_hook = false;
}

void Report(State& state) {
    std::vector<std::pair<u64, const PageState*>> changed;
    size_t late_pages = 0;
    for (const auto& [page, st] : state.pages) {
        late_pages += st.late_frames != 0;
        if (st.changed_frames != 0) {
            changed.emplace_back(page, &st);
        }
    }
    std::ranges::sort(changed, [](const auto& a, const auto& b) {
        return a.second->changed_frames != b.second->changed_frames
                   ? a.second->changed_frames > b.second->changed_frames
                   : a.first < b.first;
    });
    LOG_INFO(HW_GPU,
             "Late reads: {} presents ran ahead, {} waited; {} late reads of {} pages, {} pages "
             "read after the game changed the bytes read; {} reads for later command lists",
             state.presents_ahead, state.presents_waited, state.late_reads, late_pages,
             changed.size(), state.next_frame_reads);
    for (const auto& [key, site] : state.sites) {
        const auto& [file, line, caller] = key;
        LOG_INFO(HW_GPU, "  site {}:{} caller +{:#x}: {} reads of changed bytes ({} bytes)",
                 file ? file : "?", line, caller, site.changed_reads, site.changed_bytes);
    }
    for (size_t i = 0; i < std::min<size_t>(changed.size(), 24); ++i) {
        const auto& [page, st] = changed[i];
        LOG_INFO(HW_GPU, "  page {:#x}: changed in {} of {} late frames, last at {}:{} caller +{:#x}",
                 page << Core::DEVICE_PAGEBITS, st->changed_frames, st->late_frames,
                 st->file ? st->file : "?", st->line, st->caller);
    }
    // Keep only pages read late in this period, so the snapshot stays the working set.
    std::erase_if(state.pages, [](const auto& entry) { return entry.second.late_frames == 0; });
    for (auto& [page, st] : state.pages) {
        st.late_frames = 0;
        st.changed_frames = 0;
    }
    state.sites.clear();
    state.presents_ahead = 0;
    state.presents_waited = 0;
    state.late_reads = 0;
    state.next_frame_reads = 0;
}

} // Anonymous namespace

u64 SubmitEpoch() {
    return returned_presents.load(std::memory_order_relaxed);
}

void SetListEpoch(u64 epoch) {
    list_epoch = epoch;
}

bool Enabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("EDEN_NX2_LATE_READ_CHECK");
        return value != nullptr && value[0] == '1';
    }();
    return enabled;
}

void OnPresent(Tegra::MaxwellDeviceMemoryManager& memory,
               const Tegra::Host1x::SyncpointManager& syncpoints, bool ran_ahead, u32 fence_id,
               u32 fence_value) {
    State& state = Get();
    in_hook = true;
    std::scoped_lock lk{state.mutex};
    state.memory = &memory;
    state.syncpoints = &syncpoints;
    Core::g_device_access_hook.store(&Hook, std::memory_order_relaxed);
    state.window_open.store(false, std::memory_order_relaxed);
    state.window_returned = returned_presents.fetch_add(1, std::memory_order_relaxed) + 1;
    if (ran_ahead && fence_id < Tegra::Host1x::SyncpointManager::NUM_MAX_SYNCPOINTS) {
        ++state.presents_ahead;
        ++state.epoch;
        state.fence_id = fence_id;
        state.fence_value = fence_value;
        for (auto& [page, st] : state.pages) {
            const u8* const pointer = PagePointer(state, page);
            if (pointer == nullptr) {
                continue;
            }
            if (!st.snapshot) {
                st.snapshot = std::make_unique<PageBytes>();
            }
            std::memcpy(st.snapshot->data(), pointer, Core::DEVICE_PAGESIZE);
            st.snap_epoch = state.epoch;
        }
        state.window_open.store(true, std::memory_order_relaxed);
    } else {
        ++state.presents_waited;
    }
    const auto now = std::chrono::steady_clock::now();
    if (now - state.last_report >= std::chrono::seconds{2}) {
        state.last_report = now;
        Report(state);
    }
    in_hook = false;
}

} // namespace VideoCommon::Nx2LateRead
