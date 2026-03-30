# ADR-002: Prepare / Run / Validate Boundary

## Current Choice
The current flow allows validation to prepare or restore geometry before it runs.

## Why This Is A Problem Here
Validation is supposed to measure the runtime. If it also mutates the runtime artifacts, then the measurement and the preparation are mixed together. That makes results hard to trust and makes failures hard to reproduce.

## Alternatives Considered
- Keep validation as a combined prepare-and-measure step.
- Add an extra flag to disable mutation sometimes.
- Separate prepare, run, and validate into distinct commands.

## Chosen Best Option
Separate `prepare-runtime`, `run-runtime`, and `validate-runtime`.

## Why This Is Best For This Repo
- It makes validation read-only and therefore reliable.
- It makes preparation an explicit operator action instead of hidden behavior.
- It matches the way real operators think about the system.
- It gives the redesign a clean migration path for geometry artifacts.

## Cost
- Some commands and scripts must change.
- Existing automation that relied on implicit prep must be updated.

## Proof Required
- `validate-runtime` does not change geometry or lens artifacts.
- `prepare-runtime` is the only path allowed to create or replace them.
- `run-runtime` refuses missing or incompatible prepared artifacts.

