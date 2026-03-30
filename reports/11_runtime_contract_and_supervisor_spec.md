# Runtime Contract And Supervisor Spec

## Summary
This document defines the runtime control plane contract and the process ownership model.

The current contract is problematic because Python and native code do not agree on one field set, and the native side still parses commands by searching for substrings inside JSON-shaped text. That is fragile, hard to extend, and impossible to reason about when the schema changes. The supervisor story is also too fragmented: UI, CLI, and runtime all participate in lifecycle behavior.

The replacement is a single JSON schema with one parser, one reload behavior, and one supervisor in Python.

## Contract Principles
1. One schema version.
2. One canonical field set.
3. Unknown fields fail fast.
4. Partial reloads are allowed only when they are schema-valid partial updates.
5. Command and event names must be explicit, not implied by string matching.

## Canonical Shape
The canonical config shape should be:

- `inputs.left`
- `inputs.right`
- `geometry`
- `timing.sync`
- `outputs.probe`
- `outputs.transmit`
- `runtime`

This shape is better than the current flat alias set because it tells the truth about ownership and makes nested validation straightforward.

## Command Model
The runtime should support:

- `prepare-runtime`
- `run-runtime`
- `validate-runtime`
- `start`
- `stop`
- `shutdown`
- `reload-config`
- `request-snapshot`

The first three are canonical user-facing lifecycle phases. The last group is the wire-level runtime control set.

## Supervisor Responsibilities
The Python supervisor should own:

- process launch
- hello handshake wait
- metrics polling
- viewer attach
- preview worker management
- graceful stop
- forced cleanup fallback

The supervisor should not contain stitching logic. It should orchestrate, observe, and clean up.

## Why The Current Choices Are Problematic
| Area | Current choice | Problem |
|---|---|---|
| Parsing | Manual field extraction in native control code | It is not a real JSON contract and breaks easily when the schema evolves. |
| Naming | Multiple aliases for the same output role | Humans cannot tell which field is authoritative. |
| Lifecycle | UI and CLI each own parts of runtime startup and shutdown | Failure modes become hard to reproduce and harder to clean up. |
| Reload | Config reload and output alias handling drift independently | A reload can silently differ from the config that launched the runtime. |

## Preferred Replacement
The replacement should:

- Parse JSON with a real library in native code.
- Validate commands against typed schemas.
- Reject unknown keys.
- Treat the supervisor as the only place where lifecycle policy lives.
- Keep UI and CLI thin.

## Acceptance Rules
- Launch and reload must use the same schema version.
- Unknown keys must fail immediately.
- A stop command must leave no orphan child processes or preview workers.
- The runtime must expose a stable hello event and stable metrics event.

