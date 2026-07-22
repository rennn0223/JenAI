# Security policy

JenAI controls or supervises software that can move a robot. Security reports therefore need to
distinguish ordinary software bugs from issues that could cause unauthorized actuation, bypass an
approval boundary, expose a credential, or prevent an emergency stop.

## Supported versions

Security fixes target the latest published release. Older releases may receive a fix only when the
maintainer determines that an upgrade is not a practical mitigation. This is a community research
project and does not currently provide an enterprise SLA.

## Reporting a vulnerability

Do not open a public issue containing exploit details, credentials, private maps, or robot network
addresses. Use the repository's private security advisory form:

<https://github.com/rennn0223/JenAI/security/advisories/new>

Include the affected version, interface, deployment topology, reproduction steps, expected impact,
and whether physical actuation occurred. Remove API keys, personal data, camera frames, and exact
site coordinates. If private reporting is unavailable, open a public issue asking the maintainer to
establish a private contact channel, without including vulnerability details.

## Deployment boundary

JenAI v1 is designed for a trusted workstation on an isolated laboratory or site LAN:

- Do not expose the WebUI directly to the public internet.
- Treat TUI access and approved `!`/`/shell` commands as operator-level code execution.
- MCP is stdio and trusts its host process; action tools remain opt-in.
- ROS2 DDS is not authenticated by JenAI. Use network isolation or SROS2 where required.
- Keep API keys in the configured `0600` environment file and never attach it to an issue.
- Use a physical emergency stop for any physical deployment. JenAI's software stop is not a
  replacement for certified hardware protection.

## Provider data egress

Provider choice is also a data-boundary choice. When a cloud OpenAI-compatible provider is active,
JenAI sends the text needed for that request to the configured endpoint. Depending on the command,
that can include user prompts, task and location names, ROS schema excerpts, and tool results.
Vision requests send the selected image or captured camera frame itself. Local trace files are not
automatically uploaded, but that does **not** make cloud inference local.

Before using a cloud provider, review its retention and training terms, obtain any required consent,
and avoid sensitive site names, coordinates, images, or prompts. Select a local endpoint such as
Ollama when those inputs must remain on the workstation. Switching provider changes future calls; it
does not revoke data already sent to a previous provider.

The detailed trust boundaries and deliberate exclusions are documented in
[`docs/validation/THREAT_MODEL.md`](docs/validation/THREAT_MODEL.md). Safety hazards and residual risks are documented in
[`docs/validation/SAFETY_CASE.md`](docs/validation/SAFETY_CASE.md).

## Disclosure and fixes

The maintainer will acknowledge a private report on a best-effort basis, reproduce it in a safe
environment, prepare tests and a fix, and publish a security release when appropriate. A fix is not
considered complete until the affected safety path is tested and the release artifact passes CI and
wheel smoke testing.
