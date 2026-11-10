#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Isolated Ocarina of Time benchmark rig.

Runs a preserved build against a private copy of the owner's configuration, saves and shader cache
(.cache/rig), drives it through the debug server and records real-frame statistics. Never touches
the owner's config, data or running emulator.

    oot_rig.py snapshot                      copy the owner's config, nand and shader cache
    oot_rig.py keep <label> [--build DIR]    preserve bunx2 and eden-cli from a build as bins/<label>
    oot_rig.py run --bin <label> --tag <tag> [--game oot|starfox] [--frontend qt|cli]
                   [--profile owner|high|low|reference] [--seconds 24] [--floor 20] [--beat FPS|TAG]
                   [--env NAME=VALUE ...] [--set key=value ...] [--mute]
    oot_rig.py boot --bin <label> --tag <tag> ...   boot to gameplay and leave it running
    oot_rig.py call '<json request>' [--out png]    one debug server request to the lab instance
    oot_rig.py stop
    oot_rig.py table <tag> [<tag> ...]              one line per run

A run mashes A to the newest save, then walks and pans the camera in time-bounded segments. It
ends at once when a segment is under --floor fps (not playable), when the save takes longer than
--load-limit to load, or when the run is not ahead of --beat after --prove seconds. The Qt frontend
is closed the way the owner closes it: a close request, then Yes on the confirmation.

Every run holds an exclusive lock (.cache/rig/lock), restores the snapshot first, and writes
.cache/rig/runs/<tag>/result.json with settings, binary hash, host state and the statistics.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
RIG = os.path.join(REPO, ".cache", "rig")
SNAP = os.path.join(RIG, "snap")
HOME = os.path.join(RIG, "home")
BINS = os.path.join(RIG, "bins")
RUNS = os.path.join(RIG, "runs")
PORT = 6546  # not the owner's 6545
SERVER = f"127.0.0.1:{PORT}"
ROMS = os.path.expanduser("~/ROMs/nsw2")
GAMES = {
    "oot": {"id": "0400486024DEC000", "scenario": "oot", "path": os.path.join(
        ROMS, "The Legend of Zelda Ocarina of Time [0400486024DEC000][v131072].nup")},
    # Control: tells an emulator-wide slowdown from an Ocarina one.
    "starfox": {"id": "0400E88025B1A000", "scenario": "mash",
                "path": os.path.join(ROMS, "Star Fox [0400E88025B1A000].nup")},
}
# The Qt executable is bunx2 here and lichii in Lichii builds, which also keep their folders
# under "eden" instead of "eglantine" (launch() links one name to the other).
FRONTENDS = {"qt": (("bunx2", "lichii"), "qt-config.ini"),
             "cli": (("eden-cli",), "sdl2-config.ini")}
CONFIG_DIR = os.path.join(HOME, "config", "eglantine")

# Applied to every run: private data folders, and no host microphone. Opening the default
# microphone can switch a Bluetooth headset to HFP, which silences the whole desktop.
ISOLATION = {
    "nand_directory": os.path.join(HOME, "data", "eglantine", "nand"),
    "sdmc_directory": os.path.join(HOME, "data", "eglantine", "sdmc"),
    "load_directory": os.path.join(HOME, "data", "eglantine", "load"),
    "dump_directory": os.path.join(HOME, "data", "eglantine", "dump"),
    "input_device": "none", "check_for_updates": "false",
}
# The owner listens to runs, so sound stays as configured unless a run asks for --mute.
MUTE = {"audio_muted": "true", "volume": "0"}
# The documented reference settings (docs/nx2/oot-performance-experiments.md).
REFERENCE = {
    "use_docked_mode": "1", "resolution_setup": "3", "use_vsync": "1",
    "fps_limit": "170", "nx2_frame_rate_limit": "170",
    "native_async_draws": "1", "native_pipeline_wait_ms": "2",
    "use_asynchronous_shaders": "true", "async_presentation": "true",
    "precache_shaders": "2", "force_max_clock": "true", "frame_gen": "false",
    "use_speed_limit": "true", "speed_limit": "100", "log_filter": '"*:Info"',
}
PROFILES = {"owner": {}, "high": {"gpu_accuracy": "1"}, "low": {"gpu_accuracy": "0"},
            "reference": dict(REFERENCE, gpu_accuracy="1")}


def sh(*command, **kwargs):
    return subprocess.run(command, check=True, text=True, capture_output=True, **kwargs).stdout


def set_keys(path, values):
    """Sets existing keys in an ini file and returns the keys it has no line for."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    missing = []
    for key, value in values.items():
        pattern = re.compile(rf"(?m)^{re.escape(key)}=.*$")
        if not pattern.search(text):
            missing.append(key)
            continue
        text = pattern.sub(lambda match: f"{key}={value}", text)
        for suffix, flag in (("default", "false"), ("use_global", "false")):
            text = re.sub(rf"(?m)^{re.escape(key)}\\{suffix}=.*$",
                          lambda match: f"{key}\\{suffix}={flag}", text)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return missing


def call(method, params=None, out=None, timeout=120):
    request = json.dumps({"method": method, "params": params or {}})
    command = ["python3", "-I", os.path.join(HERE, "debug_mcp.py"), "--server", SERVER,
               "--call", request]
    if out:
        command += ["--out", out]
    try:
        done = subprocess.run(command, text=True, capture_output=True, timeout=timeout, cwd=REPO)
    except subprocess.TimeoutExpired:
        return {"error": f"no answer to {method} in {timeout} s"}
    try:
        return json.loads(done.stdout)
    except ValueError:
        return {"error": (done.stdout + done.stderr).strip()[:400]}


def look(path):
    """Mean colour of a screenshot, and whether the gameplay HUD's hearts are in its corner."""
    from PIL import Image
    import numpy
    pixels = numpy.asarray(Image.open(path).convert("RGB"), dtype=numpy.float32) / 255.0
    mean = pixels.mean(axis=(0, 1))
    height, width = pixels.shape[:2]
    corner = pixels[height * 36 // 720:height * 66 // 720, width * 50 // 1280:width * 180 // 1280]
    red = (corner[..., 0] > 0.6) & (corner[..., 1] < 0.4) & (corner[..., 2] < 0.4)
    return {"luma": round(float(0.2126 * mean[0] + 0.7152 * mean[1] + 0.0722 * mean[2]), 4),
            "hearts": round(float(red.mean()), 3)}


def shot(path, wait=3):
    result = call("screenshot", {"height": 720, "timeout_s": wait}, out=path, timeout=wait + 20)
    if "error" in result or not os.path.exists(path):
        return None
    return look(path)


def pid_file():
    return os.path.join(RIG, "emulator.pid")


def alive(pid):
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def process_stamp(pid):
    """Linux start time distinguishes a process from a later reuse of its PID."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().rsplit(")", 1)[1].split()[19]
    except OSError:
        return None


def is_rig_process(pid):
    try:
        executable = os.path.realpath(f"/proc/{pid}/exe")
        return os.path.commonpath((executable, BINS)) == BINS
    except OSError:
        return False


def windows(pid):
    done = subprocess.run(["xdotool", "search", "--onlyvisible", "--pid", str(pid)], text=True,
                          capture_output=True)
    return set(done.stdout.split())


def close_emulator(pid, limit=20):
    """Asks the emulator to close and answers Yes on the Qt frontend's confirmation.

    SIGTERM is the window's close request (MainWindow::close). The confirmation is the window
    that appears after it; Alt+Y is its Yes button, whatever the default button is.
    """
    began = time.time()
    record = {"how": "already gone", "popup": None}
    if not alive(pid):
        return record
    if not is_rig_process(pid):
        return {"how": "refused: not a rig process", "popup": None}
    stamp = process_stamp(pid)
    before = windows(pid)
    os.kill(pid, signal.SIGTERM)
    record["how"] = "closed"
    sent = 0.0
    while alive(pid) and process_stamp(pid) == stamp and time.time() - began < limit:
        popups = sorted(windows(pid) - before)
        # Qt takes the key from the window it has as the event's source, which is not always
        # the visible dialog's: every window of the process gets it. Those the modal dialog
        # blocks drop it. A key sent before the dialog takes input is lost, so it is repeated.
        if popups and time.time() - sent >= 0.4:
            if record["popup"] is None:
                name = subprocess.run(["xdotool", "getwindowname", popups[0]], text=True,
                                      capture_output=True).stdout.strip()
                record["popup"] = {"title": name, "after_s": round(time.time() - began, 2),
                                   "rounds": 0}
            every = subprocess.run(["xdotool", "search", "--pid", str(pid)], text=True,
                                   capture_output=True).stdout.split()
            for window in every:
                subprocess.run(["xdotool", "key", "--window", window, "alt+y"],
                               capture_output=True)
            record["popup"]["rounds"] += 1
            sent = time.time()
        time.sleep(0.1)
    if alive(pid) and process_stamp(pid) == stamp:
        record["how"] = "killed"
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        time.sleep(0.3)
    record["seconds"] = round(time.time() - began, 2)
    return record


def stop():
    try:
        with open(pid_file(), encoding="utf-8") as handle:
            pid, leader = map(int, handle.read().split())
    except (OSError, ValueError):
        return None
    leader_stamp = process_stamp(leader)
    record = close_emulator(pid)
    if record["how"].startswith("refused"):
        os.unlink(pid_file())
        return record
    # perf writes its samples out once the emulator is gone.
    for _ in range(240):
        if leader == pid or not alive(leader) or process_stamp(leader) != leader_stamp:
            break
        time.sleep(0.25)
    if leader_stamp is not None and process_stamp(leader) == leader_stamp:
        try:
            os.killpg(leader, signal.SIGKILL)  # helpers in the same, still-owned session
        except (ProcessLookupError, PermissionError):
            pass
    os.unlink(pid_file())
    return record


def perf_window(run_dir, seconds):
    """Samples for a while, on a run started with --perf."""
    control = os.path.join(run_dir, "perf.ctl")
    if not os.path.exists(control):
        return
    for word, wait in (("enable", seconds), ("disable", 0)):
        with open(control, "w", encoding="utf-8") as handle:
            handle.write(word + "\n")
        with open(os.path.join(run_dir, "perf.ack"), encoding="utf-8") as handle:
            handle.read(4)
        time.sleep(wait)


def audio_probe(pid, run_dir):
    """The emulator's PipeWire playback stream and one second of what it plays."""
    try:
        streams = json.loads(sh("pactl", "-f", "json", "list", "sink-inputs"))
        clients = json.loads(sh("pactl", "-f", "json", "list", "clients"))
    except (subprocess.CalledProcessError, ValueError) as error:
        return {"state": "unknown", "error": str(error)[:120]}
    # SDL's PipeWire streams carry no process id themselves; their client does.
    owned = {str(client["index"]) for client in clients
             if str(client.get("properties", {}).get("application.process.id")) == str(pid)}
    mine = [stream for stream in streams if str(stream.get("client")) in owned]
    if not mine:
        return {"state": "no stream"}
    result = {"streams": len(mine), "state": "silent", "peak_dbfs": None,
              "muted": any(stream.get("mute") for stream in mine),
              "corked": all(stream.get("corked") for stream in mine)}
    import numpy
    peak = 0.0
    for stream in mine:
        capture = os.path.join(run_dir, f"audio-{stream['index']}.wav")
        subprocess.run(["timeout", "1.2", "pw-record", "--target", str(stream["index"]),
                        "--format", "s16", capture], capture_output=True)
        try:
            with wave.open(capture, "rb") as handle:
                samples = numpy.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
            os.unlink(capture)
        except (OSError, wave.Error, EOFError):
            continue
        if samples.size:
            peak = max(peak, float(numpy.abs(samples.astype(numpy.float32)).max()) / 32768.0)
    if peak > 0:
        result["peak_dbfs"] = round(20 * numpy.log10(peak), 1)
    if peak > 1e-3 and not result["muted"] and not result["corked"]:
        result["state"] = "playing"
    return result


def host_state():
    busy = sh("sh", "-c", "pgrep -af 'cc1plus|ninja|\\bmake\\b|perf record|perf report|"
                          "bunx2|eden-cli' | grep -v pgrep || true").strip().splitlines()
    with open("/proc/loadavg", encoding="utf-8") as handle:
        load = handle.read().split()[:3]
    with open("/proc/meminfo", encoding="utf-8") as handle:
        memory = dict(line.split(":") for line in handle)
    gpu = sh("nvidia-smi", "--query-gpu=memory.used,utilization.gpu,temperature.gpu",
             "--format=csv,noheader").strip()
    return {"load": load, "available_gib": round(int(memory["MemAvailable"].split()[0]) / 2**20, 1),
            "gpu": gpu, "busy": [line[:120] for line in busy]}


def launch(args, run_dir):
    names, config_name = FRONTENDS[args.frontend]
    binary = next((path for path in (os.path.join(BINS, args.bin, name) for name in names)
                   if os.path.exists(path)), None)
    if not binary:
        sys.exit(f"no {' or '.join(names)} in {BINS}/{args.bin}: oot_rig.py keep {args.bin} first")
    game = GAMES[args.game]
    stop()
    before = host_state()
    if before["busy"] and not args.force:
        sys.exit("host is busy, not a clean run (--force to run anyway):\n  " +
                 "\n  ".join(before["busy"]))
    sh("rsync", "-a", "--delete", SNAP + "/", HOME + "/")
    for folder in ("dump", "play_time", "log", "screenshots"):
        os.makedirs(os.path.join(HOME, "data", "eglantine", folder), exist_ok=True)
    for part in ("config", "data", "cache"):
        link = os.path.join(HOME, part, "eden")
        if not os.path.lexists(link):
            os.symlink("eglantine", link)
    settings = dict(ISOLATION, **PROFILES[args.profile])
    if args.mute:
        settings.update(MUTE)
    for item in args.set or []:
        key, _, value = item.partition("=")
        settings[key] = value
    config = os.path.join(CONFIG_DIR, config_name)
    missing = set_keys(config, settings)
    if missing:
        sys.exit(f"{config}: no line for {', '.join(missing)}")
    # The per-game file overrides the global one for the keys it lists.
    custom = os.path.join(CONFIG_DIR, "custom", game["id"] + ".ini")
    if os.path.exists(custom):
        set_keys(custom, settings)
    environment = dict(os.environ)
    environment.update({
        "XDG_CONFIG_HOME": os.path.join(HOME, "config"),
        "XDG_DATA_HOME": os.path.join(HOME, "data"),
        "XDG_CACHE_HOME": os.path.join(HOME, "cache"),
        # The driver's own shader cache stays warm across runs unless a run asks otherwise.
        "__GL_SHADER_DISK_CACHE_PATH": os.path.join(RIG, "nvcache"),
        "__GL_SHADER_DISK_CACHE_SKIP_CLEANUP": "1",
        "EDEN_NX2_DEBUG_SERVER": SERVER,
        "EDEN_NX2_DEBUG_DIR": run_dir,
        # xdotool answers the close confirmation, so the Qt window must be an X11 one.
        "QT_QPA_PLATFORM": "xcb",
    })
    extra = {}
    for item in args.env or []:
        key, _, value = item.partition("=")
        environment[key] = extra[key] = value
    os.makedirs(environment["__GL_SHADER_DISK_CACHE_PATH"], exist_ok=True)
    command = [binary, "-g", game["path"]]
    if args.frontend == "cli":
        command[1:1] = ["-c", config]
    elif subprocess.run(["grep", "-q", "-a", "-e", "--game-only ", binary]).returncode == 0:
        # Builds with the owner's launcher mode; the Qt frontend has no --help that exits.
        command.insert(1, "--game-only")
    log = open(os.path.join(run_dir, "emulator.log"), "w")
    wrapped = command
    if args.perf:
        # Yama lets only an ancestor profile the emulator, so perf starts it. Sampling stays off
        # until perf_window() asks for it through the control fifo. Call graphs need privileges
        # here; branch stacks (-b) do not, and their last calls name the path to each sample.
        for name in ("perf.ctl", "perf.ack"):
            os.mkfifo(os.path.join(run_dir, name))
        wrapped = ["perf", "record", "-o", os.path.join(run_dir, "perf.data"), "-e", "cycles:u",
                   "-b", "-F", str(args.perf), "-D", "-1", "--control",
                   f"fifo:{run_dir}/perf.ctl,{run_dir}/perf.ack", "--"] + command
    process = subprocess.Popen(wrapped, env=environment, stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True, cwd=run_dir)
    pid = process.pid
    for _ in range(100):
        if not args.perf:
            break
        children = subprocess.run(["pgrep", "-P", str(process.pid)], text=True,
                                  capture_output=True).stdout.split()
        if children:
            pid = int(children[0])
            break
        time.sleep(0.05)
    with open(pid_file(), "w", encoding="utf-8") as handle:
        handle.write(f"{pid} {process.pid}")
    subprocess.Popen([sys.executable, "-I", __file__, "watchdog", str(pid),
                      str(process_stamp(pid)), str(args.max_seconds)],
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(binary, "rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    label_file = os.path.join(BINS, args.bin, "label.json")
    label = json.load(open(label_file)) if os.path.exists(label_file) else {}
    return {"tag": args.tag, "bin": args.bin, "binary_sha256": digest, "binary": label,
            "game": args.game, "frontend": args.frontend, "command": command[1:-1],
            "profile": args.profile, "settings": settings, "env": extra, "pid": pid,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "host_before": before}


class Presents:
    """Present rate between two looks at the status counter."""

    def __init__(self):
        self.count = self.time = None

    def rate(self):
        status = call("status", {"fps_window_ms": 0}, timeout=10)
        if "error" in status:
            return None
        count, now = status["presents"], time.time()
        rate = None
        if self.count is not None and now > self.time:
            rate = (count - self.count) / (now - self.time)
        self.count, self.time = count, now
        return rate


def press(button, fps):
    """One press and its release, each long enough for a game frame at the current rate."""
    frames = int(min(max(round((fps or 0) * 0.15), 3), 40))
    call("input_sequence", {"steps": [{"buttons": [button], "frames": frames},
                                      {"frames": frames}], "timeout_s": 2}, timeout=12)


def to_gameplay(args, pid, run_dir, record):
    """Mashes A through the menus (title, newest save, Load) until the game is on screen.

    Ocarina's gameplay is the first frame with the HUD's hearts. Another game has no known
    landmark: it gets --boot-seconds of A presses from its first bright frame.
    """
    scenario = GAMES[args.game]["scenario"]
    start = time.time()
    steps = record["boot"] = []
    deadline = start + args.boot_limit
    while call("status", {"fps_window_ms": 0}, timeout=10).get("presents") is None:
        if not alive(pid):
            return "the emulator exited during boot"
        if time.time() > deadline:
            return "debug server never answered"
        time.sleep(0.5)
    record["server_seconds"] = round(time.time() - start, 1)
    presents = Presents()
    presses = dark_presents = 0
    dark_since = first_bright = sound = None
    while time.time() < deadline:
        if not alive(pid):
            return "the emulator exited during boot"
        rate = presents.rate()
        seen = shot(os.path.join(run_dir, "boot.png"), 2)
        now = time.time()
        steps.append({"t": round(now - start, 1), "fps": rate and round(rate, 1),
                      "presses": presses, **(seen or {})})
        # Anything but black takes a press: A also skips the fading logos.
        bright = bool(seen) and seen["luma"] > 0.01
        if bright and first_bright is None:
            first_bright = now
            # The title plays music, so the sound check does not wait for gameplay.
            sound = threading.Thread(target=lambda: record.update(
                audio=audio_probe(pid, run_dir)), daemon=True)
            sound.start()
        if scenario == "oot" and seen and seen["hearts"] > 0.05:
            break
        if scenario == "mash" and first_bright and now - first_bright > args.boot_seconds:
            break
        if bright:
            dark_since = None
            if scenario == "mash" and presses % 4 == 3:
                # Cutscenes skip on a held A.
                call("input", {"buttons": ["A"], "frames": 36000, "timeout_s": 2}, timeout=15)
            else:
                press("A", rate)
            presses += 1
            if scenario == "mash" and presses % 6 == 1:
                shutil.copy(os.path.join(run_dir, "boot.png"),
                            os.path.join(run_dir, f"boot-{presses:03}.png"))
        elif presses >= 3:
            # Dark after the menus is the save loading.
            dark_since = dark_since or now
            if now - dark_since > 14 and not record.get("perf_load"):
                record["perf_load"] = True
                perf_window(run_dir, 2)
            if now - dark_since > args.load_limit:
                record["load_seconds"] = round(now - dark_since, 1)
                return (f"save still loading after {args.load_limit} s "
                        f"({presents.count - dark_presents} presents)")
            time.sleep(0.3)
        else:
            time.sleep(0.3)
        if dark_since is None:
            dark_presents = presents.count
    else:
        return "gameplay never appeared"
    record["boot_seconds"] = round(time.time() - start, 1)
    if sound:
        sound.join()
    if dark_since:
        record["load_seconds"] = round(time.time() - dark_since, 1)
    shutil.copy(os.path.join(run_dir, "boot.png"), os.path.join(run_dir, "gameplay.png"))
    return None


def thread_times(pid):
    """CPU seconds used so far by each of the emulator's threads, by thread name."""
    ticks = os.sysconf("SC_CLK_TCK")
    times = {}
    for task in os.listdir(f"/proc/{pid}/task"):
        try:
            with open(f"/proc/{pid}/task/{task}/stat", encoding="utf-8") as handle:
                name, rest = handle.read().split("(", 1)[1].rsplit(")", 1)
        except OSError:
            continue
        fields = rest.split()
        times[name] = times.get(name, 0.0) + (int(fields[11]) + int(fields[12])) / ticks
    return times


def pin_threads(pid, plan):
    """Pins threads by name: "CPUCore_0=0,CPUCore_1=1,GPU=6,*=8-15" ("*" is every other one)."""
    wanted = dict(item.split("=") for item in plan.split(","))
    for task in os.listdir(f"/proc/{pid}/task"):
        try:
            with open(f"/proc/{pid}/task/{task}/comm", encoding="utf-8") as handle:
                name = handle.read().strip()
        except OSError:
            continue
        cpus = wanted.get(name, wanted.get("*"))
        if cpus:
            subprocess.run(["taskset", "-pc", cpus, task], capture_output=True)


# Left stick (walk) and right stick (camera) per segment: every direction of both.
WALK = [((0, 1), (1, 0)), ((1, 0), (0, 1)), ((0, -1), (-1, 0)), ((-1, 0), (0, -1)),
        ((0.7, 0.7), (-1, 0)), ((0.7, -0.7), (0.7, 0.7)), ((-0.7, -0.7), (1, 0)),
        ((-0.7, 0.7), (-0.7, -0.7)), ((0, 1), (0, 0)), ((0, 0), (1, 0))]


def reference_fps(text):
    try:
        return float(text)
    except ValueError:
        with open(os.path.join(RUNS, text, "result.json"), encoding="utf-8") as handle:
            return float(json.load(handle)["walk"]["fps"])


def walk(args, record):
    """Walks and pans in --segment second steps and stops once the verdict is clear."""
    result = record["walk"] = {"segments": [], "fps": 0.0, "scene": args.scene,
                               "warmup_seconds": args.warmup}
    if args.warmup:
        call("input", {"frames": 36000, "timeout_s": args.warmup},
             timeout=args.warmup + 15)
    result["start_status"] = call("status", {"fps_window_ms": 0}, timeout=10)
    beat = reference_fps(args.beat) if args.beat else None
    needed = beat * (1 + args.margin) if beat else None
    frames = 0
    elapsed = 0.0
    index = 0
    sampler = threading.Thread(target=lambda: (time.sleep(args.perf_at), perf_window(
        record["run_dir"], args.perf_seconds)), daemon=True)
    sampler.start()
    if args.pin:
        pin_threads(record["pid"], args.pin)
    cpu_before, cpu_began = thread_times(record["pid"]), time.time()

    def cpu_use():
        # Milliseconds of CPU per second of the walk, busiest threads first.
        after, span = thread_times(record["pid"]), time.time() - cpu_began
        use = {name: round(1000 * (after[name] - cpu_before.get(name, 0.0)) / span)
               for name in after}
        result["thread_ms_per_s"] = dict(sorted(((name, value) for name, value in use.items()
                                                 if value >= 10), key=lambda item: -item[1]))
    while elapsed < args.seconds:
        left, right = WALK[index % len(WALK)] if args.scene == "walk" else ((0, 0), (0, 0))
        index += 1
        # More frames than any segment presents: the timeout ends the hold, so a segment is a
        # fixed time whatever the frame rate.
        held = call("input", {"left_stick": left, "right_stick": right, "frames": 36000,
                              "timeout_s": args.segment}, timeout=args.segment + 15)
        match = re.search(r"(\d+) of \d+ frames were presented", held.get("error", ""))
        if not match:
            result["error"] = str(held)[:200]
            return "walk failed: " + result["error"]
        stats = call("frame_times", {"seconds": args.segment, "refresh_hz": 170,
                                     "lookback": True}, timeout=20)
        segment = {"left": left, "right": right, "frames": int(match.group(1)),
                   "fps": round(int(match.group(1)) / args.segment, 2),
                   "p50_ms": stats.get("p50_ms"), "p99_ms": stats.get("p99_ms"),
                   "max_ms": stats.get("max_ms")}
        try:
            # Host GPU load while walking: tells a host GPU limit from an emulation one.
            segment["gpu_util_power"] = sh("nvidia-smi", "--query-gpu=utilization.gpu,power.draw",
                                           "--format=csv,noheader,nounits").strip()
        except (subprocess.CalledProcessError, OSError):
            pass
        result["segments"].append(segment)
        frames += segment["frames"]
        elapsed += args.segment
        result["fps"] = round(frames / elapsed, 2)
        result["seconds"] = elapsed
        print(f"  walk {elapsed:4.0f} s: {segment['fps']:7.2f} fps (run {result['fps']:.2f})",
              flush=True)
        cpu_use()
        if segment["fps"] < 5 and not record.get("perf_crawl"):
            record["perf_crawl"] = round(elapsed, 1)
            perf_window(record["run_dir"], 2)
        # The first segment still builds the scene's pipelines: it only ends a run that is
        # far under the floor.
        if segment["fps"] < (args.floor if index > 1 else args.floor / 4):
            return f"not playable: {segment['fps']:.1f} fps, under the {args.floor:g} fps floor"
        if needed and elapsed >= args.prove and result["fps"] < needed:
            return (f"no improvement shown: {result['fps']:.1f} fps after {elapsed:.0f} s, "
                    f"needs {needed:.1f} (reference {beat:.1f} + {args.margin:.0%})")
    window = call("frame_times", {"seconds": min(elapsed, 60), "refresh_hz": 170,
                                  "lookback": True}, timeout=30)
    window.pop("intervals_ms", None)
    result["window"] = window
    result["end_status"] = call("status", {"fps_window_ms": 0}, timeout=10)
    if needed:
        return f"improved: {result['fps']:.1f} fps against {beat:.1f}"
    return f"playable: {result['fps']:.1f} fps"


def log_summary(run_dir):
    source = os.path.join(HOME, "data", "eglantine", "log", "eden_log.txt")
    target = os.path.join(run_dir, "eden_log.txt")
    if os.path.exists(source):
        shutil.copy(source, target)
    try:
        with open(target, "rb") as handle:
            text = handle.read().replace(b"\0", b"").decode("utf-8", "replace")
    except OSError:
        return {}
    version = re.search(r"\w+ Version: [^\n]*", text)
    count = lambda pattern: len(re.findall(pattern, text))
    # Where each frame goes on the game's present call, per 600 presents (always logged).
    presents = [dict(zip(("frame_us", "fence_us", "composite_us", "pacing_us", "ahead"),
                         map(int, match)))
                for match in re.findall(
                    r"last 600 presents: frame (\d+) us \(max \d+, \d+ over 100 ms\), blocked on "
                    r"the GPU fence (\d+) us \(max \d+\), composite (\d+) us, vblank\s+pacing "
                    r"(\d+) us, (\d+) ran a frame ahead", text)]
    return {"version": version.group(0)[:120] if version else "", "present_blocks": presents[-8:],
            "errors": count(r"<Error>"), "critical": count(r"<Critical>"),
            "device_loss": count(r"Device loss"), "semaphore_waits": count(r"waits on semaphore"),
            "gpu_thread_waits": count(r"Waited \d+ s for the GPU thread"),
            "unmapped_reads": count(r"Unmapped Device ReadBlock"),
            "audio": sorted(set(re.findall(r"Audio\.(?:output_engine|input_device|volume|"
                                           r"audio_muted): [^\n]*", text)))}


def command_run(args, keep_running=False):
    os.makedirs(RUNS, exist_ok=True)
    run_dir = os.path.join(RUNS, args.tag)
    if os.path.exists(run_dir):
        sys.exit(f"{run_dir} exists: pick a new tag")
    lock = open(os.path.join(RIG, "lock"), "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    os.makedirs(run_dir)
    record = launch(args, run_dir)
    record["run_dir"] = run_dir
    began = time.time()
    try:
        failure = to_gameplay(args, record["pid"], run_dir, record)
        record["failure"] = failure
        record["verdict"] = failure
        if not failure:
            print(f"  gameplay after {record['boot_seconds']} s", flush=True)
            if not keep_running:
                record["verdict"] = walk(args, record)
                shot(os.path.join(run_dir, "end.png"))
    finally:
        record["host_after"] = host_state()
        if not keep_running or record.get("failure"):
            record["exit"] = stop()
            record["log"] = log_summary(run_dir)
        record["total_seconds"] = round(time.time() - began, 1)
        with open(os.path.join(run_dir, "result.json"), "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=1)
    print(line(record))
    if record.get("failure"):
        sys.exit(1)


def line(record):
    parts = [f"{record['tag']:<18} {record['bin']:<10} {record.get('game', 'oot'):<7} "
             f"{record.get('frontend', 'cli'):<3} {record['profile']:<5}"]
    if "boot_seconds" in record:
        parts.append(f"boot {record['boot_seconds']:.0f} s")
    walked = record.get("walk") or {}
    if walked.get("segments"):
        window = walked.get("window") or {}
        text = f"walk {walked['fps']:.2f} fps over {walked.get('seconds', 0):.0f} s"
        if window.get("frames"):
            text += (f" p50 {window['p50_ms']:.2f} p99 {window['p99_ms']:.2f} "
                     f"max {window['max_ms']:.1f} ms")
        parts.append(text)
    parts.append(str(record.get("verdict")))
    sound = record.get("audio")
    if sound:
        level = f" {sound['peak_dbfs']} dBFS" if sound.get("peak_dbfs") is not None else ""
        parts.append(f"audio {sound['state']}{level}")
    closed = record.get("exit")
    if closed:
        popup = " (answered the confirmation)" if closed.get("popup") else ""
        parts.append(f"exit {closed['how']} in {closed.get('seconds', 0)} s{popup}")
    log = record.get("log") or {}
    parts.append(f"err={log.get('errors')} loss={log.get('device_loss')} "
                 f"sem={log.get('semaphore_waits')}")
    return " | ".join(parts)


def command_keep(args):
    build = args.build or os.path.join(RIG, "build")
    tree = args.tree or os.path.join(RIG, "wt")
    sha = sh("git", "-C", tree, "rev-parse", "HEAD").strip()
    scm = os.path.join(build, "src", "common", "scm_rev.cpp")
    with open(scm, encoding="utf-8") as handle:
        embedded = re.search(r'^#define GIT_REV "([^"]+)"', handle.read(), re.M)
    if not embedded or embedded.group(1) != sha:
        sys.exit("build revision differs from the source: reconfigure and rebuild before keep")
    for name in ("bunx2", "lichii", "eden-cli"):
        binary = os.path.join(build, "bin", name)
        if os.path.exists(binary) and subprocess.run(
                ["grep", "-F", "-a", "-q", sha, binary]).returncode != 0:
            sys.exit(f"{binary} does not contain the source revision: rebuild before keep")
    target = os.path.join(BINS, args.label)
    if os.path.exists(target):
        sys.exit(f"{target} exists: pick a new label to preserve earlier binaries")
    os.makedirs(target, exist_ok=True)
    for name in ("bunx2", "lichii", "eden-cli", "libnxformat.so", "nx2_switches.txt"):
        source = os.path.join(build, "bin", name)
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(target, name))
    label = {"sha": sha, "embedded_sha": embedded.group(1),
             "describe": sh("git", "-C", tree, "log", "-1", "--format=%h %s").strip(),
             "dirty": bool(sh("git", "-C", tree, "status", "--porcelain", "-uno").strip()),
             "build": build, "kept": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with open(os.path.join(target, "label.json"), "w", encoding="utf-8") as handle:
        json.dump(label, handle, indent=1)
    print(json.dumps(label))


def command_snapshot(_args):
    home = os.path.expanduser("~")
    for part in ("config/eglantine", "data/eglantine", "cache/eglantine"):
        os.makedirs(os.path.join(SNAP, part), exist_ok=True)
    sh("rsync", "-a", "--delete", f"{home}/.config/eglantine/", f"{SNAP}/config/eglantine/")
    # The owner's configuration keeps nand, keys, sdmc and load in the older eden data folder.
    sh("rsync", "-a", "--delete", *(f"{home}/.local/share/eden/{name}" for name in
                                    ("nand", "keys", "sdmc", "load")), f"{SNAP}/data/eglantine/")
    sh("rsync", "-a", "--delete", f"{home}/.cache/eglantine/shader", f"{SNAP}/cache/eglantine/")
    print(sh("du", "-sh", SNAP).strip())


def command_watchdog(args):
    time.sleep(args.seconds)
    if process_stamp(args.pid) == args.stamp:
        close_emulator(args.pid)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    watchdog = commands.add_parser("watchdog", help=argparse.SUPPRESS)
    watchdog.add_argument("pid", type=int)
    watchdog.add_argument("stamp")
    watchdog.add_argument("seconds", type=int)
    watchdog.set_defaults(func=command_watchdog)
    commands.add_parser("snapshot").set_defaults(func=command_snapshot)
    keep = commands.add_parser("keep")
    keep.add_argument("label")
    keep.add_argument("--build")
    keep.add_argument("--tree")
    keep.set_defaults(func=command_keep)
    for name in ("run", "boot"):
        run = commands.add_parser(name)
        run.add_argument("--bin", required=True)
        run.add_argument("--tag", required=True)
        run.add_argument("--game", choices=sorted(GAMES), default="oot")
        run.add_argument("--frontend", choices=sorted(FRONTENDS), default="qt")
        run.add_argument("--profile", choices=sorted(PROFILES), default="owner")
        run.add_argument("--seconds", type=float, default=24, help="longest walk")
        run.add_argument("--scene", choices=("walk", "fixed"), default="walk")
        run.add_argument("--warmup", type=int, default=0, help="neutral input seconds before measurement")
        run.add_argument("--segment", type=int, default=2, help="seconds per walk direction")
        run.add_argument("--floor", type=float, default=20, help="fps under which a run ends")
        run.add_argument("--beat", help="fps, or the tag of a run, this run must improve on")
        run.add_argument("--margin", type=float, default=0.05, help="lead over --beat that counts")
        run.add_argument("--prove", type=float, default=6, help="seconds before --beat is judged")
        run.add_argument("--load-limit", type=float, default=30,
                         help="seconds the save may take to load")
        run.add_argument("--boot-limit", type=float, default=240)
        run.add_argument("--boot-seconds", type=float, default=45,
                         help="menu time for a game without a gameplay landmark")
        run.add_argument("--perf-at", type=float, default=0, help="seconds into the walk")
        run.add_argument("--perf-seconds", type=float, default=3)
        run.add_argument("--pin", help="thread=cpus,... applied when the walk starts")
        run.add_argument("--env", action="append")
        run.add_argument("--set", action="append")
        run.add_argument("--mute", action="store_true")
        run.add_argument("--perf", type=int, metavar="HZ",
                         help="run under perf record; samples the load stall and the walk")
        run.add_argument("--force", action="store_true")
        run.add_argument("--max-seconds", type=int, default=600)
        run.set_defaults(func=(lambda a: command_run(a)) if name == "run"
                         else (lambda a: command_run(a, keep_running=True)))
    one = commands.add_parser("call")
    one.add_argument("request")
    one.add_argument("--out")
    one.set_defaults(func=lambda a: print(json.dumps(call(
        json.loads(a.request)["method"], json.loads(a.request).get("params"), a.out, 700))))
    commands.add_parser("stop").set_defaults(func=lambda a: print(json.dumps(stop())))
    table = commands.add_parser("table")
    table.add_argument("tags", nargs="+")
    table.set_defaults(func=lambda a: [print(line(json.load(open(
        os.path.join(RUNS, tag, "result.json"))))) for tag in a.tags])
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
