# A1 Engine Contract - 2026-03-06

## Purpose

This document freezes the first interface boundary for `A`.

The rule is:

- Python owns control/UI/logging
- native runtime owns full-rate video processing
- communication between them carries only commands, metrics, and optional low-rate previews

This is the contract that should remain stable even if the runtime wrapper later changes from:

- `A`: standalone executable
- `B`: GStreamer plugin wrapper
- `C`: FFmpeg-centered wrapper

## Process Model

### Python control process

Responsibilities:

- collect operator input
- launch runtime
- send commands
- receive metrics/events
- render logs and optional low-rate preview

### Native runtime process

Responsibilities:

- stream input
- decode
- pair/sync policy
- calibration
- stitch
- encode/send
- metrics production

## Non-Goals For Control IPC

The control link must not carry:

- 1080p raw BGR frames
- full-rate panorama frames
- full-rate left/right stream frames

That traffic stays inside the runtime.

## Core Contract File

The initial contract draft is implemented in:

- `stitching/runtime_contract.py`

That file defines:

- `StreamSpec`
- `PreviewSpec`
- `OutputSpec`
- `EngineConfig`
- `ManualPoint`
- `SnapshotRequest`
- `EngineCommand`
- `EngineMetrics`
- `ManualState`
- `RuntimeEvent`

## Command Flow

### Boot sequence

1. Python launches runtime
2. Runtime emits `hello`
3. Python sends `start`
4. Runtime emits `started`
5. Runtime periodically emits `metrics`

### Manual calibration sequence

1. Python sends `set_manual_mode`
2. Python sends repeated `add_manual_point`
3. Runtime emits `manual_state`
4. Python sends `reset_auto_calibration` if operator aborts

### Stop sequence

1. Python sends `stop`
2. Runtime flushes state
3. Runtime emits `stopped`
4. Python may send `shutdown`

## Message Shapes

### Start command

```json
{
  "seq": 1,
  "type": "start",
  "payload": {
    "config": {
      "left": {
        "name": "left",
        "url": "rtsp://...",
        "transport": "tcp",
        "timeout_sec": 10.0,
        "reconnect_cooldown_sec": 1.0
      },
      "right": {
        "name": "right",
        "url": "rtsp://...",
        "transport": "tcp",
        "timeout_sec": 10.0,
        "reconnect_cooldown_sec": 1.0
      },
      "input_runtime": "ffmpeg-cuda",
      "sync_pair_mode": "none",
      "process_scale": 1.0,
      "stitch_every_n": 1,
      "gpu_mode": "on",
      "gpu_device": 0
    }
  }
}
```

### Metrics event

```json
{
  "seq": 48,
  "type": "metrics",
  "timestamp_sec": 12345.67,
  "payload": {
    "status": "stitching",
    "frame_index": 1024,
    "left_fps": 29.9,
    "right_fps": 29.8,
    "stitch_fps": 58.2,
    "worker_fps": 58.2,
    "matches": 188,
    "inliers": 63,
    "gpu_enabled": true,
    "gpu_warp_count": 1024,
    "gpu_blend_count": 1024
  }
}
```

### Manual point command

```json
{
  "seq": 90,
  "type": "add_manual_point",
  "payload": {
    "point": {
      "side": "left",
      "x": 932.0,
      "y": 416.0,
      "width": 1920,
      "height": 1080
    }
  }
}
```

## Engine Boundary Rules

The native engine may depend on:

- CUDA
- FFmpeg / NVDEC / NVENC
- OpenCV native APIs if still needed

The native engine must not depend on:

- Python `numpy` frame ownership at runtime boundary
- `cv2.imshow`
- Python thread scheduling for full-rate processing

## Reuse Plan

The following concepts are intended to survive the migration:

- stitch config semantics
- manual calibration semantics
- metrics names already used in logs
- blend / exposure policy names

The following implementation details are not intended to survive:

- Python RTSP reader classes as the hot path
- Python frame queues as the hot path
- Python-owned frame pairing as the hot path

## Required Next Step

The next implementation step after this contract freeze is:

- add a Python-side runtime launcher/client that speaks this contract

That allows the current UI/CLI to stay alive while the native runtime is introduced behind a stable boundary.
