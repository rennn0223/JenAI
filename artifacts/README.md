# Local artifacts

This directory stores local-only evidence and generated run outputs. Its contents are excluded
from Git and release packages; only this index is versioned. Never overwrite a failed or formal run.

- `isaac-hil-*.json`: Isaac Sim HIL and preflight evidence.
- `experiments/e1/`: bounded-decision evaluation outputs.
- `experiments/e2/`: Twin Gate ablation and paired reanalysis outputs.
- `experiments/e3/`: natural-language ROS 2 discover–execute–verify outputs.
- `experiments/e4/`: model decision-latency outputs.
- `experiments/soak/`: daemon soak reports, RSS samples, and logs.

Formal claims and hashes are indexed in [EVIDENCE_LEDGER](../docs/validation/EVIDENCE_LEDGER.md).
