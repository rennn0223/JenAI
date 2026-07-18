# Local data lifecycle

JenAI stores conversation context and robot-operational records locally. These
files can contain natural-language tasks, map positions, route outcomes, and
tool metadata, so they must be treated as sensitive even when no cloud provider
is configured.

## Data inventory and permissions

| Category | Default path | Contents | Created mode |
|---|---|---|---:|
| Sessions | `~/.config/jenai/sessions/` | Cross-restart `/run` conversation memory | directory `0700`, files/locks `0600` |
| Pending runs | `<config dir>/pending-runs/` | Paused Agent SDK state and approval IDs | directory `0700`, files `0600` |
| Locations | `<config dir>/locations.toml` | Named map-frame poses and aliases | file `0600`; newly created parent directories `0700` |
| Reports | `<config dir>/reports/` | Patrol result JSON | directory `0700`, files `0600` |
| Traces | `~/.config/jenai/traces/` | Local agent trace JSONL | directory `0700`, files `0600` |
| Audit | `<config dir>/audit.sqlite3` | Bounded run/approval/tool/gate metadata; SQLite sidecars are also inventoried | file `0600` |
| Config backups | `<config dir>/config.toml.bak-*` | Timestamped pre-onboard configuration | file `0600`; retained and excluded from routine export/default purge |

`JenAI data status --config PATH` is read-only and reports each category's
resolved path, file count, byte count, root mode, insecure allow-listed child
count, refused unsafe-alias count, and `permissions_ok` result. Sessions and traces are
application-wide; pending runs, locations, reports, audit, and config backups follow the selected
config directory.

Locations, reports, session and pending-run snapshots, trace retention rewrites, onboarding
backups, and export archives use a temporary file in the destination directory followed by an
atomic replace. A failed replacement removes the temporary file and preserves
the prior destination. Trace events use a locked append-only descriptor and
never open the existing trace with truncation.

## Legacy permission hardening

Upgrading does not silently chmod legacy data during a read-only status check.
Audit the exact migration first, then confirm it:

```bash
JenAI data harden --dry-run
JenAI data harden                 # interactive confirmation
JenAI data harden --yes           # apply the displayed plan
```

Hardening only considers the operational allow-list: locations, session and pending-run JSON,
patrol-report JSON, trace JSONL/lock files, audit database/sidecars, timestamped config backups, and
the directories that contain matched generated files. It never selects `config.toml` or `.env`.
Every chmod is revalidated through a no-follow file descriptor against
the planned device/inode and expected file type. Symlinks, files with multiple
hardlinks, config/credential inode aliases, unexpected file types, and paths
changed after planning are refused or skipped and reported for manual review.
Target modes are `0700` for directories and `0600` for files.

## Export

```bash
JenAI data export jenai-data-2026-07-18.tar.gz
JenAI data export backup.tar.gz --config ~/.config/jenai/config.toml
JenAI data export backup.tar.gz --force
```

The export is an atomic `tar.gz` with mode `0600`. It allow-lists locations, session and
pending-run JSON, patrol-report JSON, trace JSONL, and the audit SQLite database; symlinks,
hardlinks, and non-regular files are skipped. It never includes `config.toml`, `.env`, or
`config.toml.bak-*`. Values found in the credential file/environment and common credential
assignments are replaced with `[REDACTED]` inside exported **text**. SQLite audit bytes are not
text-redacted; its schema intentionally stores metadata rather than prompts/raw actions, but the
archive must still be handled as sensitive.

The sanitized archive is therefore not a byte-for-byte backup. Treat it as
sensitive despite redaction: a free-form conversation can contain private data
that is not recognizable as a credential.

## Retention

```bash
# Inspect without changing anything
JenAI data prune --older-than-days 30 --dry-run

# Interactively confirm, or use --yes in an administered job
JenAI data prune --older-than-days 30
JenAI data prune --older-than-days 30 --yes
```

Pruning removes session, pending-run, and patrol-report files whose modification time is older
than the requested age. For trace JSONL and audit SQLite it removes only records with an older
parseable timestamp; recent and malformed records are retained rather than guessed away. Saved
locations, config, credentials, config backups, skills, and rules are never age-pruned.

## Before uninstall

Removing the Python package or `uv tool` entry does not remove user data. Before
uninstalling, run `JenAI data status`, optionally create a sanitized export, and
then use `JenAI data purge` (plus only the protected-category flags you intend).
If JenAI is already uninstalled, remove the paths printed by the last status
report according to the host organization's retention policy. Stop other JenAI
processes before prune or purge; an active writer can recreate generated state.

## Purge

Always inspect the exact plan first:

```bash
JenAI data purge --dry-run
JenAI data purge                 # asks for confirmation
JenAI data purge --yes           # confirms the displayed default plan
```

The default purge removes generated sessions, pending runs, reports, traces, audit SQLite, and
existing audit sidecars. It preserves locations, `config.toml`, `.env`, timestamped config backups,
`skills/`, and `rules.toml`. Each managed protected category requires its own explicit option;
skills and rules remain user-managed configuration:

```bash
JenAI data purge --include-locations
JenAI data purge --include-config
JenAI data purge --include-credentials
JenAI data purge --include-config-backups
```

These options still prompt unless `--yes` is also supplied. The plan prints the
exact resolved paths before the prompt. `--include-config` never implies
`--include-credentials`, and the reverse is also true. Config backups likewise require
`--include-config-backups`; deleting current config does not silently delete historical backups.

Purge is logical deletion, not forensic media erasure. SSD wear levelling,
filesystem snapshots, backups, and journaling can retain old blocks; use the
host organization's encrypted-disk and media-disposal policy when secure erasure
is required.
