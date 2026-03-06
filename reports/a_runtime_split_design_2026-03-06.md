# A Runtime Split Design - 2026-03-06

## Goal

Current Python/OpenCV runtime is good for experimentation, calibration UI, and debugging, but it is not a good long-term runtime for a 60fps-class real-time panorama pipeline.

The goal of `A` is:

- keep Python for UI/control/logging
- move hot-path video processing out of Python
- make the hot-path engine reusable later from:
  - a standalone runtime executable (`A`)
  - a GStreamer plugin (`B`)
  - an FFmpeg-integrated runtime/filter (`C`)

The critical point is this: `A` must be designed as `core engine + thin wrapper`, not as one monolithic executable.

## Current Problem

The current runtime assumes that frames are Python-owned `numpy.ndarray` objects.

Examples:

- readers produce Python frames:
  - `stitching/desktop_app.py`
  - `stitching/ffmpeg_reader.py`
- pairing/sync logic assumes Python frames:
  - `_read_synced_pair()` in `stitching/desktop_app.py`
- stitching worker receives Python frames and then uploads them to GPU.

That means the pipeline is still effectively:

`decode -> Python frame -> GPU upload -> stitch`

This is the boundary that must move.

## Target Shape

The project should be split into 3 layers.

### Layer 1. Core Stitch Engine

This layer is the reusable heart.

Responsibilities:

- calibration state
- homography / affine estimation
- warp plan generation
- GPU stitch runtime
- exposure compensation
- seam / blend policy
- metrics collection

Requirements:

- no CLI
- no OpenCV windows
- no Python-only state
- no RTSP/network ownership

Suggested interface shape:

- `EngineConfig`
- `CalibrationState`
- `FramePair`
- `StitchResult`
- `EngineMetrics`
- methods:
  - `initialize(config)`
  - `submit_pair(left_frame, right_frame, timestamps)`
  - `run_once()`
  - `get_result()`
  - `get_metrics()`
  - `set_manual_points(...)`
  - `reset_auto_calibration()`

This layer must become a native library first:

- `stitch_engine_core.dll` or static/shared native library

Later:

- `A` uses it from a standalone executable
- `B` wraps it as a GStreamer plugin
- `C` wraps it as an FFmpeg-side integration/filter/runtime

### Layer 2. Runtime Adapter

This layer is replaceable.

Responsibilities:

- input ownership
- decode ownership
- timestamp handling
- output ownership
- reconnect / stream lifecycle
- IPC or pipeline glue

Possible adapters:

- `A`: `stitch_runtime.exe`
- `B`: `gststitch` plugin
- `C`: FFmpeg-centered runtime/filter wrapper

This is where `A/B/C` differ.

### Layer 3. Control / UI

Keep in Python.

Responsibilities:

- RTSP URL input
- start / stop / profile select
- manual calibration UI
- metrics display
- operator controls
- benchmark mode trigger

This layer must not own full-rate raw frame processing in the final architecture.

## Recommended Boundary For A

`A` should be a standalone engine process.

Suggested process split:

### Python control process

Responsibilities:

- user input
- configuration file generation / IPC request
- manual point input
- monitor / logs / status panel

### Native runtime process

Responsibilities:

- open left/right streams
- decode
- synchronize or latest-pair policy
- stitch
- encode/send or benchmark

IPC should carry only small messages, never full-rate raw frames.

Examples of safe IPC payloads:

- config JSON
- metrics JSON
- commands: `start`, `stop`, `manual_mode`, `add_point`, `reset_auto`
- state snapshots

Examples of payloads to avoid:

- 1080p BGR frame buffers
- raw panorama frames at full rate

## Suggested IPC Contract

Use one of these:

- local TCP socket with JSON lines
- named pipe with JSON messages
- stdin/stdout JSON protocol if the runtime is launched as a child process

Suggested messages:

### Python -> runtime

- `start`
- `stop`
- `reload_config`
- `set_manual_mode`
- `add_manual_point`
- `reset_auto_calibration`
- `request_snapshot`

### Runtime -> Python

- `status`
- `metrics`
- `warning`
- `error`
- `manual_state`
- `preview_snapshot` (optional, low-rate only)

Important:

- operator preview should be low-rate and optional
- full-rate panorama should stay in runtime

## What To Reuse From Current Repo

The following concepts are worth preserving:

- `stitching/core/config.py`
- `stitching/core/geometry.py`
- `stitching/core/blend.py`
- `stitching/core/exposure.py`
- `stitching/core/features.py`
- configuration semantics from `DesktopConfig`
- manual calibration behavior from `desktop_app.py`
- benchmark/logging semantics already proven useful

The current Python code should be treated as reference behavior, not final runtime.

## What Must Not Be Carried Forward As-Is

- Python-owned frame buffers as the runtime boundary
- `numpy.ndarray` as the hot-path interchange contract
- desktop `imshow` assumptions in core processing
- current `RtspReader` / `FfmpegRtspReader` as final runtime architecture

These are fine for the current benchmark phase, but not for the final target.

## A Design Rule That Protects Future B/C Migration

This is the most important rule:

`A` must be written as:

- `core engine library`
- `runtime wrapper`

and not as:

- one giant executable with all logic fused together

If we follow that rule:

- moving from `A` to `B` means writing a GStreamer wrapper around the same core
- moving from `A` to `C` means writing an FFmpeg-side wrapper around the same core

If we do not follow that rule:

- `A -> B/C` becomes another rewrite

## Concrete Recommended Module Split

### Keep in Python

- `stitching/desktop_app.py`
- `stitching/gui_app.py`
- `stitching/cli.py`
- monitoring / reporting / profiles

Role after migration:

- control client
- preview client
- benchmark launcher

### Convert into native-engine spec

- matching / homography logic
- warp plan logic
- blend/exposure logic
- runtime metrics logic

Role after migration:

- engine core library

### Replace entirely in final runtime

- Python RTSP readers
- Python full-rate worker loop
- Python-owned hot-path frame queues

## Phased Plan

### Phase A1. Freeze engine contract

Define these native-facing structs first:

- engine config
- calibration state
- frame pair metadata
- metrics snapshot
- command/event schema

Deliverable:

- stable interface document

### Phase A2. Build standalone native runtime

Deliverable:

- `stitch_runtime.exe`

Capabilities:

- left/right input
- benchmark mode
- metrics JSON output

Python still drives it.

### Phase A3. Port manual calibration control

Deliverable:

- Python UI sends manual points to runtime
- runtime updates calibration state

### Phase A4. Output path

Deliverable:

- benchmark only
- then encode/send

Important:

- preview remains low-rate
- runtime remains frame owner

### Phase A5. Decide whether A is sufficient

If `A` meets target:

- keep `A`

If `A` still misses target but core is clean:

- wrap the same core for `B` or `C`

## Decision Guidance

Choose `A` if:

- fastest path to a working native runtime matters
- current Python UI/tools should stay alive
- we want measurement before committing to a media framework

Choose `B` later if:

- Linux deployment
- full pipeline productization
- many outputs/branches/stream paths

Choose `C` later if:

- FFmpeg-centric deployment
- codec/protocol control is dominant

## Final Recommendation

Proceed with `A`, but define the architecture as:

- reusable native core
- thin standalone runtime wrapper
- Python control/UI wrapper

That keeps the next step practical while avoiding a second full rewrite later.
