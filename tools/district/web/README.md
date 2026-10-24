# Web dashboard assets

Browser assets for the local FastAPI/WebSocket District Terminal control-plane dashboard.

<!-- district:inventory:start -->
## Directory contents
- `index.html` — Accessible dashboard document.
- `console.html` — Full-screen console document.
- `js/` — Browser modules for the dashboard and console.
- `css/` — Hierarchical-token dashboard presentation.
<!-- district:inventory:end -->

Tests live in `tests/tools/district/js/` (`node --test "tests/tools/district/js/*.test.mjs"`).

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`console.html`](console.html) — Provide console html content.
- [`index.html`](index.html) — Provide index html content.
- [`package.json`](package.json) — Provide package json content.

### Subdirectories

- [`css/`](css/README.md) — Provide console css content.
- [`js/`](js/README.md) — ANSI terminal emulation shared by the dashboard and the full-screen console.

<!-- exactus:inventory:end -->
