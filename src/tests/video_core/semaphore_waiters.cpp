// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#include <array>
#include <atomic>
#include <thread>

#include <catch2/catch_test_macros.hpp>

#include "video_core/nx2.h"

using Tegra::Nx2SemaphoreWaiters;

namespace {

constexpr u64 Word = 0x2b030;
constexpr u64 Other = 0x2b034;

void RetireAll(Nx2SemaphoreWaiters& waiters, u64 address) {
    while (const auto value = waiters.LastQueued(address)) {
        waiters.OnRetired(address, *value);
    }
}

} // namespace

TEST_CASE("Semaphore waiters latch on the writes that meet them", "[video_core][nx2_waiters]") {
    Nx2SemaphoreWaiters waiters;
    const int equal{}, gequal{}, mask{};
    REQUIRE_FALSE(waiters.Latched(&equal));
    REQUIRE(waiters.Add(&equal, 0x1000, 5, Nx2SemaphoreWaiters::AcquireEqual));
    REQUIRE(waiters.Add(&gequal, 0x1000, 5, Nx2SemaphoreWaiters::AcquireGequal));
    REQUIRE(waiters.Add(&mask, 0x2000, 0x4, Nx2SemaphoreWaiters::AcquireMask));

    waiters.OnWrite(0x1000, 4);
    waiters.OnWrite(0x2000, 0x3);
    waiters.OnWrite(0x3000, 0xffffffff);
    REQUIRE_FALSE(waiters.Latched(&equal));
    REQUIRE_FALSE(waiters.Latched(&gequal));
    REQUIRE_FALSE(waiters.Latched(&mask));

    waiters.OnWrite(0x1000, 6);
    REQUIRE_FALSE(waiters.Latched(&equal));
    REQUIRE(waiters.Latched(&gequal));
    waiters.OnWrite(0x1000, 5);
    REQUIRE(waiters.Latched(&equal));
    // A later write that no longer meets it keeps the latch.
    waiters.OnWrite(0x1000, 0);
    REQUIRE(waiters.Latched(&equal));
    waiters.OnWrite(0x2000, 0x6);
    REQUIRE(waiters.Latched(&mask));

    // Adding again starts unlatched, on the new word.
    REQUIRE(waiters.Add(&equal, 0x1004, 7, Nx2SemaphoreWaiters::AcquireEqual));
    REQUIRE_FALSE(waiters.Latched(&equal));
    waiters.OnWrite(0x1000, 7);
    REQUIRE_FALSE(waiters.Latched(&equal));
    waiters.OnWrite(0x1004, 7);
    REQUIRE(waiters.Latched(&equal));

    waiters.Remove(&gequal);
    REQUIRE_FALSE(waiters.Latched(&gequal));
    waiters.Remove(&equal);
    waiters.Remove(&mask);
    waiters.Remove(&mask);
    REQUIRE_FALSE(waiters.Latched(&equal));
    REQUIRE_FALSE(waiters.Latched(&mask));
}

TEST_CASE("Semaphore waiters refuse owners past the table and reuse freed slots",
          "[video_core][nx2_waiters]") {
    Nx2SemaphoreWaiters waiters;
    std::array<int, Nx2SemaphoreWaiters::MaxWaiters + 1> owners{};
    for (size_t i = 0; i < Nx2SemaphoreWaiters::MaxWaiters; ++i) {
        REQUIRE(waiters.Add(&owners[i], 0x1000 + i * 4, 1, Nx2SemaphoreWaiters::AcquireGequal));
    }
    const int* const extra = &owners.back();
    REQUIRE_FALSE(waiters.Add(extra, 0x8000, 1, Nx2SemaphoreWaiters::AcquireGequal));
    waiters.OnWrite(0x8000, 1);
    REQUIRE_FALSE(waiters.Latched(extra));
    // An owner already in the table moves to its new word.
    REQUIRE(waiters.Add(&owners[3], 0x9000, 2, Nx2SemaphoreWaiters::AcquireGequal));
    waiters.OnWrite(0x100c, 1);
    REQUIRE_FALSE(waiters.Latched(&owners[3]));
    waiters.OnWrite(0x9000, 2);
    REQUIRE(waiters.Latched(&owners[3]));

    waiters.Remove(&owners[5]);
    REQUIRE(waiters.Add(extra, 0x8000, 1, Nx2SemaphoreWaiters::AcquireGequal));
    waiters.OnWrite(0x8000, 1);
    REQUIRE(waiters.Latched(extra));
    REQUIRE_FALSE(waiters.Latched(&owners[5]));
}

TEST_CASE("Semaphore waiters see what the writer wrote before the latch",
          "[video_core][nx2_waiters]") {
    Nx2SemaphoreWaiters waiters;
    constexpr u32 Rounds = 2000;
    std::array<u32, 4> words{};
    std::atomic<u32> round{};
    std::jthread writer([&] {
        for (u32 value = 1; value <= Rounds; ++value) {
            while (round.load(std::memory_order_acquire) < value) {
                std::this_thread::yield();
            }
            for (u32 word = 0; word < words.size(); ++word) {
                words[word] = value;
                waiters.OnWrite(0x1000 + word * 4, value);
            }
        }
    });
    std::array<int, 4> owners{};
    for (u32 value = 1; value <= Rounds; ++value) {
        for (u32 word = 0; word < words.size(); ++word) {
            REQUIRE(waiters.Add(&owners[word], 0x1000 + word * 4, value,
                                Nx2SemaphoreWaiters::AcquireGequal));
        }
        round.store(value, std::memory_order_release);
        for (u32 word = 0; word < words.size(); ++word) {
            while (!waiters.Latched(&owners[word])) {
                std::this_thread::yield();
            }
            REQUIRE(words[word] == value);
            waiters.Remove(&owners[word]);
        }
    }
}

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
