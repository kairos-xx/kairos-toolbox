# `js/`

ANSI terminal emulation shared by the dashboard and the full-screen console.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`ansi.js`](ansi.js) — ANSI terminal emulation shared by the dashboard and the full-screen console.
- [`api.js`](api.js) — A small client for the dashboard's API. Every failure becomes an {
- [`app.js`](app.js) — Live District Terminal dashboard with a stateful ANSI terminal emulator and observability widgets.
- [`console.js`](console.js) — Provide console js content.
- [`filters.js`](filters.js) — The dashboard's scope: one set of filters shared by the stats, the sessions, the activity feed and the command history, and kept in the URL hash so that every agent, session id and stat can be a plain link to a filtered view.
- [`forms.js`](forms.js) — Builds an input form from a command's parameter specification, so the palette and the URI screen can offer every district command without a hand-made form for each one.
- [`host.js`](host.js) — The overview's picture of the machine a district runs on: processor, memory and disk as gauges, the busiest processes, the network and the state of the district itself. It replaces a strip of totals that the lists below already repeated.
- [`kit.js`](kit.js) — Small shared helpers for the dashboard's newer screens: DOM builders and the formatters that turn counts, sizes and moments into short text.
- [`palette.js`](palette.js) — The command palette: every district command, grouped by what it is for, each one a tap away. A command with nothing to fill in runs at once; the others ask for their input in a sheet. Results appear as widgets, not JSON.
- [`route.js`](route.js) — Where the dashboard is: one of its screens, the agent or session it is about, and the filters in force. All of it lives in the URL hash, so every screen is a plain link that can be shared, bookmarked and returned to.
- [`sheet.js`](sheet.js) — A layered sheet: the current screen stays visible behind it, it holds one focused sub-task (a command's form), and closing it returns to where the person was rather than navigating away.
- [`uris.js`](uris.js) — The URI screen. A district URI is a signed, expiring, single-use ticket that runs one district command with fixed input, so a tap, a scan or a pasted link can do exactly one thing. Here a person issues them, sees which still work, copies, runs or revokes them, and redeems one that was sent to them.
- [`widgets.js`](widgets.js) — Turns the typed widgets a district command returns (metrics, facts, tables, checks, console output, notices) into DOM, so every command shows a real interface instead of JSON. Districts and agents in a result link to their screens.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
