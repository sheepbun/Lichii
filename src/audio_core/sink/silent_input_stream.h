// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <array>
#include <chrono>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "audio_core/common/common.h"
#include "audio_core/sink/sink.h"
#include "audio_core/sink/sink_stream.h"
#include "core/core.h"
#include "core/core_timing.h"

namespace AudioCore::Sink {

/**
 * Guest capture stream that hears silence, used when the host microphone cannot be opened or
 * started. Console hardware always has a microphone, so games do not handle a failed capture
 * open. Silence arrives in blocks at the guest sample rate, like a host callback would deliver it.
 */
class SilentInputStream final : public SinkStream {
public:
    SilentInputStream(Core::System& system_, u32 system_channels_)
        : SinkStream{system_, StreamType::In},
          tick_event{
              Core::Timing::CreateEvent("SilentInputTick", [this](s64, std::chrono::nanoseconds) {
                  ProcessAudioIn(silence, TargetSampleCount);
                  return std::optional<std::chrono::nanoseconds>{};
              })} {
        system_channels = system_channels_;
    }

    ~SilentInputStream() override {
        Stop();
    }

    bool IsReady() const override {
        return !failed;
    }

    void Start(bool = false) override {
        if (!IsReady() || !paused) {
            return;
        }
        paused = false;
        system.CoreTiming().ScheduleLoopingEvent(BlockTime, BlockTime, tick_event);
    }

    void Stop() override {
        if (paused) {
            return;
        }
        system.CoreTiming().UnscheduleEvent(tick_event);
        SignalPause();
    }

private:
    static constexpr auto BlockTime =
        std::chrono::nanoseconds{std::chrono::seconds{TargetSampleCount}} / TargetSampleRate;
    static_assert(BlockTime == std::chrono::milliseconds{5});

    std::array<s16, TargetSampleCount * 2> silence{};
    std::shared_ptr<Core::Timing::EventType> tick_event;
};

/**
 * Input sink for the "none" microphone setting. It never opens a host device, so selecting it
 * also keeps a Bluetooth headset from switching to its low-quality headset (HFP) profile.
 */
class SilentInputSink final : public Sink {
public:
    SinkStream* AcquireSinkStream(Core::System& system, u32 system_channels, const std::string&,
                                  StreamType) override {
        auto& stream =
            streams.emplace_back(std::make_unique<SilentInputStream>(system, system_channels));
        stream->SetDeviceVolume(device_volume);
        return stream.get();
    }

    void CloseStream(SinkStream* stream) override {
        std::erase_if(streams, [stream](const SinkStreamPtr& s) { return s.get() == stream; });
    }

    void CloseStreams() override {
        streams.clear();
    }

    f32 GetDeviceVolume() const override {
        return device_volume;
    }

    void SetDeviceVolume(f32 volume) override {
        device_volume = volume;
    }

    void SetSystemVolume(f32) override {}

private:
    std::vector<SinkStreamPtr> streams;
};

} // namespace AudioCore::Sink
