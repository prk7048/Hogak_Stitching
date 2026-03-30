# ADR-001: Versioned Runtime Schema

## Current Choice
The current project uses a flat, alias-heavy runtime contract with multiple names for the same role.

## Why This Is A Problem Here
This repo already shows drift between Python, launcher, and native runtime. That means the current contract is not a safe source of truth. In practice, it makes reload behavior ambiguous, makes validation harder, and makes it too easy for one side of the stack to evolve independently.

## Alternatives Considered
- Keep the current alias-heavy flat contract.
- Move only the native parser to a better implementation while keeping the schema flat.
- Adopt a new canonical schema with versioning.

## Chosen Best Option
Adopt a versioned canonical schema, `schema_version = 2`, with nested sections for inputs, geometry, timing, outputs, and runtime.

## Why This Is Best For This Repo
- It removes ambiguity instead of just renaming it.
- It makes Python and native code agree on one truth.
- It supports partial reloads safely because the schema boundaries are explicit.
- It gives us a stable base for later geometry and output changes.

## Cost
- Breaking changes to config and command payloads.
- Short-term migration work in Python and native code.

## Proof Required
- Unknown fields fail.
- Launch and reload use the same schema.
- The schema can represent the current runtime truth without aliases.

