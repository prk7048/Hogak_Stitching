# Validation Rollout And Acceptance Spec

## Summary
This document defines how the redesign is rolled out and how it is judged.

The current validation story is problematic because validation can mutate runtime geometry or restore state before testing. That means the validation result is entangled with preparation behavior. The replacement is a strict read-only validation path plus explicit preparation and execution phases.

## Phase Definitions
- `prepare-runtime`: may create or replace geometry artifacts.
- `run-runtime`: consumes prepared artifacts only.
- `validate-runtime`: reads prepared artifacts and runtime inputs, but never mutates geometry or lens files.

## Rollout Sequence
1. Freeze the schema and contract.
2. Separate prepare, run, and validate behavior.
3. Introduce the supervisor boundary.
4. Switch geometry to the cylindrical baseline.
5. Make transmit mode truthful and explicit.
6. Remove legacy aliases and fallback-only surface area.

This order is better than trying to improve stitch quality first because it makes the system measurable before it is optimized.

## Acceptance Checks
### Contract
- Launch and reload must accept the same schema version.
- Unknown keys must fail.
- Invalid enum values must fail.

### Validation
- `validate-runtime` must leave geometry and lens artifacts unchanged.
- Validation output must identify the runtime baseline that was tested.

### Lifecycle
- `run -> stop -> run` must not leave orphan processes or stale ports.
- Preview/viewer workers must be cleaned up on stop.

### Geometry
- Cylindrical baseline must improve overlap agreement relative to homography-only baseline.
- Seam path jitter must remain bounded on static or slow-moving scenes.

### Performance
- The runtime must remain at or near the 30 fps service target.
- Transmit fallback should be explicit, not silent.

## Why The Current Choices Are Problematic
| Area | Current choice | Problem |
|---|---|---|
| Validation | Prepares or restores runtime geometry | It is not a clean measurement of runtime behavior. |
| Rollout | Mixed responsibility across docs, UI, CLI, and runtime | It is too hard to tell what changed and why. |
| Acceptance | Focused on baseline runtime without enough truthfulness in output mode | Operators cannot easily tell which path actually ran. |

## Rollback Triggers
Rollback or halt if:

- the runtime loses its 30 fps service target for the normal case,
- validation mutates artifacts unexpectedly,
- output mode truthfulness is lost,
- or the cylindrical baseline regresses quality on the reference pair.

