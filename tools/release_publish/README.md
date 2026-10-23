# `release_publish/`

Provider-agnostic release control plane.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Provider-agnostic release control plane.
- [`__main__.py`](__main__.py) — Run the release and publishing control plane.
- [`artifacts.py`](artifacts.py) — Artifact post-processing commands.
- [`cli.py`](cli.py) — Command-line interface.
- [`config.py`](config.py) — TOML configuration loading and validation.
- [`engine.py`](engine.py) — Release execution engine with dry-run and journaling.
- [`git.py`](git.py) — Git repository adapter.
- [`models.py`](models.py) — Immutable domain models.
- [`planner.py`](planner.py) — Release planning and dependency ordering.
- [`providers.py`](providers.py) — Repository and package-registry adapters.
- [`runner.py`](runner.py) — Deterministic subprocess execution.
- [`upload.py`](upload.py) — Safe package-registry upload helper.
- [`versioning.py`](versioning.py) — Strict semantic-version operations.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
