# Final Stream Observer Architecture - 2026-03-09

## Goal

The operator wants to see the final stitched result without letting the Python control/UI path become the hot-path frame owner.

The required property is:

- native runtime owns full-rate video processing
- Python control/UI must not block or slow the engine
- operator should be able to inspect the actual final output stream, not just an internal debug image

This document defines that structure.

## Core Decision

Use the final encoded output stream itself as the primary operator-visible preview source.

That means:

- the engine produces one real output stream for downstream consumers
- Python UI behaves like an external client/viewer for that stream
- Python does not receive full-rate raw panorama frames from the engine

This keeps the hot path inside the native runtime and avoids recreating the current Python-owned frame bottleneck.

## Target Topology

```text
left input ->
             \
              decode -> pair/sync -> stitch -> encode -> output stream -----> downstream consumer
             /
right input ->

                               |-> metrics channel -------------------------> Python control/UI
                               |-> optional low-rate snapshot channel -----> Python control/UI
                               |-> final stream observer client -----------> Python control/UI viewer
```

## Process Layout

### Process 1. Native runtime engine

Responsibilities:

- open left/right inputs
- decode frames
- buffer and pair frames
- run stitch pipeline
- encode final panorama
- publish final output stream
- publish metrics events
- optionally publish low-rate snapshots

This process owns:

- full-rate left/right frames
- full-rate stitched frames
- GPU buffers
- encode pipeline

This process must never wait for Python UI rendering.

### Process 2. Python control/UI

Responsibilities:

- start/stop/reconfigure engine
- display metrics/logs
- provide manual calibration controls
- optionally show low-rate snapshots
- optionally view the final stream as a normal client

This process must not own:

- full-rate raw left/right frames
- full-rate raw stitched frames
- decode/stitch hot path buffers

## Channels

### Channel A. Control channel

Direction:
- Python -> engine
- engine -> Python

Payload:
- commands
- status
- metrics
- warnings/errors
- manual calibration state

Transport options:
- local TCP
- named pipe
- stdin/stdout JSON protocol

Properties:
- small messages only
- no raw video payloads

### Channel B. Final output stream

Direction:
- engine -> network target
- Python UI may also subscribe as a viewer client

Payload:
- encoded panorama stream

Transport options:
- RTSP
- RTMP
- SRT
- MPEG-TS over UDP/TCP

Properties:
- this is the real operator-visible output
- Python views this as a normal receiver
- engine does not send raw panorama frames to Python

### Channel C. Optional low-rate snapshot channel

Direction:
- engine -> Python UI

Payload:
- low-rate JPEG/PNG or shared-memory preview frame

Properties:
- optional
- low frequency only (for example 1-2 fps)
- latest-only overwrite, never blocking
- used only for diagnostics or calibration helper views

## Why Final-Stream Viewing Is Preferred

If Python receives full-rate stitched raw frames directly, the system recreates the old problem:

- frame becomes Python-visible object
- Python becomes part of the hot path
- preview backpressure can slow the engine
- memory traffic increases

If Python instead subscribes to the already encoded final stream:

- engine keeps ownership of the real-time pipeline
- Python is only a consumer client
- preview path cannot force raw-frame ownership into Python
- operator sees the actual transmitted result

This is the correct preview mode for production behavior.

## Required Engine Guarantees

The engine must be designed so that observer/viewer behavior cannot stall the hot path.

Required rules:

1. encode/output path is part of the engine, not Python
2. control channel is non-video, small-message only
3. snapshot channel is optional and non-blocking
4. if a viewer disconnects, the engine continues running
5. if Python UI lags, metrics may be dropped/throttled, but stitching continues

## Recommended Observer Modes

### Mode 1. Production observer mode

Python UI opens the final encoded stream as an external client.

Use when:
- validating real transmitted quality
- checking encode artifacts
- checking end-to-end latency
- operator monitoring during production

Pros:
- closest to actual deployment behavior
- no raw stitched frame ownership in Python
- easiest correctness check

Cons:
- includes encode and transport latency
- not ideal for pixel-level debugging

### Mode 2. Calibration/debug observer mode

Python UI receives low-rate snapshots from the engine.

Use when:
- checking seam position
- checking manual calibration quality
- visualizing internal intermediate state

Pros:
- useful for debugging
- does not require full stream viewer path

Cons:
- must remain low-rate
- not the true final stream

Recommended policy:
- keep this disabled by default in production
- enable only on demand

## Engine-Side Output Strategy

The engine should expose two output concepts:

1. primary output
- full-rate encoded panorama stream
- single source of truth

2. debug snapshot
- low-rate optional side output
- never part of the hot-path contract

This prevents the system from confusing operator preview with the real runtime output.

## Python UI Responsibilities In This Design

Python UI should do these things:

- launch runtime with output URL/port settings
- render metrics panel
- display connection/health state
- open the final stream using a separate viewer component when needed
- provide manual calibration commands

Python UI should not do these things:

- ingest stitched raw frames at full rate
- perform decode on behalf of the engine for the main stream
- block engine on preview consumption

## Minimum Implementation Plan

### Step 1. Native runtime owns output

Deliverable:
- native runtime can encode and publish a panorama stream
- output target configurable from control channel or startup config

### Step 2. Python control/UI subscribes as observer

Deliverable:
- Python UI can open the final stream in a separate viewer panel/process
- failure of viewer must not affect runtime

### Step 3. Add low-rate debug snapshots

Deliverable:
- engine can optionally publish 1-2 fps snapshots for calibration/debug
- snapshots use latest-only overwrite semantics

### Step 4. Add watchdog/health indicators

Deliverable:
- Python UI shows runtime status
- stream health
- encode health
- output bitrate/fps
- queue depth/stale counters

## Recommended Implementation Boundary

The runtime contract should distinguish clearly between:

- control plane
- data plane

### Control plane

Small messages only:
- start/stop
- config
- manual calibration commands
- metrics
- status

### Data plane

Actual media transport only:
- encoded panorama output stream
- optional low-rate preview snapshots

Python UI belongs to the control plane.
The final panorama stream belongs to the data plane.

## Failure Behavior

Desired failure behavior:

- if Python UI crashes, engine continues
- if viewer disconnects, engine continues
- if snapshot consumer is slow, snapshot is dropped
- if output target is unavailable, engine reports error but control channel stays alive

This is why preview/viewer must not be inside the main hot path.

## Recommendation

Use the final encoded stream as the operator-visible result.

Use Python only for:
- control
- metrics
- optional low-rate debug snapshots
- external viewing of the already encoded stream

Do not send full-rate raw panorama frames to Python.

That is the cleanest way to let the operator inspect the real output while protecting engine FPS.
