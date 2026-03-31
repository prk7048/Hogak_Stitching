# ADR-005: Virtual-Center Rectilinear As A Bakeoff Candidate

## Current Choice
The current runtime baseline is `cylindrical-affine` with a left-anchor style runtime structure.

## Why It Is A Problem In This Project
This project uses a same-position left/right camera layout and already-rectified input streams.

That makes the current structure feel asymmetric:

- the left image behaves like the native output frame
- the right image carries most of the warp burden
- scale or shape errors are easier to notice on the right edge

If the observed right-edge enlargement is structural rather than calibration noise, a more symmetric virtual camera model is worth testing.

## Alternatives Considered
- Keep `cylindrical-affine` as the only model
- Replace cylindrical with virtual-center rectilinear immediately
- Introduce `virtual-center-rectilinear` as a bakeoff candidate while keeping cylindrical as the baseline

## Chosen Best Option
Introduce `virtual-center-rectilinear` as an artifact-level candidate model first, not as an immediate runtime default.

## Why This Is The Best Fit Here
This option gives the project a realistic way to test the geometry hypothesis without lying about runtime readiness.

It fits the current architecture because:

- geometry is already chosen by artifact path
- prepare/run/validate already revolve around one active artifact
- the GPU-only branch can reject unsupported runtime paths instead of silently falling back

It also keeps rollback simple: switch artifacts back to the cylindrical baseline.

## Costs / Side Effects
- artifact schema becomes more general
- validation and operator messaging must distinguish between candidate-only and launch-ready models
- runtime implementation is still required later
- rectilinear may still lose against cylindrical on very wide final FOV scenes

## What Must Be Proven
- the candidate reduces right-edge enlargement or asymmetry
- overlap quality and seam stability are at least as good as the cylindrical baseline
- GPU-only throughput does not regress unacceptably
- runtime truth and validation messaging remain honest throughout the rollout
