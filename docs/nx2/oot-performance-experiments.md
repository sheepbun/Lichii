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

## C4-06: frame critical path and host-GPU waits on the back (investigating)

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
