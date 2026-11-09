// SPDX-License-Identifier: GPL-3.0-or-later

#include <catch2/catch_test_macros.hpp>

#include "video_core/nx2.h"

namespace {

using Tegra::Nx2SemaphoreWaiters;

constexpr u64 Word = 0x2b030;
constexpr u64 Other = 0x2b034;

void RetireAll(Nx2SemaphoreWaiters& waiters, u64 address) {
    while (const auto value = waiters.LastQueued(address)) {
        waiters.OnRetired(address, *value);
    }
}

} // namespace

TEST_CASE("Queued GPU releases meet acquires before their writes", "[video_core][semaphore_waiters]") {
    auto& waiters = Nx2SemaphoreWaiters::Get();
    REQUIRE_FALSE(waiters.QueuedMatches(Word, 5, Nx2SemaphoreWaiters::AcquireGequal));
    waiters.OnQueued(Word, 4);
    waiters.OnQueued(Word, 6);
    waiters.OnQueued(Other, 1);
    REQUIRE(waiters.LastQueued(Word) == 6U);
    REQUIRE(waiters.LastQueued(Other) == 1U);
    REQUIRE(waiters.QueuedMatches(Word, 5, Nx2SemaphoreWaiters::AcquireGequal));
    REQUIRE_FALSE(waiters.QueuedMatches(Word, 7, Nx2SemaphoreWaiters::AcquireGequal));
    // An equal acquire is met by any queued value, as a written one would latch it.
    REQUIRE(waiters.QueuedMatches(Word, 4, Nx2SemaphoreWaiters::AcquireEqual));
    REQUIRE_FALSE(waiters.QueuedMatches(Word, 5, Nx2SemaphoreWaiters::AcquireEqual));
    REQUIRE(waiters.QueuedMatches(Word, 2, Nx2SemaphoreWaiters::AcquireMask));
    REQUIRE_FALSE(waiters.QueuedMatches(Other, 6, Nx2SemaphoreWaiters::AcquireGequal));
    // Written releases retire in order; the last queued value stays the next base.
    waiters.OnRetired(Word, 4);
    REQUIRE(waiters.LastQueued(Word) == 6U);
    REQUIRE_FALSE(waiters.QueuedMatches(Word, 4, Nx2SemaphoreWaiters::AcquireEqual));
    waiters.OnRetired(Word, 6);
    REQUIRE_FALSE(waiters.LastQueued(Word).has_value());
    REQUIRE_FALSE(waiters.QueuedMatches(Word, 5, Nx2SemaphoreWaiters::AcquireGequal));
    RetireAll(waiters, Other);
}

TEST_CASE("Queuing a release latches the waiter it meets", "[video_core][semaphore_waiters]") {
    auto& waiters = Nx2SemaphoreWaiters::Get();
    const int owner{};
    waiters.Add(&owner, Word, 3, Nx2SemaphoreWaiters::AcquireGequal);
    waiters.OnQueued(Word, 2);
    REQUIRE_FALSE(waiters.Latched(&owner));
    waiters.OnQueued(Other, 3);
    REQUIRE_FALSE(waiters.Latched(&owner));
    waiters.OnQueued(Word, 3);
    REQUIRE(waiters.Latched(&owner));
    waiters.Remove(&owner);
    REQUIRE_FALSE(waiters.Latched(&owner));
    RetireAll(waiters, Word);
    RetireAll(waiters, Other);
}

TEST_CASE("Unretired releases are bounded", "[video_core][semaphore_waiters]") {
    auto& waiters = Nx2SemaphoreWaiters::Get();
    for (u32 value = 1; value <= 4096; ++value) {
        waiters.OnQueued(Word, value);
    }
    REQUIRE(waiters.QueuedMatches(Word, 1, Nx2SemaphoreWaiters::AcquireEqual));
    // A dropped write never retires: past the bound the stale entries are discarded.
    waiters.OnQueued(Word, 5000);
    REQUIRE_FALSE(waiters.QueuedMatches(Word, 1, Nx2SemaphoreWaiters::AcquireEqual));
    REQUIRE(waiters.LastQueued(Word) == 5000U);
    RetireAll(waiters, Word);
}
