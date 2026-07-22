# Support policy

JenAI is an Apache-2.0 research and developer platform. Community support is provided through GitHub
issues on a best-effort basis; no uptime, response-time, or functional-safety SLA is currently
offered.

## Before requesting help

1. If you are an authorized collaborator, use authenticated GitHub CLI access to install an
   immutable release wheel with its matching constraints and verified `SHA256SUMS` (see
   `docs/QUICKSTART.md`), then run `JenAI version`. This private repository does not currently
   provide an anonymous public download channel.
2. Run `JenAI doctor` in the same shell used to start JenAI.
3. For ROS2 problems, record `ROS_DISTRO`, `ROS_DOMAIN_ID`, RMW implementation, Nav2 availability,
   and whether the issue occurs in Isaac Sim or on physical hardware.
4. Search existing issues and the troubleshooting sections in `docs/QUICKSTART.md` and
   `docs/ONBOARDING.md`.

## What to include

Provide the JenAI version, operating system, Python version, provider/model, exact command,
expected result, actual result, and the smallest safe reproduction. Attach sanitized `doctor`
output and audit/report identifiers when relevant. Do not upload API keys, `.env`, private maps,
camera images, site coordinates, or unredacted model prompts.

## Support scope

- Release wheels installed with their same-release constraints on the validated combinations in
  `docs/operations/SUPPORT_MATRIX.md`.
- A source install pinned to an exact reviewed tag or full commit SHA is supported, but has not
  received the same isolated install lifecycle validation as the release wheel. Moving branches
  and unpinned source snapshots are not reproducible support targets.
- Installation, configuration, TUI/WebUI behavior, registered ROS2 capabilities, and Isaac Sim
  reproduction.
- Physical-vehicle reports are welcome, but hardware-specific tuning and safety acceptance remain
  the deployer's responsibility.

Public-internet hosting, multi-tenant operation, certified functional safety, vendor locomotion
controllers, and custom SLAM/Nav2 tuning are outside the v1 support commitment.

Security-sensitive issues must follow `SECURITY.md`, not a public bug report. Upgrade and rollback
steps are in `docs/operations/ROLLBACK.md`; local export, retention, purge, and uninstall data boundaries are
in `docs/operations/DATA_LIFECYCLE.md`.
