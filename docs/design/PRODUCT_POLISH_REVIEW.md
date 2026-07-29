# JenAI Product Polish UI Review

> Review date: 2026-07-30
> Scope: first-run setup, the approved Claude Code-style TUI, the auxiliary WebUI, and the
> documentation website. This is engineering usability evidence, not a user study.

## Design contract

- The TUI remains the terminal-first primary interaction surface.
- The approved transcript layout, dachshund mascot, composer, approval hierarchy, colours,
  responsive breakpoints, and motion behaviour are unchanged.
- The WebUI remains an auxiliary monitoring and approval surface; it does not replace the TUI or
  create a second robot execution path.
- No Nav2, AMCL, arrival tolerance, endpoint recovery, Capability, or dependency changed.

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
CSS at 1440 × 1000. The GPU-backed Firefox capture path is unavailable on this headless DGX, so
these static views use WeasyPrint; JavaScript interaction and responsive behaviour remain covered
by rendered-output tests. Inspectable WebUI documents are also preserved as
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

## First useful task checklist

| Journey | Expected user experience | Evidence |
|---|---|---|
| First launch | Three short steps; an invalid field explains what to fix without discarding earlier answers | `test_setup_ux.py` |
| Setup complete | Shows the real config and locations paths, then leads to `JenAI doctor` and `JenAI` | `test_setup_ux.py` |
| TUI welcome | Leads with a natural-language task, then `/doctor` and `/help`; the approved visual layout stays intact | wide/narrow renders and `test_tui_ux_copy.py` |
| Discover a command | `/` opens the palette; keyboard navigation and descriptions use operator language | `test_tui.py`, `test_tui_command_dispatch.py` |
| Approve motion | Risk and physical effect are explicit; options remain one-time, session, or reject; `Esc` rejects | `test_tui_ux_copy.py`, approval-policy tests |
| Web command fails | The user’s input is restored and the connection is shown as interrupted instead of implying success | `test_webui_ux.py` |
| Web stop is ambiguous | The UI says stop delivery is unconfirmed and directs the operator to the physical emergency stop | `test_webui_ux.py` |
| Website discovery | Search supports Arrow keys, Enter, and Escape; mobile navigation reports expanded state | website rendered-output tests |

## Verification

- Python format, lint, strict type check: pass.
- Full Python suite: pass.
- Website TypeScript check and production build: pass.
- Website rendered-output and stylesheet regressions: pass.
- Generated WebUI JavaScript syntax check: pass.
- Live Isaac Sim and physical NXDog motion were not run because this change does not alter a
  motion path. No simulation or physical result is claimed.
