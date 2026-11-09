// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// SPDX-FileCopyrightText: Copyright 2023 yuzu Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <chrono>

#include "common/dkb_frame_trace.h"
#include "common/logging.h"
#include "common/settings.h"
#include "common/thread.h"
#include "core/core.h"
#include "core/frontend/emu_window.h"
#ifdef HAS_LSFG
#include "video_core/renderer_vulkan/present/lsfg_common.h"
#endif
#include "video_core/renderer_vulkan/vk_present_manager.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/renderer_vulkan/vk_swapchain.h"
#include "video_core/vulkan_common/vulkan_device.h"
#include "video_core/vulkan_common/vulkan_surface.h"
#include "video_core/vulkan_common/vulkan_wrapper.h"

namespace Vulkan {


namespace {

constexpr size_t MAX_FRAMES_IN_FLIGHT = 7;
constexpr u32 MAX_PRESENT_ATTEMPTS = 3;
#ifdef HAS_LSFG
static_assert(MAX_FRAMES_IN_FLIGHT <= LSFG_MAX_TARGETS);
#endif

bool CanStoreToFrame(const vk::PhysicalDevice& physical_device, VkFormat format) {
#ifdef HAS_LSFG
    const VkFormatProperties props{physical_device.GetFormatProperties(format)};
    return (props.optimalTilingFeatures & VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT) != 0;
#else
    return false;
#endif
}

bool CanBlitToSwapchain(const vk::PhysicalDevice& physical_device, VkFormat format) {
    const VkFormatProperties props{physical_device.GetFormatProperties(format)};
    return (props.optimalTilingFeatures & VK_FORMAT_FEATURE_BLIT_DST_BIT);
}

[[nodiscard]] VkImageSubresourceLayers MakeImageSubresourceLayers() {
    return VkImageSubresourceLayers{
        .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
        .mipLevel = 0,
        .baseArrayLayer = 0,
        .layerCount = 1,
    };
}

[[nodiscard]] VkImageBlit MakeImageBlit(s32 frame_width, s32 frame_height, s32 swapchain_width,
                                        s32 swapchain_height) {
    return VkImageBlit{
        .srcSubresource = MakeImageSubresourceLayers(),
        .srcOffsets =
            {
                {
                    .x = 0,
                    .y = 0,
                    .z = 0,
                },
                {
                    .x = frame_width,
                    .y = frame_height,
                    .z = 1,
                },
            },
        .dstSubresource = MakeImageSubresourceLayers(),
        .dstOffsets =
            {
                {
                    .x = 0,
                    .y = 0,
                    .z = 0,
                },
                {
                    .x = swapchain_width,
                    .y = swapchain_height,
                    .z = 1,
                },
            },
    };
}

[[nodiscard]] VkImageCopy MakeImageCopy(u32 frame_width, u32 frame_height, u32 swapchain_width,
                                        u32 swapchain_height) {
    return VkImageCopy{
        .srcSubresource = MakeImageSubresourceLayers(),
        .srcOffset =
            {
                .x = 0,
                .y = 0,
                .z = 0,
            },
        .dstSubresource = MakeImageSubresourceLayers(),
        .dstOffset =
            {
                .x = 0,
                .y = 0,
                .z = 0,
            },
        .extent =
            {
                .width = (std::min)(frame_width, swapchain_width),
                .height = (std::min)(frame_height, swapchain_height),
                .depth = 1,
            },
    };
}

} // Anonymous namespace

PresentManager::PresentManager(const vk::Instance& instance_,
                               Core::Frontend::EmuWindow& render_window_,
                               const Device& device_,
                               MemoryAllocator& memory_allocator_,
                               Scheduler& scheduler_,
                               Swapchain& swapchain_,
                               vk::SurfaceKHR& surface_)
    : instance{instance_}
    , render_window{render_window_}
    , device{device_}
    , memory_allocator{memory_allocator_}
    , scheduler{scheduler_}
    , swapchain{swapchain_}
    , surface{surface_}
    , blit_supported{CanBlitToSwapchain(device.GetPhysical(), swapchain.GetImageViewFormat())}
    , storage_supported{CanStoreToFrame(device.GetPhysical(), swapchain.GetImageFormat())}
    , use_present_thread{Settings::values.async_presentation.GetValue()}
{
    SetImageCount();

    auto& dld = device.GetLogical();
    cmdpool = dld.CreateCommandPool({
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .pNext = nullptr,
        .flags =
            VK_COMMAND_POOL_CREATE_TRANSIENT_BIT | VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT,
        .queueFamilyIndex = device.GetGraphicsFamily(),
    });
    auto cmdbuffers = cmdpool.Allocate(image_count);

    frames.resize(image_count);
    for (u32 i = 0; i < frames.size(); i++) {
        Frame& frame = frames[i];
        frame.index = i;
        frame.cmdbuf = vk::CommandBuffer{cmdbuffers[i], device.GetDispatchLoader()};
        frame.render_ready = dld.CreateSemaphore({
            .sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO,
            .pNext = nullptr,
            .flags = 0,
        });
        frame.present_done = dld.CreateFence({
            .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO,
            .pNext = nullptr,
            .flags = VK_FENCE_CREATE_SIGNALED_BIT,
        });
        frame.storage_capable = storage_supported;
        free_queue.push_back(&frame);
    }

    if (use_present_thread) {
        present_thread = std::jthread([this](std::stop_token token) { PresentThread(token); });
    }
}

PresentManager::~PresentManager() = default;

Frame* PresentManager::GetRenderFrame() {

    // Wait for free presentation frames
    std::unique_lock lock{free_mutex};
    free_cv.wait(lock, [this] { return !free_queue.empty(); });

    // Take the frame from the queue
    Frame* frame = free_queue.front();
    free_queue.pop_front();

    // Wait for the presentation to be finished so all frame resources are free
    frame->present_done.Wait();
    frame->present_done.Reset();

    frame->batch_start = true;
    frame->present_offset = {};
    frame->real = true;
    return frame;
}

size_t PresentManager::FreeFrames() {
    std::scoped_lock lock{free_mutex};
    return free_queue.size();
}

void PresentManager::Present(Frame* frame) {
    if (use_present_thread) {
        scheduler.Record([this, frame](vk::CommandBuffer) {
            if (Common::DkbFrameTrace::Enabled()) {
                frame->queued = std::chrono::steady_clock::now();
            }
            std::unique_lock lock{queue_mutex};
            if (frame->batch_start) {
                queued_batches.fetch_add(1, std::memory_order_relaxed);
            }
            present_queue.push_back(frame);
            frame_cv.notify_one();
        });
    } else {
        scheduler.WaitWorker();
        CopyToSwapchain(frame);
        free_queue.push_back(frame);
    }
}

size_t PresentManager::MaxExtraFrames() const {
    return image_count - 1;
}

bool PresentManager::NeedsStorage(const Frame* frame, bool required) const {
    return required && frame->storage_capable && !frame->storage_view;
}

void PresentManager::RecreateFrame(Frame* frame, u32 width, u32 height, VkFormat image_view_format,
                                   VkRenderPass rd, bool storage) {
    auto& dld = device.GetLogical();

    frame->width = width;
    frame->height = height;

    const bool with_storage = storage && frame->storage_capable;
    VkImageUsageFlags storage_usage = 0;
    if (with_storage) {
        storage_usage = VK_IMAGE_USAGE_STORAGE_BIT;
    }

    frame->image = memory_allocator.CreateImage({
        .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .pNext = nullptr,
        .flags = VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT | VK_IMAGE_CREATE_EXTENDED_USAGE_BIT,
        .imageType = VK_IMAGE_TYPE_2D,
        .format = swapchain.GetImageFormat(),
        .extent =
            {
                .width = width,
                .height = height,
                .depth = 1,
            },
        .mipLevels = 1,
        .arrayLayers = 1,
        .samples = VK_SAMPLE_COUNT_1_BIT,
        .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT |
                 VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_SAMPLED_BIT | storage_usage,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
        .queueFamilyIndexCount = 0,
        .pQueueFamilyIndices = nullptr,
        .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
    });

    frame->image_view = dld.CreateImageView({
        .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
        .pNext = nullptr,
        .flags = 0,
        .image = *frame->image,
        .viewType = VK_IMAGE_VIEW_TYPE_2D,
        .format = image_view_format,
        .components =
            {
                .r = VK_COMPONENT_SWIZZLE_IDENTITY,
                .g = VK_COMPONENT_SWIZZLE_IDENTITY,
                .b = VK_COMPONENT_SWIZZLE_IDENTITY,
                .a = VK_COMPONENT_SWIZZLE_IDENTITY,
            },
        .subresourceRange =
            {
                .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                .baseMipLevel = 0,
                .levelCount = 1,
                .baseArrayLayer = 0,
                .layerCount = 1,
            },
    });

    frame->storage_view = vk::ImageView{};
    if (with_storage) {
        frame->storage_view = dld.CreateImageView({
            .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
            .pNext = nullptr,
            .flags = 0,
            .image = *frame->image,
            .viewType = VK_IMAGE_VIEW_TYPE_2D,
            .format = swapchain.GetImageFormat(),
            .components =
                {
                    .r = VK_COMPONENT_SWIZZLE_IDENTITY,
                    .g = VK_COMPONENT_SWIZZLE_IDENTITY,
                    .b = VK_COMPONENT_SWIZZLE_IDENTITY,
                    .a = VK_COMPONENT_SWIZZLE_IDENTITY,
                },
            .subresourceRange =
                {
                    .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                    .baseMipLevel = 0,
                    .levelCount = 1,
                    .baseArrayLayer = 0,
                    .layerCount = 1,
                },
        });
    }

    const VkImageView image_view{*frame->image_view};
    frame->framebuffer = dld.CreateFramebuffer({
        .sType = VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO,
        .pNext = nullptr,
        .flags = 0,
        .renderPass = rd,
        .attachmentCount = 1,
        .pAttachments = &image_view,
        .width = width,
        .height = height,
        .layers = 1,
    });
}

void PresentManager::WaitPresent() {
    if (!use_present_thread) {
        return;
    }

    // Wait for the present queue to be empty
    {
        std::unique_lock queue_lock{queue_mutex};
        frame_cv.wait(queue_lock, [this] { return present_queue.empty(); });
    }

    // The above condition will be satisfied when the last frame is taken from the queue.
    // To ensure that frame has been presented as well take hold of the swapchain
    // mutex.
    std::scoped_lock swapchain_lock{swapchain_mutex};
}

void PresentManager::PresentThread(std::stop_token token) {
    Common::SetCurrentThreadName("VulkanPresent");
    Common::SetCurrentThreadPriority(Common::ThreadPriority::High);
    Common::SetCurrentThreadToPerformanceCores();
    while (!token.stop_requested()) {
        std::unique_lock lock{queue_mutex};
        // Wait for presentation frames
        frame_cv.wait(lock, token, [this] { return !present_queue.empty(); });
        if (!token.stop_requested()) {
            // Take the frame and notify anyone waiting
            Frame* frame = present_queue.front();
            present_queue.pop_front();
            frame_cv.notify_one();

            // By exchanging the lock ownership we take the swapchain lock
            // before the queue lock goes out of scope. This way the swapchain
            // lock in WaitPresent is guaranteed to occur after here.
            void(std::exchange(lock, std::unique_lock{swapchain_mutex}));
            // A generated frame with a later batch already queued would only push that batch's
            // frames (the newer game frame included) back by a refresh each: drop it. Its own
            // batch's game frame is still shown.
            const u32 later_batches = queued_batches.load(std::memory_order_relaxed) -
                                      (frame->batch_start ? 1U : 0U);
            if (!frame->real && later_batches != 0) {
                if (frame->batch_start) {
                    queued_batches.fetch_sub(1, std::memory_order_relaxed);
                }
                DiscardFrame(frame);
            } else {
                const bool trace = Common::DkbFrameTrace::Enabled();
                const auto hold_start = trace ? std::chrono::steady_clock::now()
                                              : std::chrono::steady_clock::time_point{};
                HoldForBatch(frame, token);
                if (trace) {
                    present_trace.hold_ms += std::chrono::duration<double, std::milli>(
                                                 std::chrono::steady_clock::now() - hold_start)
                                                 .count();
                }
                CopyToSwapchain(frame);
            }

            // Free the frame for reuse
            std::scoped_lock fl{free_mutex};
            free_queue.push_back(frame);
            free_cv.notify_one();
        }
    }
}

void PresentManager::HoldForBatch(Frame* frame, std::stop_token token) {
    const auto now = std::chrono::steady_clock::now();
    if (frame->batch_start) {
        queued_batches.fetch_sub(1, std::memory_order_relaxed);
        batch_anchor = now;
        return;
    }
    // A later batch already waiting means this one is behind: show the rest at once.
    if (queued_batches.load(std::memory_order_relaxed) != 0) {
        return;
    }
    constexpr auto MAX_HOLD = std::chrono::milliseconds{100};
    const auto target = (std::min)(batch_anchor + frame->present_offset, now + MAX_HOLD);
    while (!token.stop_requested() && std::chrono::steady_clock::now() < target &&
           queued_batches.load(std::memory_order_relaxed) == 0) {
        std::this_thread::sleep_until(
            (std::min)(target, std::chrono::steady_clock::now() + std::chrono::milliseconds{1}));
    }
}

void PresentManager::RecreateSwapchain(Frame* frame) {
    swapchain.Create(*surface, frame->width, frame->height); // Pass raw pointer
    SetImageCount();
}

void PresentManager::SetImageCount() {
    // We cannot have more than 7 images in flight at any given time.
    // FRAMES_IN_FLIGHT is 8, and the cache TICKS_TO_DESTROY is 8.
    // Mali drivers will give us 6.
#ifdef HAS_LSFG
    const size_t generations = Settings::FrameGenMaxGenerations();
    const size_t queued_composites = Settings::values.frame_gen_queue_target.GetValue() + 1;
    image_count =
        std::clamp<size_t>((generations + 1) * queued_composites, swapchain.GetImageCount(),
                           MAX_FRAMES_IN_FLIGHT);
#else
    image_count = std::min<size_t>(swapchain.GetImageCount(), MAX_FRAMES_IN_FLIGHT);
#endif
    swapchain_image_count = swapchain.GetImageCount();
    swapchain_image_format = swapchain.GetImageFormat();
}

void PresentManager::DiscardFrame(Frame* frame) {
    static constexpr VkPipelineStageFlags wait_stage = VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT;
    const VkSemaphore render_ready = *frame->render_ready;
    const VkSubmitInfo submit_info{
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .pNext = nullptr,
        .waitSemaphoreCount = 1U,
        .pWaitSemaphores = &render_ready,
        .pWaitDstStageMask = &wait_stage,
        .commandBufferCount = 0U,
        .pCommandBuffers = nullptr,
        .signalSemaphoreCount = 0U,
        .pSignalSemaphores = nullptr,
    };

    std::scoped_lock submit_lock{scheduler.submit_mutex};
    void(device.GetGraphicsQueue().Submit(submit_info, *frame->present_done));
}

void PresentManager::CopyToSwapchain(Frame* frame) {
    bool requires_recreation = false;

    for (u32 attempt = 0; attempt < MAX_PRESENT_ATTEMPTS; ++attempt) {
        try {
            // Recreate surface and swapchain if needed.
            if (requires_recreation) {
#ifdef __ANDROID__
                surface = CreateSurface(instance, render_window.GetWindowInfo());
#endif
                RecreateSwapchain(frame);
            }

            // Draw to swapchain.
            return CopyToSwapchainImpl(frame);
        } catch (const vk::Exception& except) {
            const VkResult result = except.GetResult();
            if (result != VK_ERROR_SURFACE_LOST_KHR && result != VK_INCOMPLETE &&
                result != VK_ERROR_OUT_OF_DATE_KHR && result != VK_ERROR_NATIVE_WINDOW_IN_USE_KHR) {
                throw;
            }

            requires_recreation = true;
        }
    }

    DiscardFrame(frame);
}

void PresentManager::CopyToSwapchainImpl(Frame* frame) {

    // If the size of the incoming frames has changed, recreate the swapchain
    // to account for that.
    const bool is_suboptimal = swapchain.NeedsRecreation() || swapchain.IsOutDated();
    const bool size_changed =
        swapchain.GetWidth() != frame->width || swapchain.GetHeight() != frame->height;
    if (is_suboptimal || size_changed) {
        RecreateSwapchain(frame);
    }
    if (!swapchain.IsCreated()) {
        DiscardFrame(frame);
        return;
    }

    const bool trace = Common::DkbFrameTrace::Enabled();
    const auto acquire_start = trace ? std::chrono::steady_clock::now()
                                     : std::chrono::steady_clock::time_point{};
    while (swapchain.AcquireNextImage(!use_present_thread)) {
        RecreateSwapchain(frame);
    }
    const auto acquired = trace ? std::chrono::steady_clock::now()
                                : std::chrono::steady_clock::time_point{};

    const vk::CommandBuffer cmdbuf{frame->cmdbuf};
    cmdbuf.Begin({
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .pNext = nullptr,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
        .pInheritanceInfo = nullptr,
    });

    const VkImage image{swapchain.CurrentImage()};
    const VkExtent2D extent = swapchain.GetExtent();
    const std::array pre_barriers{
        VkImageMemoryBarrier{
            .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
            .pNext = nullptr,
            .srcAccessMask = 0,
            .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
            .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
            .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = image,
            .subresourceRange{
                .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                .baseMipLevel = 0,
                .levelCount = 1,
                .baseArrayLayer = 0,
                .layerCount = VK_REMAINING_ARRAY_LAYERS,
            },
        },
        VkImageMemoryBarrier{
            .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
            .pNext = nullptr,
            .srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,
            .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT,
            .oldLayout = VK_IMAGE_LAYOUT_GENERAL,
            .newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = *frame->image,
            .subresourceRange{
                .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                .baseMipLevel = 0,
                .levelCount = 1,
                .baseArrayLayer = 0,
                .layerCount = VK_REMAINING_ARRAY_LAYERS,
            },
        },
    };
    const std::array post_barriers{
        VkImageMemoryBarrier{
            .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
            .pNext = nullptr,
            .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
            .dstAccessMask = VK_ACCESS_MEMORY_READ_BIT,
            .oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
            .newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = image,
            .subresourceRange{
                .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                .baseMipLevel = 0,
                .levelCount = 1,
                .baseArrayLayer = 0,
                .layerCount = VK_REMAINING_ARRAY_LAYERS,
            },
        },
        VkImageMemoryBarrier{
            .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
            .pNext = nullptr,
            .srcAccessMask = VK_ACCESS_TRANSFER_READ_BIT,
            .dstAccessMask = VK_ACCESS_MEMORY_WRITE_BIT,
            .oldLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
            .newLayout = VK_IMAGE_LAYOUT_GENERAL,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = *frame->image,
            .subresourceRange{
                .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                .baseMipLevel = 0,
                .levelCount = 1,
                .baseArrayLayer = 0,
                .layerCount = VK_REMAINING_ARRAY_LAYERS,
            },
        },
    };

    cmdbuf.PipelineBarrier(vk::PIPELINE_STAGE_GRAPHICS_COMPUTE_TRANSFER, VK_PIPELINE_STAGE_TRANSFER_BIT, {},
                           {}, {}, pre_barriers);

    if (blit_supported) {
        cmdbuf.BlitImage(*frame->image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, image,
                         VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                         MakeImageBlit(frame->width, frame->height, extent.width, extent.height),
                         VK_FILTER_LINEAR);
    } else {
        cmdbuf.CopyImage(*frame->image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, image,
                         VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                         MakeImageCopy(frame->width, frame->height, extent.width, extent.height));
    }

    cmdbuf.PipelineBarrier(VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_ALL_GRAPHICS_BIT, {},
                           {}, {}, post_barriers);

    cmdbuf.End();

    const VkSemaphore present_semaphore = swapchain.CurrentPresentSemaphore();
    const VkSemaphore render_semaphore = swapchain.CurrentRenderSemaphore();
    const std::array wait_semaphores = {present_semaphore, *frame->render_ready};

    static constexpr std::array<VkPipelineStageFlags, 2> wait_stage_masks{
        VK_PIPELINE_STAGE_TRANSFER_BIT,
        VK_PIPELINE_STAGE_TRANSFER_BIT,
    };

    const VkSubmitInfo submit_info{
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .pNext = nullptr,
        .waitSemaphoreCount = 2U,
        .pWaitSemaphores = wait_semaphores.data(),
        .pWaitDstStageMask = wait_stage_masks.data(),
        .commandBufferCount = 1,
        .pCommandBuffers = cmdbuf.address(),
        .signalSemaphoreCount = 1U,
        .pSignalSemaphores = &render_semaphore,
    };

    // Submit the image copy/blit to the swapchain
    {
        std::scoped_lock submit_lock{scheduler.submit_mutex};
        switch (const VkResult result =
                    device.GetGraphicsQueue().Submit(submit_info, *frame->present_done)) {
        case VK_SUCCESS:
            break;
        case VK_ERROR_DEVICE_LOST:
            device.ReportLoss();
            [[fallthrough]];
        default:
            vk::Check(result);
            break;
        }
    }

    // Present
    swapchain.Present(render_semaphore);
    if (trace) {
        const auto now = std::chrono::steady_clock::now();
        const auto ms = [](auto duration) {
            return std::chrono::duration<double, std::milli>(duration).count();
        };
        PresentTrace& t = present_trace;
        ++t.presents;
        t.acquire_ms += ms(acquired - acquire_start);
        t.acquire_max_ms = (std::max)(t.acquire_max_ms, ms(acquired - acquire_start));
        t.submit_present_ms += ms(now - acquired);
        if (frame->real) {
            // From the worker queueing the game's frame to its present call: the delay frame
            // generation adds before it is shown (FIFO's queue comes on top).
            ++t.real_presents;
            t.real_delay_ms += ms(now - frame->queued);
            t.real_delay_max_ms = (std::max)(t.real_delay_max_ms, ms(now - frame->queued));
        }
        if (now >= t.next) {
            if (t.next != std::chrono::steady_clock::time_point{}) {
                LOG_INFO(Render_Vulkan,
                         "DKB present_thread presents={} hold_ms={:.1f} acquire_ms={:.1f} "
                         "acquire_max_ms={:.2f} submit_present_ms={:.1f} real_presents={} "
                         "real_delay_avg_ms={:.1f} real_delay_max_ms={:.1f}",
                         t.presents, t.hold_ms, t.acquire_ms, t.acquire_max_ms,
                         t.submit_present_ms, t.real_presents,
                         t.real_presents ? t.real_delay_ms / t.real_presents : 0.0,
                         t.real_delay_max_ms);
            }
            t = PresentTrace{.next = now + std::chrono::seconds{1}};
        }
    }
    if (Core::g_nx2_present_time_services.load(std::memory_order_relaxed) != 0) {
        const u64 present =
            Core::g_nx2_host_present_count.fetch_add(1, std::memory_order_relaxed) + 1;
        const u64 present_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
                                   std::chrono::steady_clock::now().time_since_epoch())
                                   .count();
        Core::g_nx2_host_present_times_ns[present % Core::g_nx2_host_present_times_ns.size()]
            .store(present_ns, std::memory_order_relaxed);
    }
}

} // namespace Vulkan
