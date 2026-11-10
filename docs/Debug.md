# Debug Guidelines

## Issue reports

When reporting issues or finding bugs, we often need backtraces, debug logs, or both in order to track down the issue.

### Graphics Debugging

If your bug is related to a graphical issue -- e.g. mismatched colors, vertex explosions, flickering, etc. -- then you are required to include graphical debugging logs in your issue reports.

Graphics Debugging is found in General -> Debug. It needs the Vulkan Validation Layers: `sudo pacman -S vulkan-validation-layers`.

Once Graphics Debugging is enabled, run the problematic game again and continue. Note that the game may run extremely slow on weak hardware.

### Debug Logs

Debug logs can be found in `General -> Debug -> Open Log Location`, or at `~/.local/share/eglantine/log/eden_log.txt`. This MUST be included in all bug reports, except for certain UI bugs -- but we still highly recommend them even for UI bugs.

## Debugging (host code)

Ignoring SIGSEGV when debugging in host:

- **gdb**: `handle SIGSEGV nostop pass`.
- **lldb**: `pro hand -p true -s false -n false SIGSEGV`.

## Debugging (guest code)

### gdb

You must have GDB installed for aarch64 to debug the target: `sudo pacman -Syu aarch64-linux-gnu-gdb`.

Run `./build/bin/eden-cli -c <path to your config file (see logs where you run Eden normally to see where it is)> -d -g <path to game>`, or `Enable GDB Stub` at General > Debug, then hook up an aarch64-gdb:

- `target remote localhost:6543`

Type `c` (for continue) and then if it crashes just do a `bt` (backtrace) and `layout asm`

### gdb cheatsheet

- `mo <cmd>`: Monitor commands, `get info`, `get fastmem` and `get mappings` are available. Type `mo help` for more info.
- `detach`: Detach from remote (i.e restarting the emulator).
- `c`: Continue
- `p <expr>`: Print variable, `p/x <expr>` for hexadecimal.
- `r`: Run
- `bt`: Print backtrace
- `info threads`: Print all active threads
- `thread <number>`: Switch to the given thread (see `info threads`)
- `layout asm`: Display in assembly mode (TUI)
- `si`: Step assembly instruction
- `s` or `step`: Step over LINE OF CODE (not assembly)
- `display <expr>`: Display variable each step.
- `n`: Next (skips over call frame of a function)
- `frame <number>`: Switches to the given frame (from `bt`)
- `br <expr>`: Set breakpoint at `<expr>`.
- `delete`: Deletes all breakpoints.
- `catch throw`: Breakpoint at throw. Can also use `br __cxa_throw`
- `br _mesa_error`: Break on mesa errors (set environment variable `MESA_DEBUG=1` beforehand), see [MESA_DEBUG](https://mesa-docs.readthedocs.io/en/latest/debugging.html).

Expressions can be `variable_names` or `1234` (numbers) or `*var` (dereference of a pointer) or `*(1 + var)` (computed expression).

For more information type `info gdb` and read [the man page](https://man7.org/linux/man-pages/man1/gdb.1.html).

# RenderDoc

RenderDoc is a free, cross platform, multi-graphics API debugger. It is an invaluable tool for diagnosing issues with graphics applications, and includes support for Vulkan. Get it from [renderdoc.org](https://renderdoc.org).

RenderDoc can capture Eden's Vulkan output when its Vulkan layer is loaded before Eden creates the Vulkan device. Before using RenderDoc to diagnose issues, it's always good to make sure there are no validation errors. Any errors means the behavior of the application is undefined. That said, RenderDoc can help debug validation errors if you do have them.

## Usage

Install it with `sudo pacman -S renderdoc`. You can either use the RenderDoc UI (`qrenderdoc`) to launch Bunx2, or enable RenderDoc's Vulkan layer for one run:

```sh
ENABLE_VULKAN_RENDERDOC_CAPTURE=1 ./build/bin/bunx2
```

Captures go to `/tmp/RenderDoc` by default.

Press RenderDoc's capture hotkey, usually `F12`, to capture a frame. To stop using RenderDoc, close Bunx2 and launch it again without `ENABLE_VULKAN_RENDERDOC_CAPTURE`.

## Eden Hotkey

Eden also has a separate `Toggle Renderdoc Capture` hotkey behind the debug setting `renderdoc_hotkey`.
That hotkey does not load or unload RenderDoc. It only toggles Eden's own manual capture through RenderDoc's API:

- First press: Starts a capture
- Second press: Ends that capture

## Simple checklist for debugging black screens using Renderdoc

When debugging a black screen, there are many ways the application could have setup Vulkan wrong.
Here is a short checklist of items to look at to make sure are appropriate:

- Draw call counts are correct (aka not zero, or if rendering many triangles, not 3)
- Vertex buffers are bound
- vertex attributes are correct - Make sure the size & offset of each attribute matches what should it should be
- Any bound push constants and descriptors have the right data - including:
  - Matrices have correct values - double check the model, view, & projection matrices are uploaded correctly
- Pipeline state is correct
  - viewport range is correct - x,y are 0,0; width & height are screen dimensions, minDepth is 0, maxDepth is 1, NDCDepthRange is 0,1
  - Fill mode matches expected - usually solid
  - Culling mode makes sense - commonly back or none
  - The winding direction is correct - typically CCW (counter clockwise)
  - Scissor region is correct - usually same as viewport's x,y,width, &height
- Blend state is correct
- Depth state is correct - typically enabled with Function set to Less than or Equal
- Swapchain images are bound when rendering to the swapchain
- Image being rendered to is the same as the one being presented when rendering to the swapchain

Alternatively, a [RenderDoc Extension](https://github.com/baldurk/renderdoc-contrib/tree/main/baldurk/whereismydraw) ([Archive](https://web.archive.org/web/20250000000000*/https://github.com/baldurk/renderdoc-contrib/tree/main/baldurk/whereismydraw)) exists which automates doing a lot of these manual steps.
