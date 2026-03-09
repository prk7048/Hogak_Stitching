# FFmpeg Direct Runtime Transition Plan

Date: 2026-03-06
Branch intent: `feature/ffmpeg-runtime`

## Objective

Move the runtime ingest/output path away from OpenCV-managed RTSP/video I/O and toward direct FFmpeg control, while keeping the current stitching core alive as long as possible.

## Current runtime

Desktop path today:

1. OpenCV VideoCapture opens RTSP.
2. OpenCV/FFmpeg backend decodes on CPU.
3. Frames become numpy arrays on host memory.
4. Frames are uploaded to CUDA for warp/blend.
5. Output is displayed locally or benchmarked headless.

Live path today:

1. OpenCV VideoCapture opens RTSP.
2. OpenCV/FFmpeg backend decodes on CPU.
3. Frames are stitched in Python/OpenCV.
4. Output is encoded to disk with OpenCV VideoWriter (`mp4v`), not streamed.

## Migration principle

Do not rewrite everything at once.

First move:

- RTSP ingest control
- encoder/output control

Keep for now:

- homography estimation
- seam logic
- manual calibration logic
- metrics and diagnostics

## Phase plan

### Phase 1: Runtime discovery and command generation

Deliverables:

- FFmpeg/ffprobe binary discovery
- canonical decode command builder
- canonical NVENC output command builder
- CLI diagnostic entrypoint

Status:

- Implemented in this branch as initial scaffolding.

### Phase 2: FFmpeg decode subprocess reader

Replace `cv2.VideoCapture` RTSP reader with:

- `ffmpeg` subprocess
- rawvideo pipe output
- numpy frame reconstruction

This still lands in CPU memory, but gives us direct control over:

- transport
- low-latency flags
- hwaccel flags
- decode backend selection

Status:

- Initial implementation added in this branch:
  - `stitching/ffmpeg_runtime.py`: binary discovery, ffprobe summary, decode/NVENC command builders
  - `stitching/ffmpeg_reader.py`: rawvideo pipe reader with buffer semantics compatible with the desktop stitch worker
  - `stitching/desktop_app.py`: `--input-runtime {opencv,ffmpeg,ffmpeg-cpu,ffmpeg-cuda}` switch

### Phase 3: FFmpeg output subprocess writer

Replace local display or file-only write path with:

- `ffmpeg` subprocess that reads raw frames from stdin
- NVENC encoder
- RTSP/RTMP/SRT output target

This gives us the first practical end-to-end streaming pipeline.

Status:

- Initial implementation added in this branch:
  - `stitching/ffmpeg_writer.py`: rawvideo stdin writer for FFmpeg output
  - `stitching/live_stitching.py`: `output_runtime=opencv|ffmpeg` integration
  - `stitching/cli.py`: `live` mode now exposes FFmpeg output runtime options

Current scope:

- local file output via FFmpeg writer
- URL targets are supported by command generation, but not yet validated end-to-end in this environment

### Phase 4: Evaluate throughput again

If throughput is still below target:

- the bottleneck is no longer just OpenCV I/O
- stitching runtime itself must move lower

### Phase 5: Native/GStreamer-level runtime only if still needed

Only after Phase 4 fails to hit target should we move toward:

- deeper FFmpeg integration
- GStreamer pipeline
- native C++/CUDA runtime

## Decision rationale

This phased plan is chosen because:

- it matches the current code layout
- it reduces risk
- it preserves the existing stitching logic during transition
- it allows measurement after each major I/O change
