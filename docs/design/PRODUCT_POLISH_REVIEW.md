# JenAI Product Polish UI Review

> Review date: 2026-07-30
> Scope: first-run setup, the approved Claude Code-style TUI, the auxiliary WebUI, and the
> documentation website. This is engineering usability evidence, not a user study.

## Design contract

- The TUI remains the terminal-first primary interaction surface.
- The approved transcript layout, dachshund mascot, composer, approval hierarchy, colours, and
  responsive breakpoints are unchanged.
- The WebUI remains an auxiliary monitoring and approval surface; it does not replace the TUI or
  create a second robot execution path.
- No Nav2 algorithm, AMCL parameter, arrival tolerance, endpoint recovery, Capability, TUI motion
  path, or dependency changed. WebUI confirmation cancellation and STOP lifecycle semantics did
  change; the targeted live acceptance for that lifecycle is recorded below.

## Rendered before / after

The TUI images below are real Textual `export_screenshot()` renders from `origin/main` and this
branch. They use the same deterministic provider fixture and viewport.

### Wide terminal — 120 × 30

| Before | After |
|---|---|
| ![TUI before, wide](evidence/product-polish/tui-before-wide.svg) | ![TUI after, wide](evidence/product-polish/tui-after-wide.svg) |

### Narrow terminal — 56 × 24

| Before | After |
|---|---|
| ![TUI before, narrow](evidence/product-polish/tui-before-narrow.svg) | ![TUI after, narrow](evidence/product-polish/tui-after-narrow.svg) |

The WebUI and documentation website images below are rendered from their real production HTML and
CSS at a fixed 1440 × 1000 viewport. Keyboard interaction and the narrow responsive layout were
separately exercised in real headless Firefox through WebDriver. Firefox received a 390 × 844
outer-window request and reported a 500 × 758 effective inner viewport for this run; the manifest and
browser screenshots are preserved
beside these images. Inspectable WebUI documents are also preserved as
[before](evidence/product-polish/webui-before.html) and
[after](evidence/product-polish/webui-after.html).

### Auxiliary WebUI — desktop

| Before | After |
|---|---|
| ![WebUI before, desktop](evidence/product-polish/webui-before-desktop.png) | ![WebUI after, desktop](evidence/product-polish/webui-after-desktop.png) |

### Documentation website — desktop

| Before | After |
|---|---|
| ![Website before, desktop](evidence/product-polish/website-before-desktop.png) | ![Website after, desktop](evidence/product-polish/website-after-desktop.png) |

### Real-browser narrow acceptance — measured 500 × 758 inner viewport

| Auxiliary WebUI | Documentation website |
|---|---|
| ![WebUI in Firefox narrow viewport](evidence/product-polish/webui-browser-mobile.png) | ![Website in Firefox narrow viewport](evidence/product-polish/website-browser-mobile.png) |

The real-browser gate executed the same event handlers shipped to users:

- WebUI `/` palette: typing, `ArrowDown`, `Tab` completion, and focus retention.
- WebUI tabs: `ArrowRight` and `End` both activate and focus the expected tab.
- Website search: typed query, `ArrowDown`, and `Escape` with combobox focus retained.
- Website mobile menu: `Enter` toggles `aria-expanded` and opens the navigation.
- Both narrow layouts: document width and the primary interaction region remain inside the viewport.

Machine-readable results: [browser-acceptance.json](evidence/product-polish/browser-acceptance.json).

## First useful task checklist

| Journey | Expected user experience | Evidence |
|---|---|---|
| First launch | Three short steps; an invalid field explains what to fix without discarding earlier answers | `test_setup_ux.py` |
| Setup complete | Shows the real config and locations paths, then leads to `JenAI doctor` and `JenAI` | `test_setup_ux.py` |
| TUI welcome | Leads with a natural-language task, then `/doctor` and `/help`; the approved visual layout stays intact | wide/narrow renders and `test_tui_ux_copy.py` |
| Discover a command | `/` opens the palette; keyboard navigation and descriptions use operator language | `test_tui.py`, `test_tui_command_dispatch.py` |
| Approve motion | Risk and physical effect are explicit; options remain one-time, session, or reject; `Esc` rejects | `test_tui_ux_copy.py`, approval-policy tests |
| Web monitor | Current task, pending approvals, tool timeline, and current-service run history survive browser refresh; exact redacted action parameters remain visible while binding digests stay server-side; persisted WebUI requests are redacted | `test_webui.py`, `test_webui_ux.py` |
| Web command fails | Input is restored, but response loss is reported as unknown delivery and tells the operator to inspect state before retrying | `test_webui_ux.py` |
| Web stop is ambiguous | The UI says stop delivery is unconfirmed and directs the operator to the physical emergency stop | `test_webui_ux.py` |
| Website discovery | Search and mobile navigation respond to real keyboard events in Firefox | `ui_browser_acceptance.py`, browser acceptance manifest |

## Verification

- Python format, lint, strict type check: pass.
- Full Python suite: pass.
- Website TypeScript check and production build: pass.
- Website rendered-output and stylesheet regressions: pass.
- Generated WebUI JavaScript syntax check: pass.
- Firefox WebDriver keyboard and measured 500 × 758 inner-viewport responsive acceptance: pass.
- Targeted WebUI → Isaac STOP engineering acceptance on clean revision
  `c66d163aefcd6f7644221b8fd99643132bc314af`: pass. An exact, digest-bound route approval
  entered `running`; the same Nav2 goal UUID changed from `executing` (2) to
  `canceled` (5) after WebUI STOP. The action finished `interrupted`/`cancelled`,
  no late success was observed, and the separate STOP run recorded a published zero-velocity command.
- This is simulation action-lifecycle evidence only. The STOP receipt correctly did not claim
  independently observed motion stop; a later odometry sample was near zero, while the post-stop TF
  window was unavailable because of transient extrapolation. No physical NXDog motion or physical
  safety result is claimed.
- The raw machine-readable result remains local at
  `$XDG_CONFIG_HOME/jenai/reports/acceptance/webui-isaac-stop-20260730.json`; SHA-256
  `164c85eda8ab9e4faf78d9ee0b58a1dbbbeba5a8c54810d81eb1bc4bc671410b`. ROS domain,
  full USD identity, and map identity were not captured in that run and remain `not recorded`;
  this is therefore engineering acceptance evidence, not formal HIL evidence.
