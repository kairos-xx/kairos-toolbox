# Lake Terminal Integration Qualification

This harness validates the part of Lake Terminal most likely to fail in real
agentic development: **execution attribution under concurrent providers and
surfaces**.

## Automated synthetic qualification

```bash
lake --test-integration synthetic all
```

This runs deterministic probes for human, Claude, Codex, Cursor, and Devin
identities. Each probe creates child processes, emits stdout markers, and opens
a loopback listener so process-tree and port observation can be exercised.

Run one provider:

```bash
lake --test-integration synthetic codex
```

## Real provider qualification on macOS

```bash
lake --test-integration real codex
lake --test-integration real claude
lake --test-integration real cursor
lake --test-integration real devin
```

Lake prints an exact probe command. Give that command to the selected real
agent. The harness waits for evidence and verifies the provider identity that
the existing Lake provider integration propagated. It does not assume the
prompted provider is the observed provider.

Run real tests separately for distinct surfaces where available, e.g. Claude
Desktop vs Claude in VS Code, or Codex Desktop vs its editor/CLI surface.

## Linux qualification with Docker

```bash
tools/lake_terminal/integration/run_docker.sh
```

The image installs only Linux runtime dependencies needed by Lake (Python,
tmux, procps, iproute2, lsof, shells, curl, git). It intentionally does not
install graphical editors.

## Docker Sandboxes

When `sbx` is installed, `run_sandboxes.sh` prepares targets for Claude Code,
Codex, Cursor, and Devin. Current Docker Sandboxes provide built-in kits for
those agents, so Linux agent qualification can grow without GUI automation.

## Required real-world scenarios

Before a release is marked provider-qualified, run:

1. human + Codex in one workspace;
2. Codex + Claude in one workspace;
3. Codex + Claude + Cursor + Devin concurrently;
4. two independent sessions of the same provider;
5. the same provider through two surfaces when available;
6. an agent-started server that outlives the initiating agent;
7. nested child/grandchild processes;
8. Lake daemon restart while probes are alive;
9. editor restart while a detached execution remains alive;
10. conflicting attribution evidence (must resolve to `unknown`);
11. missing provider metadata (must remain `unknown`);
12. PID reuse regression tests.

## Attribution policy

Evidence precedence is:

1. Lake execution token;
2. native provider hook;
3. explicit environment metadata;
4. editor metadata;
5. process ancestry;
6. heuristics;
7. unknown.

A weaker source never overrides a stronger source. Equally strong conflicting
providers resolve to `unknown` rather than guessing.

Use:

```bash
lake --explain SESSION_ID
```

to inspect the persisted provider, provider session, attribution source,
surface, editor, workspace, process identity, and confidence classification.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`run_docker.sh`](run_docker.sh) — Provide run docker sh content.
- [`run_sandboxes.sh`](run_sandboxes.sh) — Provide run sandboxes sh content.

### Subdirectories

- [`docker/`](docker/README.md) — Provide Dockerfile project content.
- [`providers/`](providers/README.md) — No direct files besides this README.
- [`scenarios/`](scenarios/README.md) — No direct files besides this README.

<!-- exactus:inventory:end -->
