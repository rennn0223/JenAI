# ADR 0003: Task outcome is separate from run lifecycle

- Status: Accepted
- Date: 2026-07-24

## Context

A process can finish normally while the requested real-world effect remains
unverified. For example, Nav2 can reach a dock approach pose even though the
current Isaac Sim reference platform provides no charging feedback. Treating
every completed run as success would mislead operators and invalidate
experiments.

## Decision

JenAI records product-level Task Outcome separately from run lifecycle state.
Lifecycle state describes whether execution is queued, running, completed, or
interrupted. Task Outcome describes whether the task's Completion Contract was
verified.

The authoritative outcomes are `succeeded`, `arrived_unverified`, `partial`,
`endpoint_mismatch`, `blocked`, `unavailable`, `failed`, and `cancelled`.
Receipts and user-facing summaries must include the outcome and supporting
evidence.

## Consequences

- A completed run may legitimately be `arrived_unverified` or
  `endpoint_mismatch`.
- Downstream reports must not infer success from lifecycle state alone.
- Capabilities must declare their Completion Contract and valid outcomes.
- Historical records remain auditable as verification becomes more capable.
