# ADR-004: Native Transmit Baseline And Reproducible Build

## Current Choice
The current transmit story mixes `gpu-direct` and `ffmpeg` behaviors, while the build/runtime discovery depends on local environment assumptions.

## Why This Is A Problem Here
The current output story is not truthful enough. The operator cannot easily tell when the runtime is directly using native GPU output, bridging through another step, or falling back to an external process. The build story is also fragile because it relies on incidental PATH and local folder state.

## Alternatives Considered
- Keep `ffmpeg` as a co-equal default transmit path.
- Keep `gpu-direct` as a hidden optimization and let the runtime decide silently.
- Make native NVENC the preferred baseline and keep explicit fallback modes.

## Chosen Best Option
Make native NVENC the preferred transmit baseline, with explicit fallback modes and explicit runtime reporting. Make the build reproducible through pinned bootstrap scripts, version manifests, and CMake presets.

## Why This Is Best For This Repo
- It reflects the actual performance goal of the project.
- It makes runtime behavior inspectable instead of inferred.
- It keeps the fast path native while still preserving a debug/fallback escape hatch.
- It makes the project easier to reproduce across machines.

## Cost
- Some legacy naming and fallback assumptions must change.
- Build/bootstrap scripts need to become more explicit.

## Proof Required
- The active transmit mode is reported truthfully.
- Fallback behavior is explicit and testable.
- A clean machine can reproduce the build from documented pinned inputs.

