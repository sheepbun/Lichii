# Ocarina of Time performance experiments

## Target and ownership (2026-11-09)

Target: correct gameplay approaching 170 real frames/s (5.882 ms/frame), excluding
generated or duplicated frames. The local test host is an Intel i7-11700 (8 cores,
16 threads), NVIDIA RTX 3080 10 GiB (driver 615.78.08), 32 GiB RAM, Linux/GNOME
Wayland and a 170 Hz display. It is not the Windows host described in AGENTS.md.

Integration branch: `perf/oot-170hz`, starting at `ec0c23dc0f`. Existing Claude
workers own GPUBack buffer/pipeline work (`t3/98a75d06`) and frame generation /
presentation (`t3/126c3bb0`). C4 owns front-thread work. Changes and builds in the
integration checkout are coordinated with their orchestrator.

The existing baseline is about 29.5 real presents/s in the saved Hyrule Field
scene. This is not evidence of a fundamental engine limit, nor of correct game
simulation at 170 Hz. Emulation speed and simulation timing still need separate
validation before any high-frame-rate claim.

## Measurement protocol

Use the existing isolated `.cache/rig` through `tools/lab.sh` (exclusive flock),
never a second emulator or the owner's configuration. `tools/sbench.sh` restores
the owner cache snapshot, loads the newest save, and runs four frame-counted
pan/walk cycles. Settings: High GPU accuracy, docked, 1x resolution, owner VSync
and 170 FPS limit, Bounded 2 ms native async draws. Frame generation remains off.
Record actual settings from each run's log. Reject runs with concurrent compiler
load, memory pressure, different starting scenes, or crashes. Keep binary hashes,
logs, screenshot, input results and frame-time response per run.

`frame_times.fps`, median and p99 use the full measurement window. Its
`intervals_ms` contains only the last 120 rounded intervals: do not use it to
claim full-window 1% or 0.1% lows. Presents measure renderer delivery, not distinct
simulation steps or display scanout. Verify screenshots and game timing separately.

## C4-01: GPU split cache contention (retained)

Hypothesis: the front's hot ring positions share cache space with `back_engine`,
which the back writes at engine switches. Separating them reduces cache transfers
without changing command ordering, synchronization or rendered output.

Sources: [upstream issue 807](https://github.com/Eglantine-Community/Eglantine/issues/807)
and existing fork commit `b22e54a24b47f4de4fcb332825c76f9a3185cad7` (layout-only port
of `886ac17e004156c34596d2664f4f9c4824f0ed44`). The issue's Mario Kart measurements
do not establish an Ocarina gain. `upstream/nx2` was fetched and is fully contained
in the starting branch (376 local commits ahead, zero behind). PR 686's macro
stack experiment and PR 794's GPU split diff were inspected; neither supplies
this layout in this branch.

Candidate: `alignas(128)` on `write_position` and `back_engine` in `gpu_split.h`.
No experiment switch or behavior change. Baseline binary:
`.cache/C4/base/lichii`, SHA-256
`ec959a31d1911012ddf75e27438841ba46055f6c2f79ff5dafb50bc667d0879e`.

Validation: existing tests, then baseline/candidate/candidate/baseline gameplay
runs using separately preserved binaries. Retain only a repeatable improvement;
otherwise revert and preserve the negative result here. The existing GCC Release
build is used for comparable local A/B; clang is not installed on this host.

Next candidates, in order: reduce measured macro JIT overhead, then investigate
batching macro writes. Front-thread gains may be hidden while GPUBack remains
the critical path; do not infer an FPS gain from microbenchmarks alone.

Results (FG off, High accuracy, unchanged settings):

| Run | Scenario | Layout | FPS | Median ms | p99 ms | Max ms |
|---|---|---|---:|---:|---:|---:|
| C4_a2_base | Pan/walk | Baseline | 28.84 | 34.97 | 61.32 | 100.65 |
| C4_b_aligned | Pan/walk | Aligned | 29.97 | 33.58 | 59.65 | 90.29 |
| C4_c_aligned | Pan/walk | Aligned | 29.99 | 33.66 | 55.32 | 95.93 |
| C4_d_base | Pan/walk | Baseline | 29.39 | 34.47 | 61.27 | 92.89 |
| C4_e_static_base | Fixed field | Baseline | 29.72 | 31.89 | 40.93 | 74.53 |
| C4_f_static_aligned | Fixed field | Aligned | 30.34 | 31.24 | 44.13 | 48.21 |

The two candidate gameplay runs beat both baseline runs (about 3% gain in mean
FPS across the pairs). The fixed-field pair supports the direction but its p99
was worse, so this is not a proven improvement in every tail metric. The six
runs are a small sample, not evidence across all scenes. The fixed-view windows
had no runtime pipeline builds. Pan/walk windows built 70–92 pipelines; none
reported held draws. The first `C4_a_base` run was
excluded after another worker accidentally terminated its process during startup. Fixed-view screenshots show the same scene and no
obvious visual regression; animation differs. All 1040 existing test cases passed.
The interrupted assistant turn did not interrupt `C4_f_static_aligned`; its
normal completion and cleanup were recovered.

Candidate SHA-256: `7490906c509713c7cf638dbc7761a42866629d8da78181f04a47acb74bc62cbc`.
Artifacts: `.cache/C4/`, `.cache/rig/runs/C4_*` (local, not uploaded).

## C4-02: full-window frame statistics

Add 1% and 0.1% lows, p99.9 and population standard deviation to `frame_times`.
This measures all retained intervals rather than the 120-element rounded tail.
A synthetic 1000-interval regression with one early 101 ms spike fails on the
old implementation; tests also cover empty windows and rounded-up tail counts.
This is measurement support, not an FPS optimization.

## C4-03: macro DMA handoffs (investigating)

`W2_h1_trace` shows thousands of DMA read drains per second, chiefly macro
0xe2c. Instrumentation and concurrent build load contaminated that trace's FPS;
use it to locate work, not as a performance baseline.

Existing origin branch `egla91/back-macros-nx2`, commit `26aad9dc55`, runs eligible
memory-reading macros on the back. It needs effect analysis, register ownership
tracking and APIs absent here; evaluate as a separate substantial port.
Origin PR 1032 skips some waits and query flushes, but explicitly acknowledges
unresolved stale-query correctness. Do not adopt that shortcut.

The metrics implementation passes 289 assertions in 18 debug-server test cases.
`C4_g_metrics_base` is a live smoke check, not a comparison run: its first launch
omitted the beside-binary switch file. Logged settings match the earlier runs,
and split/fence defaults match the file, but subsequent baseline and candidate
packages carry byte-identical copies explicitly. It reported full-window lows
successfully. Benchmark wrappers now reject mismatched switch files and preserve
binary hashes before launching.

C4-03 candidate ports `26aad9dc55b02218e15e6652afa4471c89cc3995` from
`origin/egla91/back-macros-nx2`, leaving narrower-wait and asynchronous-readback
prerequisites out. It copies macro arguments into the command ring; runs only
statically classified FIFO-reading, non-data-RAM macros on the back; and tracks
back-owned registers/shadow values until the front needs them. Other DMA reads
retain full draining and query flushing. Source attribution and source tests
are preserved. The differential-write diagnostic from `f04151ff36` is parked
in `.cache/C4/backmacro-verify.patch` for a separate validation build.

The port passed 1046 test cases (including new effect-analysis and register-
ownership cases). Gameplay runs `C4_i_backmacros` and `C4_j_backmacros` measured
30.545 and 30.396 FPS, against `C4_h_macro_base` at 30.010 FPS. The candidate's
1% lows were 13.445 and 12.852 FPS, versus 13.323 FPS for that baseline. This
small sample does not yet establish a gain worth the substantial port. The
reverse baseline `C4_k_macro_base` measured 27.427 FPS while another worker ran
large DWARF `perf report` processing outside the lab lock; exclude it from the
comparison and repeat under quiet conditions. The cause of its slowdown is not
proven, but the run violates isolation.

The port is parked, not integrated. Its complete patch, including its added
test file, is `.cache/C4/backmacros-candidate.patch` (apply-checked against
`f67125787d`), with an immutable binary in `.cache/C4/backmacros/`. No runtime
register escape, MME failure or device loss was seen in the two candidate runs;
this does not replace differential correctness validation. Preserve the port
for testing against faster GPUBack implementations.

## C4-04: one macro DMA handoff (rejected)

The current front path fully drains the back, then makes another synchronous
back request for query flushing, plus a separate request when buffer data is
dirty. Hypothesis: combining these into one ordered `GpuSplit::Run` removes a
round trip and an idle transition at each DMA read. Unlike a narrower dependency
wait, it still executes after every earlier entry and completes both cache
flushes before reading memory on the front.

Candidate changes only `FrontDmaRead`: trace pending queries, test and flush
buffer dirtiness, then flush query reports, in one back callback. Preserve the
buffer-before-query ordering and per-word memory reads. `Run` already ends with
a full `Drain`, including the back's idle hook; the caller waits for exactly
that completion. No skip-flush switch or early read is introduced. The existing
upstream narrower-wait implementation (`55601ecbbe`) and the earlier unsafe-read
quarantine (`1bb45da83d`) were inspected; neither supplies this smaller change.

Builds and heavy profile processing now share the benchmark lock. C4's wrapper
also records CPU frequencies, available memory and interfering compiler/perf
processes every two seconds, rejecting runs with detected interference.

Results with quiet-host monitoring: pan/walk baseline `C4_l_macro_base` 29.756
FPS, candidate `C4_m_fused` 29.305 FPS. Fixed view candidate `C4_n_static_fused`
30.467 FPS versus subsequent baseline `C4_o_static_base` 30.711 FPS; respective
p99 47.812/45.494 ms and 1% lows 19.771/19.783 FPS. Fixed windows compiled no
pipelines. This small sample does not prove a regression, but shows no reason
to retain the change. The further repeats were cancelled at the exploratory
2% fixed-view improvement threshold; the patch was reverted. Preserved at
`.cache/C4/fused-candidate.patch` and `.cache/C4/fused/`. All 1041 test cases
passed before benchmarking it.

W2b completed with no integration commits. Its uniform-bank skip remains
excluded: per-page CPU tracking does not cover all aliases and host writers.
The next investigation is acquire/fence synchronization. Upstream `a4f6287327`
(PR 949) requires immediate-release settings for its drain-free checks, so
that fast path is ineligible at the High accuracy used by this mission.


## C4-05: acquire and fence handoffs (rejected)

A temporary `EDEN_NX2_ACQUIRE_PROBE` patch preserves all waits and separates
initial checks, resume polls, legacy acquires and SEM_EXECUTE. The fixed-scene
run `C4_s_acquire_probe` recorded 23 roughly two-second gameplay windows:
135672 initial drains / 3535.890 ms, 5253136 resume drains /
385.481 ms. Only 21403 resumes were already latched before their
drain, accounting for 39.582 ms. An early-latch return would recover
less than one millisecond per second here, so do not pursue it first. Probe FPS
is not a performance comparison. Patch/binary preserved under `.cache/C4/`;
probe removed from source.

Next isolated candidate: defer the initial failed acquire's `ReleaseFences`
callback, explicitly publish it, and suspend the channel. The existing resume
path still fully drains and checks the semaphore; unsplit execution is unchanged.
This permits scheduler interleaving but adds a suspension and command resnapshot,
so benchmark it before considering retention. Patch:
`.cache/C4/defer-fences-candidate.patch`. No experiment flag is integrated.

Result: all 1041 test cases passed, but fixed-view `C4_t_defer_fences`
measured 28.80 FPS, p99 53.79 ms, 1% low 14.20 FPS and 0.1% low 7.34 FPS,
against `C4_o_static_base` at 30.71 FPS, 45.49 ms, 19.78 FPS and 16.73 FPS.
The quiet-host monitor recorded no interference. One run is not a precise
effect size, but every metric moved the wrong way and the tails by a wide
margin; the extra suspension and resnapshot cost more than the overlap gains.
Reverted; patch kept at the path above.

## C4-06: frame critical path and host-GPU waits on the back (retained)

The present statistics in `C4_o_static_base` (fixed view) split each 32.5 ms
frame into about 7.7 ms of game CPU work and 24.8 ms blocked in nx2:vi2 229
waiting for the GPU threads; composite and vblank pacing are about 0 ms. The
guest syncpoint is incremented when the back processes it, so this wait is the
GPU threads catching up, not host GPU completion. `W2_h1_trace` shows the back
idle about 254 ms/s (the game's CPU phase) and an unattributed "other" zone of
about 252 ms/s. OoT runs at `frames_ahead=0` because running ahead rereads data
the game has already rewritten (see `nx2_gsv.h`). Each GPU-thread millisecond
therefore adds directly to the frame.

`C4_u_offcpu` (`.cache/C4/offcpu.sh`, analyzed with `offcpu.py`) recorded
GPU-thread scheduling with DWARF stacks for 5 s in the fixed view; FPS during
perf was 30.9, versus 30.6 before. GPUBack was off-CPU 418 ms/s:

| Blocking stack | ms/s | waits/s |
|---|---:|---:|
| `RasterizerVulkan::ReleaseFences` (condition variable, host GPU fence) | 227.5 | 1282 |
| `BackLoop` waiting for work | 164.9 | 663 |
| `FlushRegion` → `Scheduler::Finish` from `TickWork` (CPU reads of GPU data) | 24.8 | 154 |

The front's largest wait (168 ms/s) is the drain in
`ProcessSemaphoreTriggerMethod`. A Switch 2 acquire not already met after
draining calls `ReleaseFences`, i.e. `WaitPendingFences(force)`. That waits
for the host GPU to finish everything submitted, because at High accuracy a
fence-flagged payload release writes guest memory (and latches waiters) only
when its host fence passes. About 1000 acquires per second take this path; only
about 145 per second actually suspend. Each pays a full host-GPU round trip.
That is ≈ 7.5 ms per frame on the critical path, and accounts for the "other" zone.

Candidate (queued release): when the back records a payload release whose
memory write waits for a host fence, register it (`Nx2SemaphoreWaiters::OnQueued`,
retired when the write runs or is discarded) and latch suspended waiters it
meets. A Switch 2 acquire not met in memory but met by a queued release then
continues without `ReleaseFences`. On the back, a `WaitForIdle` barrier keeps the
channel's later host work behind the release's work. Resuming a suspended
acquire does the same. CPU-visible semaphore memory is still written only after
the host fence passes. Patches: `.cache/C4/queued-release-candidate.patch` and
`queued-release2-candidate.patch`. Binaries: `.cache/C4/queued_release*/`.
All 1041 test cases passed.

| Run | Switches | FPS | 1% low | p99 ms | Fence wait ms |
|---|---|---:|---:|---:|---:|
| C4_v1_off | off | 30.67 | 23.59 | 39.77 | 24.6 |
| C4_v2_qr | queued release | 32.36 | 25.84 | 37.55 | 22.9 |
| C4_v3_qrnw | + non-forcing fence-action acquire | 32.14 | 19.80 | 39.83 | 24.2 |
| C4_v4_off | off | 30.61 | 21.44 | 42.34 | 24.4 |
| C4_w_site_probe | queued release (site counters) | 32.69 | 22.96 | 37.76 | — |
| C4_x1_off | off | 31.01 | 18.39 | 42.16 | 24.6 |
| C4_x2_qr | queued release, latched at queue | 32.36 | 23.18 | 37.91 | 23.9 |
| C4_x3_qrcp | + non-blocking 1 ms commit poll | 31.98 | 22.24 | 41.12 | 22.8 |
| C4_x4_off | off | 30.88 | 22.93 | 39.86 | 24.7 |

Queued release averages 32.47 FPS against 30.79 (+5.5%) and improved p99 in
every pairing. Neither the fence-action no-wait nor the commit poll helped; both
are dropped. No semaphore-timeout, MME or device-loss warnings appeared.
Visual correctness and other scenes are not yet validated.

Remaining forced waits: the initial acquire path still calls `ReleaseFences`
about 1100 times a second (175-250 ms/s in total), though the registry matches
none of them. Which call pays the host-GPU backlog varies between runs (initial
or resume poll); the total stays about the same.

Those initial-path waits turned out to be releases that bypass `CounterReport`.
Switch 2 QMD max releases (flag bit 19) and reduction releases (bit 26) call
`SignalFence` directly, e.g. the QMD-completion counters at device `0x2b030`
and `0x86000`, which each acquire wants exactly one past. They are now queued with
the value they will write, predicted on the back from the last queued value
(or memory) because releases are written in queue order. Registry entries
retire after their memory write and are bounded at 4096. With these,
forced initial-path waits fell from ~2700 to ~490 per 2 s (≈253 → ≈22 ms/s);
no acquire was latched during a wait any more (`C4_r*`).

Retained as `6d025225af`, unconditional, with unit tests
(`[semaphore_waiters]`; 1045 test cases pass). Final A/B/B/A against the
W3 merge (`307b305b29`):

| Run | Scene | Binary | FPS | 1% low | p99 ms |
|---|---|---|---:|---:|---:|
| C4_f1_base | Fixed | merge base | 29.95 | 22.85 | 42.07 |
| C4_f2_qr | Fixed | queued release | 32.12 | 20.71 | 38.26 |
| C4_f3_qr | Fixed | queued release | 32.66 | 25.25 | 37.80 |
| C4_f4_base | Fixed | merge base | 30.44 | 22.98 | 42.23 |
| C4_g1_base | Pan/walk | merge base | 29.94 | 14.00 | 57.23 |
| C4_g2_qr | Pan/walk | queued release | 31.33 | 14.52 | 56.01 |
| C4_g3_qr | Pan/walk | queued release | 30.82 | 13.53 | 53.30 |
| C4_g4_base | Pan/walk | merge base | 30.07 | 12.09 | 59.87 |

Fixed view +7.3%, pan/walk +3.6%. Fixed-view screenshots differ from the
baseline no more than two baselines differ from each other (RMSE 0.085 vs
0.091); no semaphore-timeout, MME or device-loss warnings. Pan/walk windows
still built 79-115 pipelines.

The bottleneck moved. `C4_u2_offcpu_qr` shows the back idle 323 ms/s
(was 165), and the front sleeping 128 ms/s in 2485 short polls
of channels suspended on semaphores the game's CPU releases, plus 163 ms/s
waiting for command lists. Guest cores are about 50% idle in
`PhysicalCore::Idle`. The GPU threads now mostly wait on the game's own threads,
which release command chunks as they finish writing them. Next: the guest
side (single-thread speed of those threads, host core placement) and
the 7.7 ms per-frame CPU phase before the GPU work starts.

Prediction check (`28d7b7a5d9`, `EDEN_NX2_RELEASE_VERIFY=1`): `C4_i_verify_fixed`
and `C4_i_verify_walk` checked 954,368 retiring releases across boot, menus and
gameplay. None mismatched its prediction and none were dropped. Only Ocarina of
Time is installed here; run the switch on other Switch 2 titles before relying
on it for them. If a title writes queued semaphore words from the CPU, drop the
word's queued entries when the CPU write is seen.

## C4-07: host thread placement and EPP (inconclusive)

`C4_h_hostab` alternated 15 s fixed-view windows within one run on the
retained build. Five as-is windows measured 30.94-33.03 FPS (mean 32.22).
Pinning (GPUBack to CPU 4, GPU to 5, SMT siblings 12/13 idle, guest cores to
0-3/6/7, everything else to the remaining siblings) gave 32.45 and 33.37.
EPP `performance` gave 32.69 and 31.45. Both together gave a single 34.11.
Within-run drift (±3%) is as large as any of these, so no code change
follows; EPP was restored to `balance_performance`.

## C4-08: where the guest threads spend their time (analysis)

`C4_j_guest_oncpu` (`.cache/C4/oncpu.sh`) sampled the six CPUCore threads at
2 kHz with DWARF stacks for 5 s on the retained build (32.3 FPS during perf).
Together they were on CPU about 1.13 cores. Of that, 60.5% was JIT guest
code, about 28% the idle loop spinning (`PhysicalCore::Idle` →
`sched_yield`, `clock_gettime`) and 26.6% kernel time overall. Host work the
guest triggers is small. CPU writes to GPU-tracked pages
(`HandleRasterizerWrite` → `RasterizerVulkan::OnCPUWrite`, contending with the
back for the rasterizer `SpinRecursiveMutex`) took 2.4%. Re-protecting cached
pages (`RasterizerMarkRegionCached` → `mprotect`) took about 1.2%, and SVC and
scheduler emulation about 3%. The guest cores are off CPU about half the time.

Neither guest JIT throughput nor emulator overhead on the guest threads
bounds the frame. The game's threads and the GPU threads wait on each
other: render threads release command chunks that suspended channels poll for,
and present waits for the last chunk. CPU reads of GPU-written memory
(`TickWork` → `FlushRegion` → `Scheduler::Finish`, about 35 ms/s on the back)
are another full host-GPU wait the game blocks on, about 1 ms per frame.

Assessment against 170 FPS (5.88 ms): the game CPU phase alone is about 7.7 ms
per frame, before any GPU-thread work, while OoT must wait for each frame
(`frames_ahead=0`; running ahead rereads rewritten data). Real 170 FPS is not
reachable without overlapping that phase with GPU work, which W1 showed
corrupts frames unless the GPU thread snapshots the data it reads late. The
remaining measured levers, each at most a few percent: CPU-read downloads
(about 1 ms per frame), the 50 µs suspended-channel poll, rasterizer lock
contention from CPU writes, and the back's flat per-draw cost.

## P5-01: frame generation held up the GPU thread (retained)

With frame generation at a 170 FPS target, Hyrule Field ran at 28.3 game FPS against
33.2 without it (fixed view, `P5_fg_*`). `Composite` (GPU thread) took a presentation frame
per generated frame and waited when FIFO had not shown the previous batch yet, so the game
waited too, and the pacer then planned more generations for the longer interval.
`cf0038e749` generates only into frames free at that moment and has the present thread drop a
generated frame once a later batch is queued (that batch's game frame is still shown).
A/B/B/A at 170 Hz: panning 25.2/24.7 -> 26.8/27.3 game FPS (27.4-27.6 in three repeats),
shown rate unchanged at ~166, 98-99% single-vblank. With the window on a 60 Hz monitor the
old build fell to 21.9-29.5 while the new one held 32.7-35.0.

## P5-02: what the rest of frame generation's cost is (host setup, not fixed here)

FG on still costs ~15% of game FPS (`P5_z*`, `P5_st*`): the GPU threads retire the same
instructions per frame but need ~12% more cycles (IPC 1.28 -> 1.15 on GPUBack, LLC misses
+9%) at the same 4.37 GHz, and wait ~5 ms longer per frame. Composite CPU time is 0.35 ms,
the SMT siblings of the pinned GPU cores are 67-78% idle, and a separate present queue (blits
off the graphics queue) changed nothing (rejected). Cause: mutter's primary GPU is the Intel
iGPU (a 60 Hz monitor on its HDMI port), so every frame for the NVIDIA-attached 170 Hz DP-3
monitor crosses system memory. With FG on, gnome-shell's i915 render engine goes from ~19%
to ~69% busy, iGPU DRAM traffic from 0.6 to 2.7 GB/s and PCIe I/O from 1.0 to 4.1 GB/s
(`uncore_imc`), contending with the CPU for L3 and DRAM. Making the NVIDIA card mutter's
preferred primary GPU (udev tag `mutter-device-preferred-primary`, then a new session)
should remove the copies; not applied, it is a desktop-wide change for the owner.

## P5-03: rejected or neutral

- Guest idle spin (`EDEN_NX2_IDLE_SPIN_US` 0/30/100/300): within run-to-run noise.
- `YUZU_BUILD_PRESET=native` (Rocket Lake, AVX-512 available): 32.8/32.0 vs 33.0/32.5 still,
  30.4/31.5 vs 31.7/31.1 pan. No gain.

## P5-04: profile-guided and link-time optimized build (retained for the local package)

The GPU threads are not limited by instruction selection: about half of GPUBack's uops
come from the legacy decoder rather than the uop cache, and its ~3.4 M L3 misses/s are
spread over the whole draw path (pipeline and image view lookups, page table walks, buffer
bindings, a few percent each). GCC PGO trained on one rig session (boot, newest save, four
pan/walk cycles, FG on; `-fprofile-update=prefer-atomic`, use with
`-fprofile-partial-training`), then LTO on top:

| A/B/B/A (FG off) | Still FPS | Pan FPS |
|---|---|---|
| current vs PGO | 31.2/31.5 vs 32.3/33.0 | 30.0/30.1 vs 31.3/31.4 |
| PGO vs PGO+LTO | 34.7/34.2 vs 35.6/36.6 | 32.8/33.0 vs 33.2/33.4 |

About +8% still and +5% panning over the plain Release build; pan p99 also improved.
An A/A check (identical settings in all four runs, `P5_fmc*`) put the middle pair of an
A/B/B/A 6% above the outer pair, so single A/B/B/A sequences are not enough here. Eight
strictly alternating fixed-view runs (`P5_alt*`) confirm it: every PGO+LTO window
(30.6-33.2 FPS, mean 32.2) beat every plain-build window (28.6-30.5, mean 29.7), +8%. Logs
show the same errors and warnings as the plain build, no device loss. The tree's
`-Werror=all` trips on PGO-only maybe-uninitialized warnings, so the use build appends `-w`
through a compiler launcher; LTO reports a pre-existing Boost.System ODR mismatch
(error-category vtables) that the normal link also resolves arbitrarily. Installed as
`lichii-custom 125.r3` from `.cache/build-pgo`; recipe in `.cache/P5/` (`train.sh`) and
`.cache/pkg/PKGBUILD`. Profiles go stale as code changes: retrain after rebasing.

## P5-05: running a frame ahead (measured, not shippable as is)

`EDEN_NX2_FRAMES_AHEAD=1` on the PGO build: still 40.5/40.2 vs 35.4/33.7 FPS, panning
38.6/37.6 vs 33.8/32.6 (+15%), with the known one-frame flashes. The late-read check finds
the GPU threads reading data the game already rewrote through buffer uploads
(`MappedUploadMemory`), CPU-write inlining (`WriteMemory`), uniform binds and the query and
semaphore counters at device pages `0x2b000` and `0x86000`.

A write guard (present returns early, a guest write that faults on GPU-tracked memory waits
for the frame's fence; `.cache/P5/write-guard-prototype.patch`) recovers nothing: 31.1 vs
30.4 FPS. Each frame about five such writes waited ~15 ms each, i.e. the game writes tracked
memory right after the present returns. Overlap therefore needs copy-on-write snapshots of the
pages written during the window, with the GPU threads' reads for the earlier frame redirected
to them (device reads, `GetPointer` users included), not a wait.

## P5-06: running ahead with snapshots of pending pages (rejected)

`.cache/P5/ahead-safe-experiment.patch` (`EDEN_NX2_AHEAD_SAFE=1`): at an early present GPUBack
copied every page its buffers held CPU-modified, a write to clean tracked memory copied its page
first and was held back until the front reached the next frame's command lists, and GPUBack's
device reads of copied pages read the copies. It copied ~10,000-12,500 pages (40-50 MB) in
7-10 ms per frame and redirected ~8,500 reads. Panning fell to 19-22 FPS with p99 ~220 ms, and
frames still broke: white screens, missing grass, smeared ground. A first version that uploaded
all CPU-modified buffer ranges instead of copying them re-protected pages the game rewrites
every frame (~600 write faults per frame) and fell to 25-27 FPS. Running ahead stays off for
Ocarina; the remaining late reads (query/semaphore words, QMDs, image uploads) and the volume of
CPU-modified memory make it a much larger change than this.


## S8: resume audit and flash measurement repair (2026-11-10)

Starting Bunx2: `e91bd764b9`. Fetched Lichii `bcb37c49f7` and Eglantine
`78743f30c2` (already contained); merged Lichii into main before further work.
New commits: #1196 ADPCM polarity, #1197 damaged NUP RomFS texture stand-ins,
#1200 recommended-settings prompt, #1154 pipeline variable build correction,
#1199 stale GPU mapping removal. Read the implementation diffs. The NUP
stand-ins are substitutes, not recovered original textures. This merge alone
is not a measured performance improvement. Origin remains the only writable remote.

Recovered candidates (all local, not integrated):

- Twelve-PR bundle, `bundle1`: earlier pan/walk ABBA gave 37.04/41.75 FPS vs
  main 39.12/38.42. No gain established; bundled hypotheses cannot attribute a cause.
- #1161 head `44520a76f6`, merge `3538955568`, `m1161`: 39.79/41.96 vs main
  38.79/40.96. No gain established. The later `2102a72272` removes a duplicate
  `MarkGpuThread` call; it was NOT in the preserved m1161 binary.
- Frame-ahead probes: main 48.0, m1161 52.75 FPS. One run per build, different
  traversal progress, rendering unvalidated: not accepted. Late-read trace
  `s5_m1161_late` reports hundreds of pages per two seconds read after their
  bytes changed. This is a risk signal, not by itself proof every read is wrong.
- Resolution probes did not establish an effect. Several previous runs ended
  with a forced kill because Qt confirmation handling was broken. The current
  driver sends Alt+Y to the lab process's windows; s5/s6 closed normally.

These numbers are the driver's `walk.fps`: input-observed presents divided by
requested input duration. They are NOT an independent simulation-rate measure.
The separate `walk.window.fps` uses a later sliding window and is typically
higher; do not mix the two. Old owner-profile runs actually logged Low GPU
accuracy, handheld, 1x, Wait for native shaders, FG off, speed limit 100%.
They are not the documented High/docked reference profile. Their time-bounded
walk also covers more game frames in a faster build. Fixed-view and explicit
warmup options, plus measurement start/end status, were added to the driver
for controlled comparisons. No 170-real-FPS claim is supported.

### Diagnostic defect

`s6_flash_a0` saved 118 supposed flashes; `s6_flash_a1` saved 204. Invalid as a
correctness comparison: `WhiteHistory::Push(white)` discarded its argument and
swapped zeroes forever. Every logged median was zero. Saved title-logo transitions
and ordinary sky/cloud frames were misclassified. Only 54/69 saved frames even
showed gameplay HUD hearts; those counts are still not verified artifacts.

Compiled the actual struct extracted from `nx2_gsv.h` into
`.cache/rig/white-history-repro.cpp`. Feeding 32 samples of 3000 returned median 0
and exit 1. Replacing the swap with `counts[next] = white` and removing unused
`last` returned 3000 and exit 0 under `-Wall -Wextra -Werror`. Fix: `abac4f95f1`.
The watcher remains a brightness-spike heuristic: camera motion, sun and scene
transitions can trigger it; readback can change race timing. Zero detections
would not prove rendering correctness.

New candidate `0e99209090` combines the existing #1161 scratch merge with current
main and the diagnostic repair. Build: `.cache/rig/build`, source `.cache/rig/wt`,
RelWithDebInfo, GCC, tests enabled. This is a diagnostic candidate, not a proven
optimization. Owner build and configuration remain untouched.

### Upstream/provenance refresh

- Lichii #1161 unchanged at `44520a76f6`; #1166 `0fd821f6c3` remains open.
  Read #1166 diff: shared SPIR-V function DontInline control targets compilation
  time and may cost runtime. Needs a separate shader-cold experiment, not inference
  from warm steady-state throughput.
- Lichii #715 `04a3f6f8ae` (open): snapshot storage for two UI shader hashes while
  running ahead. Diff read; this is narrower than a fix for all late reads and
  changes the behind-GPU wait too. Not ported as a general flash fix.
- Quixomatic/Lichii `feat/debug-flash-trigger`, `c0350265ed`, base `99ac3d5955`:
  asynchronous 64x36 GPU flash probe and later-frame dump. Different diagnostic,
  not a fix to WhiteHistory. Requires distinguishing trigger from captured frame.
- Fresh fork scan writes `.cache/rig/forks-s8/branches.jsonl`; old complete scan
  retained in `.cache/rig/forks/`. No unverified fork change accepted.

Compatibility API now reports 393 entries (was 390). Three new reports saved in
`.cache/rig/compat-s8.json`: `dc80f998-c1cc-4365-aaa2-66701cdb9e3d` (14700KF,
4070 Super, Ubuntu, Low, Zora's Domain, 45-60 FPS and first-visit shader stalls);
`d9afebb9-8934-4730-9a4b-792c99ebbf2f` (5800X, RX9060XT, SteamOS Mesa 26.2-devel,
High, crash after intro); `90e246ab-0896-4a91-b745-0f0507712f2a` (10940X, 3080Ti,
Windows, movement/fairy-associated lag and exit crash). No exact SHA, repeatable
input trace or confirmed regression baseline in these reports. Triage only.

Next: finish build/tests; compare frames-ahead 0/1 with corrected detector,
measurement-range filtering and saved-frame inspection; separate correctness
captures from performance runs. Keep frame-ahead disabled by default. Then
profile the highest measured cost on the current baseline.


S8 fixed-view diagnostic results (owner profile, FG off, 4 s warmup, 24 s
measurement; one run each, not a performance acceptance test):

| Run | Frames ahead | Input-count presents/s | Window presents/s | p50 ms | p99 ms | Gameplay spike detections |
|---|---:|---:|---:|---:|---:|---:|
| s8_fixed_a0 | 0 | 43.25 | 43.619 | 23.80 | 32.63 | 0 |
| s8_fixed_a1 | 1 | 55.83 | 56.076 | 17.73 | 23.05 | 0 |

Measurement present ranges `(2681,3800]` and `(2670,4126]`; saved title/menu
spikes (21/22) excluded. Gameplay/end screenshots inspected: same rock-side
Hyrule Field view, no obvious whole-frame corruption in these four stills.
No proof about transient errors or correct simulation timing. Audio stream
playing at -10.2 dBFS peak; confirmed-popup exits 3.44/2.92 s. Driver 615.78.08,
GCC 16.2.1, RelWithDebInfo (-O2), restored owner shader snapshot and warm NVIDIA
disk cache; no concurrent build during captures. The fork survey ran alongside
these diagnostic runs, so do not use them as quiet-host performance proof.

Build metadata caveat: `s8-current` source was `0e99209090`, but generated
`scm_rev.cpp` still stamped `3538955568`; incremental build did not regenerate
it when only the git checkout changed. Run label/source SHA and binary hash
were retained. Reconfigured explicitly, rebuilt and preserved `s8-stamped`
with embedded SHA `0e99209090`. Driver `keep` now rejects a generated-SHA/source
mismatch and refuses to overwrite a binary label. Use explicit CMake configure
before each new source revision. The first ctest passed the 1209-case main
executable but six auxiliary tests were Not Run because their binaries had not
been built; built those targets for a complete repeat.


Full validation after building the auxiliaries: all 7 CTest entries passed
(34.20 s); the main executable passed 71,681,001 assertions in 1209 cases.
The extracted WhiteHistory check additionally passes steady brightness,
a single spike, ring wrap and return-to-dark cases.

`s8_walk_a1` on the correctly stamped candidate produced 36 brightness-spike
flags in `(2700,3869]`. Inspected pairs 3180 and 3386: an enemy hit effect and
the camera turning toward sun/clouds, respectively. The walk encounters an
enemy and Link loses health; combat timing is another source of run-to-run
variation. These flags are not a rendering-regression verdict. The fixed-view
workload is preferable for performance attribution. No frame-ahead behavior
has been accepted into main.


## S9: reference-profile synchronization experiments (2026-11-10, in progress)

Main `abac4f95f1` contains Lichii `bcb37c49f7` and Eglantine `78743f30c2`;
fresh fetch: 306 commits ahead of Lichii, zero behind. Read-only push URLs
remain disabled. Current-main full CTest from S8: 7/7 passed (39.09 s).

The S8 cycle profile used `0e99209090` (#1161 candidate), owner/Low settings
and frames-ahead 1. Its BackLoop sample percentages cannot be transferred to
current main or the reference profile. New run `s9_reference_profile`: main,
High/docked/1x, Bounded 2 ms native draws, FG off, frames-ahead 0, 100% speed,
170 cap, fixed rock-side Hyrule Field view; 8 s warmup, 24 s requested measure.
GCC 16.2.1 RelWithDebInfo (-O2), NVIDIA 615.78.08, owner shader snapshot restored,
NVIDIA disk cache warm. GPU_PROFILE and 499 Hz perf sampling enabled for diagnosis.

Input-observed presents/s 32.83; sliding-window 33.249, median 31.072 ms,
p99 36.634 ms, max 78.575 ms. These are guest-present counts, not independent
simulation progress. The steady last-600-present block averaged 29.893 ms,
including 18.363 ms waiting for the GPU fence. GPUBack ~521 CPU ms/s, GPU front
~461 CPU ms/s. Back split waits ~464–486 wall ms/s, but only ~22–23 ms/s
actually spinning. Front acquire drains ~308–336 wall ms/s. These times overlap
and must not be summed into a frame budget. Perf (user cycles, no lost samples)
puts Backoff at 24.02% of the two GPU threads' samples; BackLoop only 3.39%.
Profiling/counters may be costly: diagnose with them, benchmark without them.

Audio probe playing (-10.2 dBFS), confirmation answered and exit 2.67 s; no
device loss or semaphore-wait warnings. End screenshot inspected: expected
rock-side field, no obvious corruption; a still does not rule out transient bugs.

Spin candidate provenance: GonzaCass/Lichii `deck/spin`, commit
`64e49c9f74c4c0931ee358652580a3c84d104413`, author GonzaCass, parent
`3282bc789185e40313fd8435ddf35f0badd4ede1`; `git cherry` reports unique patch.
Cherry-picked with attribution to `scratch/s9-spin-budget`, `34ea7c07fe`;
14-line diff read. Also found upstream experimental `b6fd58a647` (Djihads80
`007a132f6f` port), with equivalent adjustable budgets and stricter parsing.
Neither changes wait ordering. The GonzaCass port applies cleanly to main.
Preserved `s9-spin` has matching source/embedded SHA; builds both frontends.
This is an experiment only: its environment switches must not merge.

Hypotheses: shortening back spins may free CPU resources; it may only save
idle CPU; or extra sleeps may worsen wake latency. ABBA planned: 8192/256/256/8192
back spins, same binary, front unchanged at 16384; reference settings as above,
no profiler/counters. Next measured target is front Backoff, not a presumed
large back-spin cost. Results and acceptance decision pending.


S9 back-spin ABBA finished (source/binary `34ea7c07fe`, quiet host; no profiler):

| Run | Back spins | Input presents/s | Window presents/s | p50 ms | p99 ms | GPUBack CPU ms/s | Front CPU ms/s |
|---|---:|---:|---:|---:|---:|---:|---:|
| s9_spin_a1 | 8192 | 38.54 | 38.825 | 27.791 | 34.602 | 529 | 504 |
| s9_spin_b1 | 256 | 39.67 | 39.997 | 26.927 | 31.913 | 517 | 514 |
| s9_spin_b2 | 256 | 39.46 | 40.062 | 26.148 | 33.391 | 527 | 520 |
| s9_spin_a2 | 8192 | 39.46 | 39.796 | 27.241 | 33.654 | 541 | 517 |

Decision: not accepted. Input-rate mean +1.45% is smaller than baseline
repeat spread 2.36%; window-rate mean +1.83%. The result does not establish
a throughput gain or rule out a small one. GPUBack CPU means 535 vs 522 ms/s;
no dramatic spin saving. Full distributions including p99.9 and lows retained
in `.cache/rig/s9-back-spin-results.json` and each run's result. With fewer than
1000 window intervals, the 0.1% low is only the worst frame, not a robust tail
estimate. All four have empty pre-run busy lists, audio playing and confirmed
normal exits (2.30–3.40 s); no device loss. Identical error categories: 121
NCA messages, 11 unmapped reads, one NIFM message. These pre-existing errors
are not a clean correctness bill. End screenshot b2 inspected in the same
field view. No simulation-rate or transient-rendering proof.

Next: front budget 256 vs default 16384 with back fixed at 8192, alternating
C/A/C/A. Same binary/profile/warmup/window and FG/frames-ahead off. This tests
the largest sampled front cost without altering acquire ordering.

Upstream audit: `.cache/rig/s9-upstream-review.md` records source diffs,
#1161/#1166 still unchanged, and compatibility reports still 393. New host
commit-budget branch `ef5b9f6434` applies its new headroom cap on Windows,
not this Linux host; index-check `64374673b7` is diagnostic. Neither is an
applicable performance fix. Fork scan has 765 branch rows; full coverage
is not asserted. No optimization has been accepted or merged in S9.
