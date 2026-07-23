# ADR 0001: Natural-language dual path with unified verification

- Status: Accepted
- Date: 2026-07-24

## Context

JenAI must respond quickly to common robot requests without reducing the agent
to a collection of slash commands. More complex or ambiguous requests still
need model-assisted reasoning. Either path can cause physical or simulated
motion, so speed cannot create a second, weaker definition of success.

## Decision

Natural language remains the primary interface. JenAI uses:

1. a deterministic Fast Path for common, unambiguous intents; and
2. an LLM-assisted Agent Path for complex or ambiguous intents.

Both paths use the same capability registry, approval boundary, execution
interfaces, Completion Contracts, evidence sources, and Task Outcomes. Slash
commands remain shortcuts for discovery, debugging, and reproducibility.

JenAI exposes concise auditable progress stages, but never raw model
chain-of-thought.

## Consequences

- Common commands can avoid model latency.
- Complex requests retain genuine agent planning.
- A result means the same thing regardless of how the request was interpreted.
- Fast-path matching must fail closed into the Agent Path when intent is
  ambiguous.
- Verification logic becomes shared product infrastructure and cannot live only
  inside individual UI commands.
