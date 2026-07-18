# Support policy

JenAI is an Apache-2.0 research and developer platform. Community support is provided through GitHub
issues on a best-effort basis; no uptime, response-time, or functional-safety SLA is currently
offered.

## Before requesting help

1. Install the latest release and run `JenAI version`.
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

- Latest release on the validated combinations in `docs/SUPPORT_MATRIX.md`.
- Installation, configuration, TUI/WebUI behavior, registered ROS2 capabilities, and Isaac Sim
  reproduction.
- Physical-vehicle reports are welcome, but hardware-specific tuning and safety acceptance remain
  the deployer's responsibility.

Public-internet hosting, multi-tenant operation, certified functional safety, vendor locomotion
controllers, and custom SLAM/Nav2 tuning are outside the v1 support commitment.

Security-sensitive issues must follow `SECURITY.md`, not a public bug report. Upgrade and rollback
steps are in `docs/ROLLBACK.md`.
