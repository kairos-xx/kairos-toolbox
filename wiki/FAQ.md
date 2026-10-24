# FAQ

### Is District Terminal a terminal multiplexer?

No. It *uses* tmux/ConPTY as backends and adds the control plane they lack:
queryable SQLite state, agent attribution, durable command lifecycle, event
subscriptions, privacy controls, and machine-readable APIs for editors and
automation. See [District Terminal](District-Terminal).

### Does District Terminal store state in JSON files?

No — by design rule. `~/.district/district.db` is the only persistent
control-plane state. JSON is for configuration and explicit output
(`--status --json`) only. See [Data Model](District-Terminal-Data-Model).

### What happens when a database is corrupt?

It's set aside (renamed `.corrupt-<timestamp>`) and rebuilt. A newer
schema is left untouched. `district --doctor --full` reports integrity. See
[Data Model](District-Terminal-Data-Model#recovery-semantics).

### Is `scheme://` handling really that dangerous?

Any local process can invoke a registered URI handler — so the handler is
an input boundary whether you treat it like one or not. [Bowman](Bowman)
formalizes provenance, auth, validity windows, replay protection, and
native-OS elevation.

### Why `.pyz` instead of PyInstaller for updates?

`.pyz` archives are small, standard `zipapp` output, nameable per platform,
and updatable in place — the format `district --update` downloads. Native
installers (`.pkg`/`.deb`/`.exe`) still exist for machines without Python.
See [Stationer](Stationer-Builder).

### Why plan-then-execute releases?

Because "what will this release do?" is answerable *before* anything
happens — plans are printable, dry-runnable, journaled, and resumable.
CI splits it further: tag push → build → `publish`. See
[Stationer Publish](Stationer-Publish) and [Releasing](Releasing).

### Do the plugins need the marketplace?

No. Each ships `install.py`, which discovers VS Code-compatible editors
and installs/uninstalls locally. Devin Remote Control also packages a
deterministic VSIX.

### Why so strict on lint/types/docs?

Agentic development makes code review the bottleneck; mechanical
correctness gates (ruff `ALL`, strict typing, kwarg-only calls, zero
suppressions, enforced docs) keep review load on *behavior* instead of
hygiene. See [Engineering Standards](Engineering-Standards).

### Can I use Bowman without District Terminal?

Yes — it's a standalone package under `tools/bowman/bowman/` with no
District Terminal dependency.

### Minimum Python?

3.13. Plugins target VS Code-compatible editors on macOS/Linux/Windows.
