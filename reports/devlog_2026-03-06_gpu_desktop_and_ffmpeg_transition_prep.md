# Development Log: GPU Desktop Stitching and FFmpeg Transition Prep

Date: 2026-03-06

## Project Goal

The practical goal of this project is low-latency real-time panorama stitching from two RTSP cameras.

The immediate performance target was 30 fps.
The longer-term target is 60 fps or higher for real-time stitched output and eventual network delivery.

## Progression Summary

### 1. Initial direction changes

- Removed web-centric and unnecessary offline/preset UI paths from the active workflow.
- Moved focus to a desktop program for live RTSP preview and stitching.
- Added direct RTSP input handling in the desktop path.

Reason:
- The user needed immediate live behavior, not offline batch workflows.
- A local desktop loop is easier to debug than a browser path for frame timing and stitching behavior.

### 2. Live desktop stitching pipeline

- Added a desktop RTSP reader and worker thread model.
- Added panorama preview and system monitor output.
- Added manual calibration mode with point selection and auto/manual switching.
- Added status metrics for matches, inliers, worker fps, stitched fps, GPU usage, CPU usage, and stale frame counts.

Reason:
- Before optimizing, the project needed observability.
- The user repeatedly asked for real-time diagnostics instead of blind tuning.

### 3. GPU enablement

- Built and installed a CUDA-enabled OpenCV in the Python 3.12 environment.
- Verified `cv2.cuda.getCudaEnabledDeviceCount()` works after driver update.
- Added GPU-on default behavior with fallback logic.

Reason:
- CPU-only stitching was not sufficient for the real-time target.
- GPU capability had to be validated before deeper pipeline work.

### 4. Current desktop optimizations

- Split dashboard refresh from panorama refresh.
- Added headless benchmark mode to measure pure stitching throughput without GUI cost.
- Moved blending to GPU path in the steady-state path.
- Reduced full-frame GPU blending cost by switching to overlap-ROI-based GPU blend.
- Reused more GPU-side resources instead of rebuilding them every frame.
- Simplified sync mode for performance testing by making `sync_pair_mode=none` the default in the desktop path.

Reason:
- Logs showed that UI cost was part of the problem, but not the whole problem.
- Headless measurement showed the true stitching ceiling.
- Full-frame blend work was wasting compute outside the overlap region.

## Measured Findings So Far

### Before latest ROI optimization

- Headless benchmark was around 20 fps.
- Local panorama output was lower than headless due to display and download cost.
- GPU was active, but end-to-end throughput was still below the 30 fps target.

### After overlap ROI GPU blend optimization

- Headless benchmark improved to about 22-23 fps.
- Input streams remain around 30 fps each.
- Stale frame counters keep increasing, proving the worker still cannot sustain input rate.

Interpretation:
- GUI/display cost is not the main blocker anymore.
- The current runtime bottleneck is the processing pipeline itself.

## Why the current structure is still limited

### Current runtime shape

The desktop path currently behaves like this:

1. RTSP stream arrives compressed as H.264.
2. OpenCV VideoCapture uses FFmpeg backend internally.
3. Frames are decoded on CPU into numpy arrays.
4. Frames are uploaded to GPU for warp and blend.
5. Some paths still require GPU-to-CPU transfer depending on output mode.

This means the pipeline is not GPU end-to-end.

### Main structural limits

- Decode is CPU-side.
- Frames cross CPU/GPU memory boundaries.
- The worker loop is still a Python/OpenCV runtime loop, not a dedicated media pipeline.

## Technology choice discussion

### Current OpenCV-based runtime

Pros:
- Fast to iterate.
- Good for calibration, debugging, and algorithm development.
- Existing codebase already contains stitching logic, calibration control, metrics, and UI.

Cons:
- CPU decode through OpenCV VideoCapture.
- Limited control over low-latency decode behavior.
- Not a strong final architecture for 60 fps stitched delivery.

### Direct FFmpeg control

Pros:
- Natural next step from current OpenCV+FFmpeg backend usage.
- Better control over RTSP ingest and hardware acceleration.
- Practical migration path without rewriting all stitching logic at once.

Cons:
- Still requires careful integration with the current Python/OpenCV stitching core.
- Not automatically zero-copy unless the processing path is redesigned around it.

Decision:
- This is the best next transition step for the current repository.

### GStreamer

Pros:
- Strong real-time media pipeline model.
- Better long-term fit for production media graphs.

Cons:
- Higher integration cost in the current codebase.
- Windows setup is more fragile.
- Overkill for the immediate next step while algorithm/runtime control still lives in Python/OpenCV.

Decision:
- Consider only after direct FFmpeg control has been attempted and evaluated.

## Current decision

Proceed with:

1. Freeze and record the current GPU-accelerated desktop milestone.
2. Create a new branch for FFmpeg-direct runtime transition.
3. Keep the current stitching logic as long as possible.
4. Replace the runtime ingest/output path first.

## Next branch intent

Target of the next branch:

- Replace OpenCV `VideoCapture` RTSP ingest path with direct FFmpeg-managed ingest.
- Keep the current stitching core initially.
- Prepare for a later output path that can move toward hardware-assisted encode/send.

