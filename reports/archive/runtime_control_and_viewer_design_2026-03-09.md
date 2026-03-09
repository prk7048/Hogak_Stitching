# Runtime Control And Viewer Design - 2026-03-09

## Scope

This document defines the concrete `v1` process layout for the native runtime transition on Windows.

The design target is:

- native runtime owns the full-rate media path
- Python owns only control, logging, and operator workflow
- operator can see the final transmitted panorama result
- Python monitoring must not reduce engine FPS

## Decision Summary

### Required in v1

1. native runtime process
2. Python control/UI process
3. final-stream viewer path
4. control/metrics channel

### Explicitly not required in v1

1. full-rate raw panorama frames into Python
2. low-rate debug snapshots
3. embedded Python-side frame renderer for the real-time panorama

`low-rate debug snapshot` remains an optional future feature, not a required part of the first production-oriented architecture.

## Recommended v1 Process Topology

```text
Process A: Python control/UI
  - operator settings
  - start/stop
  - metrics/logs
  - launch/stop viewer process

Process B: native runtime
  - input/decode
  - pair/sync
  - stitch
  - encode
  - output stream
  - control-plane server

Process C: final-stream viewer
  - receives encoded panorama stream as a normal client
  - completely separate from the engine hot path
```

## Why the Viewer Should Be Separate

If Python tries to render the panorama itself inside the control/UI process, three risks appear:

1. Python becomes the owner of high-rate video data again
2. UI rendering latency can feed back into the runtime architecture
3. debugging and production behavior diverge

If the viewer is a separate client process:

1. the engine still produces the same final stream
2. the viewer cannot force raw-frame ownership into Python
3. the operator sees the actual encoded output

This is the preferred `v1` model.

## v1 Recommendation For Windows

### Control/UI process

Keep Python.

Responsibilities:

- collect operator input
- construct engine config
- launch runtime process
- receive metrics/status
- display logs
- launch/stop final-stream viewer

### Native runtime process

Use C++/CUDA.

Responsibilities:

- open left/right sources
- decode
- pair/sync
- stitch
- encode
- publish panorama output stream
- expose control/metrics endpoint

### Viewer process

Use an existing media player first.

Recommended order:

1. `ffplay`
2. `mpv`
3. `VLC`

This is better than writing a custom full-rate Python viewer first.

Reason:

- lower implementation cost
- correct end-to-end validation
- no new Python raw-frame path

## Channel Design

### Channel 1. Control/metrics channel

Purpose:
- commands and runtime state only

Transport:
- local TCP socket recommended for v1

Why TCP over named pipe in v1:

- easy to debug manually
- easy to test with Python
- cross-platform path later if Linux migration happens

Message shape:
- JSON lines

Examples:
- `start`
- `stop`
- `reload_config`
- `set_manual_mode`
- `add_manual_point`
- `reset_auto_calibration`
- `metrics`
- `status`
- `warning`
- `error`

This should reuse the message shapes already described in:
- [runtime_contract.py](c:\Users\Pixellot\Hogak_Stitching\stitching\runtime_contract.py)

### Channel 2. Final panorama output stream

Purpose:
- actual encoded panorama output

v1 recommendation on Windows:
- local UDP MPEG-TS or local RTSP

Recommended order:

1. `udp://127.0.0.1:<port>` with MPEG-TS
2. local RTSP server target if already needed for downstream

Why UDP/MPEG-TS is attractive for v1:

- simple local test path
- low ceremony
- easy for ffplay/VLC/mpv to open

Why RTSP may still be chosen:

- if the final product path is RTSP anyway
- if downstream consumers expect RTSP from day one

### Channel 3. Optional snapshot/debug channel

Purpose:
- calibration helper images
- seam/debug overlays
- very low-rate internal inspection

Status in v1:
- not required
- disabled by default

Rationale:
- the operator already has the final-stream viewer
- adding snapshots increases architecture surface area
- keep the first implementation focused

## Answer To The Snapshot Question

Is `optional low-rate debug snapshot` still needed?

### Short answer

Not for `v1`.

### Why

If the operator can already watch the final encoded stream, the minimum production-relevant visibility requirement is satisfied.

That means:

- stitching quality can be checked
- encode artifacts can be checked
- end-to-end latency can be checked

### When snapshots become useful later

Snapshots are only justified if we need one of these:

1. internal seam overlay
2. manual calibration helper view
3. intermediate non-encoded panorama state
4. diagnostics when output stream is intentionally disabled

So:

- `final stream viewer` is the main observer mechanism
- `snapshot` is only a later diagnostic tool

## Module Split For v1

### Python side

Suggested modules:

- `stitching/runtime_client.py`
- `stitching/runtime_launcher.py`
- `stitching/runtime_monitor.py`
- existing CLI/UI modules continue to call these

Responsibilities:

- connect to control socket
- send commands
- receive metrics/events
- manage viewer subprocess lifecycle

### Native runtime side

Suggested modules:

- `native_runtime/src/app/runtime_main.cpp`
- `native_runtime/src/control/control_server.cpp`
- `native_runtime/src/control/message_codec.cpp`
- `native_runtime/src/engine/stitch_engine.cpp`
- `native_runtime/src/io/input_manager.cpp`
- `native_runtime/src/io/output_manager.cpp`

Responsibilities:

- runtime lifecycle
- command handling
- metrics emission
- media path ownership

### Viewer integration

Suggested Python-side abstraction:

- `stitching/final_stream_viewer.py`

Responsibilities:

- launch external viewer process
- stop viewer process
- restart on output URL change

Important:

- viewer lifecycle belongs to Python UI
- viewer does not become part of engine control path

## Engine Start Sequence

1. Python UI collects operator config
2. Python launches runtime process
3. Python connects to control socket
4. Runtime emits `hello`
5. Python sends `start` with engine config
6. Runtime opens inputs and output target
7. Runtime emits `started`
8. Python launches final-stream viewer with the chosen output URL
9. Python keeps reading metrics/status

## Engine Stop Sequence

1. Python sends `stop`
2. Runtime closes media path
3. Runtime emits `stopped`
4. Python stops viewer process
5. Python may then send `shutdown` or terminate the runtime process

## Manual Calibration Sequence

1. Python sends `set_manual_mode`
2. Python sends repeated `add_manual_point` commands
3. Runtime updates calibration state
4. Runtime emits `manual_state`
5. Python keeps viewer open on final stream if available

Important:

Manual control still goes over the control plane.
The final stream remains separate.

## Runtime Guarantees

The runtime must enforce these rules:

1. no viewer dependency in the hot path
2. if viewer dies, runtime continues
3. if Python UI dies, runtime may keep running until an explicit policy says otherwise
4. metrics emission must be throttled if the control client is slow
5. no full-rate raw panorama frames cross the control boundary

## Practical v1 Recommendation

### Use this exact split first

- Python:
  - config
  - start/stop
  - metrics/logs
  - launch `ffplay` or `mpv` as final-stream viewer

- Native runtime:
  - decode
  - sync
  - stitch
  - encode
  - output stream

### Do not add this yet

- Python-side stitched frame renderer
- Python-side full-rate preview frame pipe
- snapshot channel by default

These can wait until the engine is already stable.

## Implementation Order

### Phase 1

Implement:

- runtime process skeleton
- control socket
- metrics emission

### Phase 2

Implement:

- output stream publication
- Python viewer launcher for final stream

### Phase 3

Implement:

- manual calibration command path
- runtime state transitions

### Phase 4

Optionally add:

- low-rate snapshots
- seam/debug overlays

## Final Recommendation

For `v1`, the correct architecture is:

1. native runtime owns the full media path
2. Python owns the control plane
3. the operator sees the final encoded stream through a separate viewer path
4. `optional low-rate debug snapshot` is not required in the first implementation

That gives the operator the real result without reintroducing Python as the hot-path frame owner.
